# Demo audio

Place configured demo tracks in this directory. Each track is mapped to an `audio_number`
(1-255), which is the value sent in CAN `HMI_Soundfile`. Audio is triggered only from CAN:

```json
{
  "audio": {
    "files": [
      {"name": "test.mp3", "audio_number": 1},
      {"name": "x.wav", "audio_number": 2}
    ],
    "track_queue_enable": false
  }
}
```

`audio_number` values must be unique. Supported extensions are set by
`audio.allowed_extensions`, which is fixed at startup; the default allowlist is `.mp3`, `.wav`,
and `.ogg`.

Playback uses `ffplay` and `ffprobe` on Linux. On macOS it automatically falls back to the
built-in `afplay` and `afinfo` commands when FFmpeg is not installed.
