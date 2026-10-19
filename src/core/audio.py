"""Audio demo playback triggered by CAN requests and published through SignalStore."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import sys
import time
from collections import deque
from enum import IntEnum
from pathlib import Path
from typing import Protocol

from src.core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

# INC_HMI_Audio_Request (read from Panther): 0 = no sound, i = play audio_number i.
AUDIO_REQUEST_SIGNAL = "HMI_Soundfile"
# INC_HMI_Audio_Response (written by CAR_PC as one batch frame).
AUDIO_TIMESTAMP_SIGNAL = "HMI_AudioTimesamp"
AUDIO_ACTIVE_SOUNDFILE_SIGNAL = "HMI_ActiveSoundfile"
AUDIO_TIMESTAMP_IDLE = -1
# Idle responses are sent at least twice so a single lost frame cannot leave
# the Panther side showing a stale position.
AUDIO_IDLE_RESPONSE_REPEATS = 2
# HMI_AudioTimesamp is a 24-bit signed DBC signal (normalize_signal_name drops the _ms suffix).
AUDIO_TIMESTAMP_MAX_MS = 8_388_607
AUDIO_QUEUE_MAX_LENGTH = 64


class AudioStatus(IntEnum):
    IDLE = 0
    PLAYING = 1
    COMPLETED = 4
    ERROR = 5


class AudioControlError(RuntimeError):
    """Base class for audio request failures."""


class AudioDisabledError(AudioControlError):
    """Raised when playback is disabled by system configuration."""


class AudioTransitionError(AudioControlError):
    """Raised when a requested audio number is not valid."""


class AudioPlaybackError(AudioControlError):
    """Raised when the configured track or player cannot be used."""


class AudioBackend(Protocol):
    """Minimal player contract; tests provide a deterministic fake."""

    async def play(self, path: Path) -> int: ...

    async def stop(self) -> None: ...

    async def wait(self) -> int: ...


class FFplayAudioBackend:
    """Local process player: ffplay first, then native afplay on macOS."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None

    @staticmethod
    def _parse_afinfo_duration_ms(output: str) -> int:
        match = re.search(
            r"^\s*estimated duration:\s*([0-9.eE+-]+)\s*sec\s*$",
            output,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if match is None:
            raise AudioPlaybackError("Unable to determine audio duration from afinfo")
        try:
            duration_seconds = float(match.group(1))
        except ValueError as exc:
            raise AudioPlaybackError("Unable to determine audio duration from afinfo") from exc
        return max(0, round(duration_seconds * 1000))

    @staticmethod
    def _player_command(path: Path) -> list[str]:
        ffplay = shutil.which("ffplay")
        if ffplay is not None:
            return [
                ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                str(path),
            ]
        if sys.platform == "darwin":
            afplay = shutil.which("afplay")
            if afplay is not None:
                return [afplay, str(path)]
        raise AudioPlaybackError(
            "No supported audio player is available; install ffmpeg (ffplay)"
        )

    @classmethod
    async def _probe_duration_ms(cls, path: Path) -> int:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is not None:
            command = [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ]
        elif sys.platform == "darwin" and (afinfo := shutil.which("afinfo")) is not None:
            command = [afinfo, str(path)]
        else:
            raise AudioPlaybackError(
                "No supported audio duration probe is available; install ffmpeg (ffprobe)"
            )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            tool_name = Path(command[0]).name
            raise AudioPlaybackError(detail or f"{tool_name} exited with code {process.returncode}")
        if Path(command[0]).name == "afinfo":
            return cls._parse_afinfo_duration_ms(stdout.decode("utf-8", errors="replace"))
        try:
            duration_seconds = float(json.loads(stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AudioPlaybackError("Unable to determine audio duration") from exc
        return max(0, round(duration_seconds * 1000))

    async def play(self, path: Path) -> int:
        await self.stop()
        duration_ms = await self._probe_duration_ms(path)
        command = self._player_command(path)
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Detect immediate failures such as a missing/locked audio device before
        # announcing PLAYING to API and CAN consumers.
        await asyncio.sleep(0.1)
        if self._process.returncode is not None:
            code = self._process.returncode
            self._process = None
            player_name = Path(command[0]).name
            raise AudioPlaybackError(
                f"{player_name} exited during startup with code {code}"
            )
        return duration_ms

    async def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def wait(self) -> int:
        process = self._process
        if process is None:
            return 0
        return await process.wait()


class AudioManager:
    """Single audio state machine driven by the CAN request signal."""

    def __init__(
        self,
        config,
        signal_store,
        can_writer=None,
        backend: AudioBackend | None = None,
    ) -> None:
        self._config = config
        self._store = signal_store
        self._writer = can_writer
        self._backend: AudioBackend = backend or FFplayAudioBackend()
        self._lock = asyncio.Lock()
        self._status = AudioStatus.IDLE
        self._position_ms = 0
        self._duration_ms = 0
        self._last_logged_publish_error: str | None = None
        self._play_started_monotonic = 0.0
        self._generation = 0
        self._position_task: asyncio.Task[None] | None = None
        self._position_update_interval_ms = int(config.position_update_interval_ms)
        self._track_queue_enable = bool(config.track_queue_enable)
        self._completion_task: asyncio.Task[None] | None = None
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._last_request_value = 0
        self._queue: deque[int] = deque()
        self._tracks: dict[int, str] = dict(config.configured_tracks())
        self._active_track_index: int | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._store.subscribe(self._on_signal_update)
        async with self._lock:
            await self._publish_locked()

    async def apply_runtime_config(
        self,
        *,
        position_update_interval_ms: int | None = None,
        track_queue_enable: bool | None = None,
        tracks: dict[int, str] | None = None,
    ) -> None:
        """Apply live settings; the active track keeps its current playback session."""
        async with self._lock:
            if position_update_interval_ms is not None:
                self._position_update_interval_ms = int(position_update_interval_ms)
            if tracks is not None:
                self._tracks = dict(tracks)
                queued_before = len(self._queue)
                self._queue = deque(number for number in self._queue if number in self._tracks)
                dropped = queued_before - len(self._queue)
                if dropped:
                    logger.warning(
                        "Dropped %d queued audio request(s) removed from playlist", dropped
                    )
            if track_queue_enable is not None:
                self._track_queue_enable = bool(track_queue_enable)

    async def close(self) -> None:
        if not self._started:
            return
        self._started = False
        with contextlib.suppress(ValueError):
            self._store.unsubscribe(self._on_signal_update)
        async with self._lock:
            self._generation += 1
            self._queue.clear()
            self._cancel_runtime_tasks_locked()
            await self._backend.stop()
            if self._status == AudioStatus.PLAYING:
                self._status = AudioStatus.IDLE
                self._position_ms = 0
                await self._publish_locked()
        for task in tuple(self._command_tasks):
            task.cancel()
        if self._command_tasks:
            await asyncio.gather(*self._command_tasks, return_exceptions=True)

    def _on_signal_update(self, name: str, signal_value) -> None:
        if not self._started or name != AUDIO_REQUEST_SIGNAL:
            return
        try:
            requested = int(float(signal_value.value))
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "Ignoring invalid %s value: %r", AUDIO_REQUEST_SIGNAL, signal_value.value
            )
            return

        previous = self._last_request_value
        self._last_request_value = requested
        # 0 means "no sound". A repeated value only counts as a new request after
        # Panther has sent 0 in between.
        if requested == 0 or requested == previous:
            return
        task = asyncio.create_task(
            self._handle_can_request(requested), name=f"audio-can-request-{requested}"
        )
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)

    async def _handle_can_request(self, audio_number: int) -> None:
        try:
            async with self._lock:
                if not self._config.enabled:
                    raise AudioDisabledError("Audio playback is disabled in system configuration")
                if audio_number not in self._tracks:
                    raise AudioTransitionError(f"Audio number {audio_number} is not configured")
                if self._track_queue_enable and self._is_busy_locked():
                    if len(self._queue) >= AUDIO_QUEUE_MAX_LENGTH:
                        logger.warning(
                            "Audio queue is full (%d); dropping audio number %d",
                            AUDIO_QUEUE_MAX_LENGTH,
                            audio_number,
                        )
                        return
                    self._queue.append(audio_number)
                    return
                self._queue.clear()
                await self._play_locked(audio_number)
        except AudioControlError as exc:
            logger.warning("CAN audio request %d rejected: %s", audio_number, exc)
        except Exception:
            logger.exception("CAN audio request %d failed", audio_number)

    async def _play_locked(self, audio_number: int) -> None:
        try:
            track_path = self._resolve_track(audio_number)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._backend.stop()
            await self._set_error_locked(exc)
            raise AudioPlaybackError(str(exc)) from exc

        self._generation += 1
        generation = self._generation
        self._cancel_runtime_tasks_locked()
        with contextlib.suppress(Exception):
            await self._backend.stop()
        try:
            duration_ms = await self._backend.play(track_path)
        except Exception as exc:
            await self._set_error_locked(exc)
            raise AudioPlaybackError(str(exc)) from exc

        self._status = AudioStatus.PLAYING
        self._position_ms = 0
        self._duration_ms = duration_ms
        self._active_track_index = audio_number
        self._play_started_monotonic = time.monotonic()
        await self._publish_locked()
        self._position_task = asyncio.create_task(
            self._position_loop(generation), name="audio-position"
        )
        self._completion_task = asyncio.create_task(
            self._completion_loop(generation), name="audio-completion"
        )

    async def _set_error_locked(self, error: Exception) -> None:
        self._queue.clear()
        self._generation += 1
        self._cancel_runtime_tasks_locked()
        self._status = AudioStatus.ERROR
        self._position_ms = 0
        self._duration_ms = 0
        logger.warning("Audio playback error: %s", error or error.__class__.__name__)
        await self._publish_locked()

    def _resolve_track(self, audio_number: int) -> Path:
        track_name = self._tracks.get(audio_number)
        if track_name is None:
            raise AudioPlaybackError(f"Audio number {audio_number} is not configured")
        media_dir = Path(self._config.media_dir)
        if not media_dir.is_absolute():
            media_dir = PROJECT_ROOT / media_dir
        media_dir = media_dir.resolve()
        track = (media_dir / track_name).resolve()
        try:
            track.relative_to(media_dir)
        except ValueError as exc:
            raise AudioPlaybackError("Configured audio file escapes audio.media_dir") from exc
        suffix = track.suffix.lower()
        if suffix not in self._config.allowed_extensions:
            raise AudioPlaybackError(f"Audio format '{suffix or '<none>'}' is not allowed")
        if not track.is_file():
            raise AudioPlaybackError(f"Audio file not found: {track}")
        return track

    def _refresh_position_locked(self) -> None:
        if self._status != AudioStatus.PLAYING:
            return
        elapsed_ms = round((time.monotonic() - self._play_started_monotonic) * 1000)
        self._position_ms = min(self._duration_ms, self._position_ms + max(0, elapsed_ms))
        self._play_started_monotonic = time.monotonic()

    async def _position_loop(self, generation: int) -> None:
        interval = max(0.05, self._position_update_interval_ms / 1000.0)
        try:
            while True:
                await asyncio.sleep(interval)
                async with self._lock:
                    if generation != self._generation:
                        return
                    if self._status != AudioStatus.PLAYING:
                        continue
                    self._refresh_position_locked()
                    await self._publish_locked()
        except asyncio.CancelledError:
            return

    async def _completion_loop(self, generation: int) -> None:
        try:
            return_code = await self._backend.wait()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            async with self._lock:
                if generation == self._generation:
                    await self._set_error_locked(exc)
            return

        async with self._lock:
            if generation != self._generation:
                return
            if return_code == 0:
                self._cancel_runtime_tasks_locked()
                if self._queue:
                    next_audio_number = self._queue.popleft()
                    try:
                        await self._play_locked(next_audio_number)
                    except AudioControlError as exc:
                        logger.warning(
                            "Queued audio number %d rejected: %s", next_audio_number, exc
                        )
                    return
                self._status = AudioStatus.COMPLETED
                self._position_ms = self._duration_ms
                await self._publish_locked()
            else:
                await self._set_error_locked(
                    AudioPlaybackError(f"Audio player exited with code {return_code}"),
                )

    def _cancel_runtime_tasks_locked(self) -> None:
        current = asyncio.current_task()
        for task in (self._position_task, self._completion_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        self._position_task = None
        self._completion_task = None

    async def _publish_locked(self) -> None:
        updates = {
            AUDIO_TIMESTAMP_SIGNAL: float(self._timestamp_ms_locked()),
            AUDIO_ACTIVE_SOUNDFILE_SIGNAL: float(self._active_audio_number_locked()),
        }
        # SignalStore is the realtime API/WS source of truth even when CAN is
        # unavailable. The normal writer path may publish the same values again;
        # the runner's short WS coalescing window collapses those duplicates.
        await self._store.bulk_update(updates, timestamp=time.time())

        if self._writer is None:
            self._log_publish_result("CAN writer not available")
            return
        repeats = 1 if self._is_busy_locked() else AUDIO_IDLE_RESPONSE_REPEATS
        error: str | None = None
        for _ in range(repeats):
            error = await self._send_response_once(updates)
            if error is not None:
                break
        self._log_publish_result(error)

    async def _send_response_once(self, updates: dict[str, float]) -> str | None:
        try:
            send_once = getattr(
                self._writer,
                "send_signals_batch_once",
                self._writer.send_signals_batch,
            )
            result = await send_once(updates)
            if isinstance(result, tuple):
                sent, errors = result
            else:
                sent, errors = result, []
        except Exception as exc:
            return str(exc) or exc.__class__.__name__
        details = [str(item.get("error", item)) for item in errors]
        missing = set(updates) - set(sent)
        if missing:
            details.append(f"not sent: {', '.join(sorted(missing))}")
        return "; ".join(details) or None

    def _log_publish_result(self, error: str | None) -> None:
        if error is None:
            if self._last_logged_publish_error is not None:
                logger.info("Audio CAN response publishing restored")
            self._last_logged_publish_error = None
            return
        if error != self._last_logged_publish_error:
            logger.warning("Audio CAN response publishing unavailable: %s", error)
            self._last_logged_publish_error = error
        else:
            logger.debug("Audio CAN response publishing still unavailable: %s", error)

    def _is_busy_locked(self) -> bool:
        return self._status == AudioStatus.PLAYING

    def _timestamp_ms_locked(self) -> int:
        if not self._is_busy_locked():
            return AUDIO_TIMESTAMP_IDLE
        return min(self._position_ms, AUDIO_TIMESTAMP_MAX_MS)

    def _active_audio_number_locked(self) -> int:
        if self._is_busy_locked() and self._active_track_index is not None:
            return self._active_track_index
        return 0
