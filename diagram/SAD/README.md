# Software Architecture Description

This directory contains architecture-level sequence diagrams. The SEAL audio demo has
three system participants: Web HMI requests an action, PANTHER performs it and selects
the sound, and CAR-PC plays the selected local audio and reports playback progress.

## Diagram index

| Diagram | Scope |
|---|---|
| [SEAL audio demo](SAD_sequence_audio_demo.puml) | Web HMI → CAR-PC → PANTHER requests and audio feedback |
| [Startup](SAD_sequence_boot_startup.puml) | Component initialization, including audio before API startup |
| [Shutdown](SAD_sequence_boot_shutdown.puml) | Audio cleanup before stopping readers and closing CAN buses |
| [Signal read](SAD_sequence_signal_read.puml) | CAN reception and dashboard delivery |
| [Signal write](SAD_sequence_signal_write.puml) | Dashboard command forwarding to CAN |
| [Restraints](SAD_sequence_restraints.puml) | Video matching and delivery |

## Audio responsibilities

| Participant | Responsibility |
|---|---|
| Web HMI | Submit `HMI_SEAL_ActivationRequest` and `SEAL_WithSound` together using the existing HTTP batch signal route; observe feedback through the existing signal WebSocket. |
| CAR-PC | Forward the SEAL request over CAN; receive `HMI_Soundfile`; play files mapped in `audio.files`; send playback position and active audio number to PANTHER. |
| PANTHER | Perform the requested SEAL action. When sound is requested, select an audio number and send `HMI_Soundfile` to CAR-PC. |

## Audio exchange

| Step | Direction | Signal or action | Behavior |
|---|---|---|---|
| 1 | Web HMI → CAR-PC, HTTP | `HMI_SEAL_ActivationRequest`, `SEAL_WithSound` | Activation `1` = Blow in, `2` = Blow out. Sound flag `1` = with sound, `0` = without sound. |
| 2 | CAR-PC → PANTHER, CAN | `INC_HMI_SEALRequest`, `0x081` | Forward both request values in one frame. PANTHER performs the SEAL action. |
| 3 | PANTHER → CAR-PC, CAN | `INC_HMI_Audio_Request`, `0x220`; `HMI_Soundfile=n` | Request audio number n when sound is enabled. The activation value does not determine the audio number. |
| 4 | CAR-PC → local player | `audio.files` | Resolve the entry whose `audio_number` equals n and play its file. |
| 5 | CAR-PC → PANTHER, CAN | `INC_HMI_Audio_Response`, `0x221` | Send `HMI_AudioTimesamp` in elapsed milliseconds and `HMI_ActiveSoundfile` every T ms while playing. |
| 6 | CAR-PC handles a new request | Interrupt or queue | Type 1 stops the current track and starts the new track. Type 2 queues the new track while the current one continues. |
| 7 | CAR-PC → PANTHER, CAN | Position `-1`, active number `0` | When the last track ends and the queue is empty, send the idle frame twice. |

`SEAL_WithSound=0` requests no new sound; it does not cancel an existing playback.
`HMI_Soundfile` is the latest requested number, while `HMI_ActiveSoundfile` identifies
the track actually playing. In queue mode these values can differ. CAR-PC publishes
both requested and actual state through SignalStore/WebSocket; WebSocket delivery has
its own rate settings.

## Configuration and lifecycle

| Parameter | Configuration | Meaning |
|---|---|---|
| Array A | `audio.files` | Unique file name/audio-number mappings under `audio.media_dir`; numbers are independent of array order. |
| T in ms | `audio.position_update_interval_ms` | Target feedback interval, `50..60000` ms. A change applies to the next playback. |
| Type 1 | `audio.track_queue_enable: false` | Interrupt the current track for a new valid request. |
| Type 2 | `audio.track_queue_enable: true` | Keep the current track playing and queue new valid requests in FIFO order. |

CAR-PC starts its audio manager after CAN readers/writers are constructed and before
building the API server. On shutdown it stops audio and publishes idle feedback if a
track was playing, while the CAN writer is still available, before closing CAN buses.

For detailed request handling, feedback publishing, and runtime settings, see the
[Software Design Description](../SDD/README.md). For configuration examples and a manual
test with `can[0].interface="virtual"`, `simulator.enabled=false`, and
`reader.stale_threshold_sec=0`, see the [audio demo guide](../../docs/audio_demo.md).
