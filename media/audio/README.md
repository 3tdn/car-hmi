# Demo audio

Place configured demo tracks in this directory. Each track is mapped to an `audio_number`
(1-255), which is the value sent in CAN `HMI_Soundfile`. Audio is triggered only from CAN:

For the HMI → Panther → CAR-PC flow, feedback signals, diagrams, and manual virtual-CAN
test, see the [SEAL audio demo guide](../../docs/audio_demo.md).

```json
{
  "audio": {
    "enabled": true,
    "media_dir": "media/audio",
    "files": [
      {"name": "test.mp3", "audio_number": 1},
      {"name": "x.wav", "audio_number": 2}
    ],
    "track_queue_enable": false,
    "position_update_interval_ms": 100
  }
}
```

Array **A** is `audio.files`; `HMI_Soundfile` selects the entry's `audio_number`, not its
array position. Type 1 (`track_queue_enable: false`) interrupts the current track for a new
request; type 2 (`true`) queues the request and keeps the current track playing. CAR-PC
sends `HMI_AudioTimesamp` (elapsed ms) and `HMI_ActiveSoundfile` (actual audio number)
together every **T** = `audio.position_update_interval_ms` while playing. When playback
ends with no queued track, it sends `-1` and `0` twice.

`audio_number` values and file names must be unique. Supported extensions are set by
`audio.allowed_extensions`, which is fixed at startup; the default allowlist is `.mp3`, `.wav`,
and `.ogg`.

Playback uses `ffplay` and `ffprobe` on Linux. On macOS it automatically falls back to the
built-in `afplay` and `afinfo` commands when FFmpeg is not installed.
