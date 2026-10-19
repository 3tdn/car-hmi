from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.audio import (
    AUDIO_ACTIVE_SOUNDFILE_SIGNAL,
    AUDIO_REQUEST_SIGNAL,
    AUDIO_TIMESTAMP_SIGNAL,
    AudioManager,
    FFplayAudioBackend,
)
from src.core.config import AudioConfig
from src.core.signal_store import SignalStore

IDLE_FRAME = {AUDIO_TIMESTAMP_SIGNAL: -1.0, AUDIO_ACTIVE_SOUNDFILE_SIGNAL: 0.0}


async def _wait_for(condition: Callable[[], bool], timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


class FakeAudioBackend:
    def __init__(self, duration_ms: int = 2_000) -> None:
        self.duration_ms = duration_ms
        self.play_count = 0
        self.stop_count = 0
        self.play_error: Exception | None = None
        self.played_paths: list[Path] = []
        self._completion: asyncio.Future[int] | None = None

    async def play(self, path: Path) -> int:
        assert path.is_file()
        if self.play_error is not None:
            raise self.play_error
        self.play_count += 1
        self.played_paths.append(path)
        self._completion = asyncio.get_running_loop().create_future()
        return self.duration_ms

    async def stop(self) -> None:
        self.stop_count += 1

    async def wait(self) -> int:
        assert self._completion is not None
        return await self._completion

    def finish(self, return_code: int = 0) -> None:
        assert self._completion is not None
        self._completion.set_result(return_code)


class BlockingPlayBackend(FakeAudioBackend):
    def __init__(self) -> None:
        super().__init__()
        self.play_started = asyncio.Event()
        self.release_play = asyncio.Event()

    async def play(self, path: Path) -> int:
        self.play_started.set()
        await self.release_play.wait()
        return await super().play(path)


class FakeCANWriter:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.writes: list[dict[str, float]] = []

    async def send_signals_batch(self, values: dict[str, float]):
        self.writes.append(dict(values))
        if self.available:
            return dict(values), []
        return {}, [{"signal_name": name, "error": "CAN unavailable"} for name in values]

    async def send_signals_batch_once(self, values: dict[str, float]):
        return await self.send_signals_batch(values)


def audio_config(tmp_path: Path, **overrides) -> AudioConfig:
    media_dir = tmp_path / "audio"
    media_dir.mkdir()
    (media_dir / "x.wav").write_bytes(b"fake test audio")
    (media_dir / "y.mp3").write_bytes(b"fake second audio")
    return AudioConfig(
        media_dir=str(media_dir),
        files=[
            {"name": "x.wav", "audio_number": 1},
            {"name": "y.mp3", "audio_number": 2},
        ],
        position_update_interval_ms=50,
        **overrides,
    )


def test_audio_backend_prefers_ffplay(monkeypatch, tmp_path):
    track = tmp_path / "x.wav"
    monkeypatch.setattr(
        "src.core.audio.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )

    assert FFplayAudioBackend._player_command(track) == [
        "/usr/bin/ffplay",
        "-nodisp",
        "-autoexit",
        "-loglevel",
        "error",
        str(track),
    ]


def test_audio_backend_falls_back_to_macos_native_tools(monkeypatch, tmp_path):
    track = tmp_path / "x.wav"
    tools = {"afplay": "/usr/bin/afplay", "afinfo": "/usr/bin/afinfo"}
    monkeypatch.setattr("src.core.audio.sys.platform", "darwin")
    monkeypatch.setattr("src.core.audio.shutil.which", tools.get)

    assert FFplayAudioBackend._player_command(track) == ["/usr/bin/afplay", str(track)]
    assert (
        FFplayAudioBackend._parse_afinfo_duration_ms(
            "File: x.wav\nestimated duration: 2.500000 sec\n"
        )
        == 2500
    )


@pytest.mark.asyncio
async def test_start_publishes_idle_frame_twice(tmp_path):
    store = SignalStore()
    writer = FakeCANWriter()
    manager = AudioManager(audio_config(tmp_path), store, writer, FakeAudioBackend())

    await manager.start()

    assert writer.writes == [IDLE_FRAME, IDLE_FRAME]
    assert (await store.get(AUDIO_TIMESTAMP_SIGNAL)).value == -1.0
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 0.0
    await manager.close()


@pytest.mark.asyncio
async def test_can_request_plays_track_and_publishes_batch_frame(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    writer = FakeCANWriter()
    manager = AudioManager(audio_config(tmp_path), store, writer, backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await _wait_for(lambda: backend.play_count == 1)

    assert backend.played_paths[-1].name == "y.mp3"
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 2.0
    assert (await store.get(AUDIO_TIMESTAMP_SIGNAL)).value >= 0.0
    assert writer.writes[-1].keys() == {AUDIO_TIMESTAMP_SIGNAL, AUDIO_ACTIVE_SOUNDFILE_SIGNAL}
    assert writer.writes[-1][AUDIO_ACTIVE_SOUNDFILE_SIGNAL] == 2.0
    await manager.close()


@pytest.mark.asyncio
async def test_can_request_is_edge_triggered_and_zero_rearms(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    manager = AudioManager(audio_config(tmp_path), store, FakeCANWriter(), backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 1)

    # Holding the same non-zero value must not restart the track.
    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await asyncio.sleep(0.05)
    assert backend.play_count == 1

    # After Panther sends 0, the same number is a new request.
    await store.update(AUDIO_REQUEST_SIGNAL, 0.0)
    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 2)
    assert [path.name for path in backend.played_paths] == ["x.wav", "x.wav"]
    await manager.close()


@pytest.mark.asyncio
async def test_new_request_stops_current_track_when_queue_disabled(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    manager = AudioManager(audio_config(tmp_path), store, FakeCANWriter(), backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 1)
    await store.update(AUDIO_REQUEST_SIGNAL, 0.0)
    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await _wait_for(lambda: backend.play_count == 2)

    assert [path.name for path in backend.played_paths] == ["x.wav", "y.mp3"]
    assert backend.stop_count >= 1
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 2.0
    await manager.close()


@pytest.mark.asyncio
async def test_new_request_is_queued_when_queue_enabled(tmp_path):
    store = SignalStore()
    backend = BlockingPlayBackend()
    config = audio_config(tmp_path, track_queue_enable=True)
    manager = AudioManager(config, store, FakeCANWriter(), backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await backend.play_started.wait()
    await store.update(AUDIO_REQUEST_SIGNAL, 0.0)
    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    backend.release_play.set()
    await _wait_for(lambda: backend.play_count == 1)

    # The second track waits in the queue while the first one is still playing.
    await asyncio.sleep(0.05)
    assert [path.name for path in backend.played_paths] == ["x.wav"]

    backend.finish()
    await _wait_for(lambda: backend.play_count == 2)
    assert [path.name for path in backend.played_paths] == ["x.wav", "y.mp3"]
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 2.0
    await manager.close()


@pytest.mark.asyncio
async def test_live_queue_change_applies_to_next_request(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    manager = AudioManager(
        audio_config(tmp_path, track_queue_enable=True), store, FakeCANWriter(), backend
    )
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 1)
    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await _wait_for(lambda: list(manager._queue) == [2])
    assert (await store.get(AUDIO_REQUEST_SIGNAL)).value == 2.0
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 1.0

    await manager.apply_runtime_config(track_queue_enable=False)
    await store.update(AUDIO_REQUEST_SIGNAL, 0.0)
    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await _wait_for(lambda: backend.play_count == 2)
    assert [path.name for path in backend.played_paths] == ["x.wav", "y.mp3"]
    assert list(manager._queue) == []
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 2.0
    await manager.close()


@pytest.mark.asyncio
async def test_live_playlist_change_keeps_current_track_and_changes_next(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    config = audio_config(tmp_path)
    (Path(config.media_dir) / "z.wav").write_bytes(b"new test audio")
    manager = AudioManager(config, store, FakeCANWriter(), backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 1)
    await manager.apply_runtime_config(tracks={1: "x.wav", 2: "z.wav"})
    assert [path.name for path in backend.played_paths] == ["x.wav"]

    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await _wait_for(lambda: backend.play_count == 2)
    assert [path.name for path in backend.played_paths] == ["x.wav", "z.wav"]

    await manager.apply_runtime_config(tracks={1: "x.wav"})
    await store.update(AUDIO_REQUEST_SIGNAL, 0.0)
    await store.update(AUDIO_REQUEST_SIGNAL, 2.0)
    await asyncio.gather(*tuple(manager._command_tasks))
    assert backend.play_count == 2
    await manager.close()


@pytest.mark.asyncio
async def test_natural_completion_publishes_idle_frame_twice(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend(duration_ms=1_234)
    writer = FakeCANWriter()
    manager = AudioManager(audio_config(tmp_path), store, writer, backend)
    await manager.start()
    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: backend.play_count == 1)

    backend.finish()
    await _wait_for(lambda: writer.writes[-2:] == [IDLE_FRAME, IDLE_FRAME])

    assert (await store.get(AUDIO_TIMESTAMP_SIGNAL)).value == -1.0
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 0.0
    await manager.close()


@pytest.mark.asyncio
async def test_missing_audio_file_sets_error_and_publishes_idle_frame(tmp_path):
    store = SignalStore()
    config = AudioConfig(
        media_dir=str(tmp_path),
        files=[{"name": "missing.mp3", "audio_number": 1}],
    )
    writer = FakeCANWriter()
    backend = FakeAudioBackend()
    manager = AudioManager(config, store, writer, backend)
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await _wait_for(lambda: writer.writes[-2:] == [IDLE_FRAME, IDLE_FRAME])

    assert backend.play_count == 0
    assert (await store.get(AUDIO_TIMESTAMP_SIGNAL)).value == -1.0
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 0.0
    await manager.close()


@pytest.mark.asyncio
async def test_unconfigured_audio_number_is_ignored(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    writer = FakeCANWriter()
    manager = AudioManager(audio_config(tmp_path), store, writer, backend)
    await manager.start()
    writes_before = len(writer.writes)

    await store.update(AUDIO_REQUEST_SIGNAL, 9.0)
    await asyncio.sleep(0.02)

    assert backend.play_count == 0
    assert len(writer.writes) == writes_before
    assert (await store.get(AUDIO_ACTIVE_SOUNDFILE_SIGNAL)).value == 0.0
    await manager.close()


@pytest.mark.asyncio
async def test_disabled_audio_ignores_can_requests(tmp_path):
    store = SignalStore()
    backend = FakeAudioBackend()
    manager = AudioManager(
        audio_config(tmp_path, enabled=False), store, FakeCANWriter(), backend
    )
    await manager.start()

    await store.update(AUDIO_REQUEST_SIGNAL, 1.0)
    await asyncio.sleep(0.02)

    assert backend.play_count == 0
    await manager.close()


@pytest.mark.asyncio
async def test_audio_config_files_and_allowed_extensions_validation(tmp_path):
    with pytest.raises(ValidationError):
        AudioConfig(
            media_dir=str(tmp_path),
            files=[
                {"name": "x.wav", "audio_number": 1},
                {"name": "y.wav", "audio_number": 1},
            ],
        )
    with pytest.raises(ValidationError):
        AudioConfig(media_dir=str(tmp_path), files=["x.wav"])

    config = AudioConfig(
        media_dir=str(tmp_path),
        files=[{"name": "x.wav", "audio_number": 7}],
    )
    assert config.configured_tracks() == {7: "x.wav"}
    assert config.track_queue_enable is False
    assert config.allowed_extensions == (".mp3", ".wav", ".ogg")


def test_audio_dbc_direction_encoding_and_enums():
    from src.can_io.parser import DatabaseLoader
    from src.can_io.writer import is_message_writable_by_local_node

    loader = DatabaseLoader()
    loader.load_dbc("db/can_db/Interface_Panther_To_CarPC_v11.dbc")

    request_message = loader.get_message_for_signal(AUDIO_REQUEST_SIGNAL)
    assert request_message is not None
    assert request_message.name == "INC_HMI_Audio_Request"
    assert "CAR_PC" not in request_message.senders

    response_message = loader.get_message_for_signal(AUDIO_TIMESTAMP_SIGNAL)
    assert response_message is not None
    assert response_message.name == "INC_HMI_Audio_Response"
    assert loader.get_message_for_signal(AUDIO_ACTIVE_SOUNDFILE_SIGNAL) is response_message
    assert is_message_writable_by_local_node(response_message) is True

    timestamp = loader.signals[AUDIO_TIMESTAMP_SIGNAL]
    assert timestamp.length == 24
    assert timestamp.is_signed is True
