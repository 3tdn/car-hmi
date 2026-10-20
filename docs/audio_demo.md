# SEAL audio demo

The HMI requests a SEAL action from Panther. Panther performs the demo and selects the
sound; CAR-PC plays that sound locally and reports its playback position over CAN. The
audio request and feedback use the existing signal pipeline and SignalStore/WebSocket.

See the [sequence diagram](../diagram/21_sequence_audio_demo.puml) for the HMI → Panther →
CAR-PC exchange and the [playback state diagram](../diagram/22_state_audio_playback.puml)
for interruption, queueing, and completion.

The [Software Architecture Description](../diagram/SAD/README.md) includes a simple
English diagram of Web HMI, CAR-PC, and PANTHER and the signal exchange table. The
[Software Design Description](../diagram/SDD/README.md) covers internal request handling,
queueing, feedback publishing, live configuration, and audio startup/shutdown.

## HMI and Panther flow

The HMI writes `HMI_SEAL_ActivationRequest` and `SEAL_WithSound` together in
`INC_HMI_SEALRequest` (CAN ID `0x081`, CAR-PC → Panther):

| HMI_SEAL_ActivationRequest | SEAL_WithSound | Requested demo |
|---|---|---|
| `1` | `1` | Blow in with sound |
| `2` | `1` | Blow out with sound |
| `1` | `0` | Blow in without sound |
| `2` | `0` | Blow out without sound |
| `0` | Either | No activation request |

For example, the HMI can use the standard batch write route to request Blow in with sound:

```http
POST /signals/batch_update
Content-Type: application/json
X-API-Key: YOUR_CONFIGURED_API_KEY
X-Profile-Name: admin
X-Client-Id: seal-demo-01

{
  "signals": [
    {"signal_name": "HMI_SEAL_ActivationRequest", "value": 1},
    {"signal_name": "SEAL_WithSound", "value": 1}
  ]
}
```

Both signals must have write permission in the selected profile. Inspect the response's
`queued`, `errors`, and `warnings`; HTTP 202 alone does not confirm that both were sent.
Sending the pair in one batch keeps both values in the same CAN frame and avoids the
unwritten-sibling policy resetting one of them during separate writes.

When `SEAL_WithSound=1`, Panther selects an audio number and sends it as `HMI_Soundfile`
in `INC_HMI_Audio_Request` (CAN ID `0x220`, Panther → CAR-PC). For example,
`HMI_Soundfile=1` requests audio number 1. Blow in and Blow out do not implicitly select
audio numbers 1 and 2: Panther chooses the sound independently of the activation value.
With `SEAL_WithSound=0`, Panther performs the action without requesting a new sound.
This flag does not cancel audio already playing on CAR-PC.

CAR-PC starts audio in response to `HMI_Soundfile`; writing the SEAL request alone does
not start local playback. Panther supplies the actuator/demo behavior outside this runtime.

## Audio requests and feedback

| Signal | Direction | Meaning |
|---|---|---|
| `HMI_Soundfile` | Panther → CAR-PC | Requested audio number, `1..255`, mapped through `audio.files`. `0` means no new sound and re-arms the previous number. |
| `HMI_AudioTimesamp` | CAR-PC → Panther | Elapsed playback position in milliseconds while playing; `-1` when no audio is playing. |
| `HMI_ActiveSoundfile` | CAR-PC → Panther | Audio number actually playing; `0` when no audio is playing. |

`HMI_AudioTimesamp` preserves the spelling used by the application. Its raw DBC name is
`HMI_AudioTimesamp_ms`; the runtime removes the unit suffix. Its value is the position
within the audio, not a Unix timestamp. Both feedback signals share
`INC_HMI_Audio_Response` (CAN ID `0x221`).

CAR-PC accepts a changed, configured non-zero `HMI_Soundfile`. Repeating the same value
does not restart or enqueue the sound again. To replay the same number, send `0`, wait
until CAR-PC has observed it, then send the number again. `HMI_Soundfile=0` does not stop
the current track or clear its queue. Unconfigured numbers are ignored.

