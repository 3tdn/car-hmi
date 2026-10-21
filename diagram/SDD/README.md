# Software Design Description

This directory contains implementation-level sequence diagrams. The audio design uses
the shared CAN Reader → Pipeline → SignalStore path and one `AudioManager` for local
playback, request queueing, and CAN feedback.

## Diagram index

| Diagram | Scope |
|---|---|
| [Audio playback](SDD_sequence_audio_demo.puml) | Request callback, player lifecycle, periodic feedback, interruption, queueing, and completion |
| [Startup](SDD_sequence_boot_startup.puml) | DBC/store/pipeline/CAN initialization and `AudioManager.start()` before API startup |
| [Shutdown](SDD_sequence_boot_shutdown.puml) | `AudioManager.close()`, player/task cleanup, idle feedback, and CAN resource cleanup |
| [Signal read](SDD_sequence_signal_read.puml) | Shared receive pipeline |
| [Signal write](SDD_sequence_signal_write.puml) | Signal write path |
| [Restraints](SDD_sequence_restraints.puml) | Video matching and file serving |

See the [Software Architecture Description](../SAD/README.md) for the three-participant
Web HMI → CAR-PC → PANTHER flow and the external signal contract.

## Audio components

| Component | Design responsibility |
|---|---|
| [AppRunner](../../src/core/runner.py) | Construct and start `AudioManager` with the shared store and writer router; apply live audio settings; close audio before CAN shutdown. |
| [CANReader](../../src/can_io/reader.py) and [SignalPipeline](../../src/processor/pipeline.py) | Decode/rate-gate/coalesce incoming frames, drain the shared queue, and publish `HMI_Soundfile` to the store. |
| [SignalStore](../../src/core/signal_store.py) | Notify the audio request callback and existing WebSocket subscribers. |
| [AudioManager](../../src/core/audio.py) | Serialize request handling with an `asyncio.Lock`; map audio numbers, manage the player and FIFO queue, and publish feedback. |
| `FFplayAudioBackend` in [audio.py](../../src/core/audio.py) | Resolve duration, launch `ffplay` or macOS `afplay`, wait for player completion, and terminate the player on replacement or shutdown. |
| [CANWriterRouter](../../src/can_io/writer.py) | Route both feedback values to the writer for `INC_HMI_Audio_Response` and send them in a single frame. |

## Request handling

`start()` subscribes `_on_signal_update()` to SignalStore and publishes idle feedback.
The callback only handles `HMI_Soundfile`; it records the latest request before
scheduling `_handle_can_request()`.

- `0` re-arms the request without stopping playback or clearing the queue. An unchanged
  non-zero value is ignored. Replaying the same number requires an observed `0 → n` edge.
- Disabled audio and numbers absent from `audio.files` are rejected without starting a
  track. The mapping uses `audio_number`, not the entry's array index.
- For type 1 (`audio.track_queue_enable=false`), a valid request clears pending entries
  and `_play_locked()` stops the current player before starting the selected file.
- For type 2 (`true`), a valid request received during playback is appended to a FIFO
  `deque`. The queue holds 64 pending entries; further requests are dropped with a warning.
- Queueing leaves the requested `HMI_Soundfile` visible immediately. The active audio
  number and position continue describing the current player until the next track starts.

`_play_locked()` validates that the configured file exists beneath `audio.media_dir`
and has an allowed extension, cancels previous playback tasks, stops the old backend,
and starts the new one. On success it publishes position `0` and the active audio number,
then starts `_position_loop()` and `_completion_loop()` for that playback generation.
Generation checks prevent an old playback task from overwriting the new track's state.

## Feedback and completion

`_position_loop()` captures T = `audio.position_update_interval_ms` when the playback
begins, updates elapsed position from a monotonic clock, and calls `_publish_locked()`
at that target cadence while playing. The timestamp is capped to the signed 24-bit DBC
maximum of `8388607` ms. The application name `HMI_AudioTimesamp` is normalized from the
raw DBC name `HMI_AudioTimesamp_ms`.

`_publish_locked()` first updates `HMI_AudioTimesamp` and `HMI_ActiveSoundfile` together
in SignalStore, then uses `send_signals_batch_once()` to send the pair in one CAN frame.
This avoids starting a separate retransmission job under `writer.periodic_mode`.
Store/WebSocket feedback remains available when CAN publishing fails; the error is logged.

On successful completion, `_completion_loop()` starts the next queued number through
`_play_locked()` if a queue entry exists. It directly publishes the next track's position
`0` and active number, without an intermediate idle frame. If the queue is empty, the
status becomes `COMPLETED` and feedback becomes position `-1`, active number `0`, sent
twice. The same idle values are published at startup and on a file/player error. A
playback error clears the queue; idle is not a continuous T-ms transmission loop.

## Configuration and lifecycle

`AppRunner.apply_system_config()` forwards playlist, queue-mode, and interval changes to
`AudioManager.apply_runtime_config()`. Playlist and queue-mode changes apply to new
requests. The current track keeps its file and T; the next playback uses the new T.
Queued numbers use the latest mapping, and removed numbers are removed from the queue.
Changing `audio.enabled` or `audio.media_dir` requires a service restart.

`close()` unsubscribes the callback, invalidates the playback generation, clears the
queue, cancels playback tasks, stops the backend, publishes idle if it was playing, and
cancels/awaits pending command tasks. The runner calls it before stopping readers and
closing CAN buses so the final feedback can still be sent.

## Verification

[Audio unit tests](../../tests/1_unit_functions/test_audio.py) cover request edges,
interruption, queueing, feedback, completion, player selection, and configuration errors.
The [manual demo procedure](../../docs/audio_demo.md#demo-test-with-virtual-can) exercises
the receive pipeline with `virtual` CAN, the simulator disabled, and the stale threshold
set to `0`. `HMI_Soundfile` is RX-only for CAR-PC; its test sender must share the same
process as the python-can virtual bus. Standard REST signal writes cannot inject it.