When playback starts, CAR-PC publishes position `0` and the active audio number. While
playing, it sends both feedback signals together in one CAN frame every **T ms**, where
`T = audio.position_update_interval_ms`. T is the target asynchronous publishing cadence.
The position increases until the track finishes; the active number remains that of the
track actually playing.

When the last track finishes, CAR-PC sends `HMI_AudioTimesamp=-1` and
`HMI_ActiveSoundfile=0` twice. The same idle feedback is sent at startup and on playback
failure. Idle feedback is not continuously retransmitted every T ms. When a queued track
follows, the current implementation transitions directly to its position `0` and audio
number; it does not insert an idle `-1` frame between queued tracks.

The feedback is also published to SignalStore and the existing signal WebSocket. To
observe requested and actual playback state, subscribe to all three signals:

```json
{"type":"subscribe","signals":["HMI_Soundfile","HMI_AudioTimesamp","HMI_ActiveSoundfile"],"rate_ms":100,"mode":"continuous"}
```

WebSocket delivery follows its own rate/coalescing settings; it is not a measurement of
every CAN transmission. Local feedback remains available if CAN transmission fails;
CAN publishing errors are logged.

## Configuration A T and playback type

| Demo parameter | System config field | Meaning |
|---|---|---|
| Array A | `audio.files` | List of `{name, audio_number}` mappings. Each number and file name must be unique. |
| T in ms | `audio.position_update_interval_ms` | Feedback interval, from `50` to `60000` ms. |
| Type 1 | `audio.track_queue_enable: false` | A new valid request stops the current track and starts the requested track from position `0`. |
| Type 2 | `audio.track_queue_enable: true` | A new valid request waits in a FIFO queue while the current track finishes. |

Example audio section in `config/system.json`:

```json
{
  "audio": {
    "enabled": true,
    "media_dir": "media/audio",
    "files": [
      {"name": "CarCrashSound.wav", "audio_number": 1},
      {"name": "x.wav", "audio_number": 2},
      {"name": "test.mp3", "audio_number": 3}
    ],
    "position_update_interval_ms": 100,
    "track_queue_enable": false,
    "allowed_extensions": [".mp3", ".wav", ".ogg"]
  }
}
```

The requested number matches `audio_number`, not the array position. For example, an
entry with `audio_number: 7` is requested by `HMI_Soundfile=7` wherever it appears in A.
Files must exist beneath `audio.media_dir`; `name` accepts a file name without directories.
The example maps number 3 explicitly; merely placing `test.mp3` in the directory does
not configure it for playback.

In type 2, if track 1 is playing and Panther requests track 2, `HMI_Soundfile` immediately
becomes `2`, while `HMI_ActiveSoundfile` remains `1` and its position keeps advancing.
After track 1 finishes, track 2 starts at position `0` and `HMI_ActiveSoundfile` becomes
`2`. Queueing delays playback only. The queue holds up to 64 pending requests; additional
requests are dropped with a warning when it is full.

`audio.files` and `audio.track_queue_enable` apply live to new requests. Playlist updates
leave the current playback intact; queued numbers use the updated mapping when they
start, and numbers removed from A are removed from the queue. Changing T applies to the
next playback, including the next queued track; the current track keeps its original T.
Changing `audio.enabled` or `audio.media_dir` requires a service restart.
See [system configuration management](system_config_management.md) for PATCH and reload
rules. Arrays replace completely, so submit all intended entries when updating A.

Linux playback requires `ffplay` and `ffprobe` from FFmpeg and an available audio output.
The Linux setup/deployment scripts install FFmpeg when needed. On macOS, playback falls
back to `afplay` and `afinfo` if FFmpeg is unavailable.

## Demo test with virtual CAN

Set the following in `config/system.json`, keep the intended audio section above, and
restart the application after changing the CAN interface or simulator setting:

```json
{
  "can": [
    {
      "interface": "virtual",
      "channel": "vcan0",
      "bitrate": 500000,
      "can_db_file": "db/can_db/Interface_Panther_To_CarPC_v11.dbc",
      "channel_tracking_signals": ["COM_Status_ElkCan"]
    }
  ],
  "simulator": {"enabled": false},
  "reader": {"stale_threshold_sec": 0}
}
```

`can` is an array: `can.interface: virtual` means `can[0].interface: "virtual"` for this
single-channel demo. Disabling the simulator prevents random requests. Setting
`reader.stale_threshold_sec=0` disables reopening a quiet bus between manual commands.

For this DBC, `HMI_Soundfile` is RX-only for CAR-PC. `PUT /signals/HMI_Soundfile` is
rejected by TX validation, including in Dev Mode. Test by sending a Panther request CAN
frame. The python-can `virtual` bus connects bus instances **within the same process**;
a sender launched in a separate terminal process cannot reach a running virtual bus.

For a manual test, stop the existing CAN-HMI instance, then start Python's asyncio REPL
from the repository root instead of running `can-hmi` separately:

```bash
.venv/bin/python -m asyncio
```

Paste this setup into the REPL. It starts the application and a second bus instance that
acts as Panther in the same process, using the saved configuration:

```python
import asyncio
import can
from src.can_io.parser import DatabaseLoader
from src.core.config import load_config
from src.core.runner import AppRunner

cfg = load_config("config/system.json")
assert len(cfg.can) == 1 and cfg.can[0].interface == "virtual"
assert not cfg.simulator.enabled and cfg.reader.stale_threshold_sec == 0
assert cfg.audio.enabled
dbc = DatabaseLoader()
dbc.load_dbc(cfg.can[0].can_db_file)
panther = can.Bus(interface="virtual", channel=cfg.can[0].channel)
runner = AppRunner(cfg)
runtime = asyncio.create_task(runner.start())
```

Wait for the application startup log and check `http://localhost:8000/system/ready`
(use the configured API host/port). Then define the request helper. It spaces frames by
at least 50 ms and waits until the reader/pipeline has observed each value, so a
`0 → same number` pair is not lost to receive rate gating or frame coalescing:

```python
async def sound(number):
    await asyncio.sleep(max(0.05, 2 / cfg.processor.max_update_rate_hz))
    frame = dbc.encode_signal("HMI_Soundfile", number)
    assert frame is not None
    panther.send(frame)
    async with asyncio.timeout(2):
        while True:
            observed = await runner.store.get("HMI_Soundfile")
            if observed is not None and observed.value == number:
                break
            await asyncio.sleep(0.01)

```

Send commands separately while watching the three signals in the dashboard or WebSocket:

```python
await sound(0)
await sound(1)
# Once track 1 is playing, request track 2 before track 1 finishes:
await sound(2)
```

| Test | Expected result |
|---|---|
| Change `0 → 1` | Track 1 starts; active number is `1`; position starts at `0` and advances every T ms on CAN. |
| Repeat `1` while the last request is `1` | No restart and no extra queue entry. |
| Send `0` during playback | The current track continues; the request is re-armed. |
| Type 1, change `1 → 2` while track 1 plays | Track 1 stops; track 2 starts from `0`; active number becomes `2`. |
| Type 2, change `1 → 2` while track 1 plays | Requested number becomes `2` immediately; active number stays `1` until track 1 finishes, then becomes `2` with position `0`. |
| Last track ends | Position becomes `-1` and active number becomes `0`; the idle CAN frame is sent twice. |
| After completion, send `0`, then the previous number | That audio plays again. |
| Request a number absent from A | No new playback is started or queued. |

Run the interruption and queue cases with their respective `audio.track_queue_enable`
setting. This test directly exercises Panther → CAR-PC audio requests; it does not
simulate the SEAL actuator or Panther's reaction to the HMI activation request.

When finished, stop the runtime and close the test sender before leaving the REPL:

```python
await runner.shutdown()
await runtime
panther.shutdown()
```
