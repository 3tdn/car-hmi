# Interface_Panther_To_CarPC_v3.dbc — Detailed Analysis

> **Source:** `Interface_Panther_To_CarPC_v3.dbc`  
> **Version:** Generated from CSV

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [ECU List (Network Nodes)](#2-ecu-list-network-nodes)
3. [INC Function Group — HMI Input Commands](#3-inc-function-group--hmi-input-commands)
4. [MON Function Group — Monitor / Sensor Data](#4-mon-function-group--monitor--sensor-data)
5. [SBS Function Group — Seatbelt System](#5-sbs-function-group--seatbelt-system)
6. [STS Function Group — Seat Position Status](#6-sts-function-group--seat-position-status)
7. [Summary Table of All Signals by Unit](#7-summary-table-of-all-signals-by-unit)
8. [Bit/Signal Encoding Conventions in the DBC](#8-bitsignal-encoding-conventions-in-the-dbc)

---

## 1. System Overview

This DBC file defines CAN communication between **PANTHER** (the intelligent safety system) and **CAR_PC** (HMI/CarPC), including these functions:

| Prefix group | Main function |
|-------------|----------------|
| `INC_`      | HMI sends commands into the system (Input Commands) |
| `MON_`      | Sensor/monitor data from ECUs back to CAR_PC |
| `SBS_`      | Seatbelt System — Request & Response |
| `STS_`      | Seat position status |

---

## 2. ECU List (Network Nodes)

| ECU | Role |
|-----|---------|
| `CAR_PC` | HMI / user interface, command coordinator |
| `PANTHER` | Central processor of the adaptive safety system |
| `EL_ECU` | Electromagnetic lock control ECU (E-Locking) |
| `PUMA_FL` | Front-Left seat actuator |
| `PUMA_FR` | Front-Right seat actuator |
| `PUMA_R1` | Rear-Left 1 seat actuator |
| `PUMA_R2` | Rear-Left 2 seat actuator |
| `PUMA_RR1` | Rear-Right 1 seat actuator |
| `SIMI` | OMS (Occupant Monitoring System) sensor |

---

## 3. INC Function Group — HMI Input Commands

These messages are sent by **CAR_PC** to **PANTHER**, carrying control commands from the HMI.

---

### 3.1 INC_Generic_MnlCpnActivation (ID: 132)

**Description:** Manually activates seat components from the HMI.  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `Generic_SeatFunctionEnable` | 0 | 5 | 0 | 31 | (bitmask) | Bit encoding for seat activation: bit0=FL, bit1=FR, bit2=R1, bit3=R2, bit4=RR |

**Values:**
- `1` = Seat FL enabled
- `2` = Seat FR enabled
- `4` = Seat R1 enabled
- `8` = Seat R2 enabled
- `16` = Seat RR enabled
- Multiple bits can be combined (e.g. `3` = FL + FR)

---

### 3.2 INC_HMI_CrashInfo (ID: 128)

**Description:** Crash information sent from HMI to PANTHER.  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `HMI_CrashImpactTrigger` | 17 | 1 | 0 | 1 | bool | Trigger: a crash is happening right now |
| `HMI_FR_OccupantAge_years` | 10 | 7 | 0 | 127 | years | Age of the FR seat occupant |
| `HMI_FL_OccupantAge_years` | 3 | 7 | 0 | 127 | years | Age of the FL seat occupant |
| `HMI_CrashSeverity` | 0 | 3 | 0 | 7 | enum | Crash severity level |

**`HMI_CrashSeverity` values:**
- `0` = Nothing
- `1` = 32 km/h (low severity)
- `2` = 56 km/h (high severity)

---

### 3.3 INC_HMI_SILGRequest (ID: 129)

**Description:** Request from the HMI to activate SILG (Side Impact / Lumbar Guard).  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `HMI_SILG_ActivationRequest` | 0 | 2 | 0 | 3 | enum | SILG activation request |

**Values:**
- `0` = Nothing
- `1` = Blow in
- `2` = Blow out

---

## 4. MON Function Group — Monitor / Sensor Data

These messages are sent by **PANTHER** or **SIMI** back to **CAR_PC** for monitoring.

---

### 4.1 MON_ARS_InjuryRisk (ID: 177)

**Description:** Injury risk level calculated by the ARS (Adaptive Restraint System) algorithm.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_FR_InjuryRiskNonAdaptive` | 24 | 8 | 0 | 255 | score | FR injury risk — without adaptive parameters |
| `ARS_FR_InjuryRiskAdaptive` | 16 | 8 | 0 | 255 | score | FR injury risk — with adaptive parameters |
| `ARS_FL_InjuryRiskNonAdaptive` | 8 | 8 | 0 | 255 | score | FL injury risk — without adaptive parameters |
| `ARS_FL_InjuryRiskAdaptive` | 0 | 8 | 0 | 255 | score | FL injury risk — with adaptive parameters |

---

### 4.2 MON_ARS_MSLLRequest (ID: 130)

**Description:** Trigger from the proactive restraints system.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_MSLL_ActivationRequest` | 0 | 8 | 0 | 255 | raw | Activation signal from ARS |

---

### 4.3 MON_ARS_TTF (ID: 178)

**Description:** Time To Fire for actuator activation in ARS.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_FR_TimeToFireSeatbelt` | 24 | 8 | 0 | 255 | ms (scaled) | TTF for the FR seatbelt actuator |
| `ARS_FR_TimeToFireAirbag` | 16 | 8 | 0 | 255 | ms (scaled) | TTF for the FR airbag vent |
| `ARS_FL_TimeToFireSeatbelt` | 8 | 8 | 0 | 255 | ms (scaled) | TTF for the FL seatbelt actuator |
| `ARS_FL_TimeToFireAirbag` | 0 | 8 | 0 | 255 | ms (scaled) | TTF for the FL airbag vent |

---

### 4.4 MON_OMS_FL_Status (ID: 179)

**Description:** FL occupant monitoring status from the OMS/DMS sensor.  
**Sender:** SIMI, PANTHER → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `OMS_FL_OccupantGender` | 50 | 2 | 0 | 3 | enum | FL occupant gender |
| `OMS_FL_SeatbeltMisuse_bool` | 49 | 1 | 0 | 1 | bool | Seatbelt is being used incorrectly |
| `OMS_FL_OccupantHeightStd_cm` | 41 | 8 | 0 | 255 | cm | Standard deviation of FL occupant height |
| `OMS_FL_OccupantHeightMean_cm` | 33 | 8 | 0 | 255 | cm | Mean height of the FL occupant |
| `OMS_FL_OccupantWeightStd_kg` | 25 | 8 | 0 | 255 | kg | Standard deviation of FL occupant weight |
| `OMS_FL_OccupantWeightMean_kg` | 17 | 8 | 0 | 255 | kg | Mean weight of the FL occupant |
| `OMS_FL_OutOfPosition` | 5 | 12 | 0 | 4095 | raw | Out-of-position seating status |
| `OMS_FL_OccupantClassification` | 2 | 3 | 0 | 7 | enum | Occupant classification: 25%/50%/95% occupant |
| `OMS_FL_HandsOnWheel` | 0 | 2 | 0 | 3 | enum | Hands on the steering wheel (0=off, 1=on) |

---

### 4.5 MON_OMS_FR_Status (ID: 180)

**Description:** FR occupant monitoring status.  
**Sender:** SIMI, PANTHER → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `OMS_FR_OccupantGender` | 49 | 2 | 0 | 3 | enum | FR occupant gender |
| `OMS_FR_SeatbeltMisuse_bool` | 48 | 1 | 0 | 1 | bool | Seatbelt misuse |
| `OMS_FR_ChildSeatDetected_bool` | 47 | 1 | 0 | 1 | bool | Child seat detected |
| `OMS_FR_OccupantHeightStd_cm` | 39 | 8 | 0 | 255 | cm | Standard deviation of FR height |
| `OMS_FR_OccupantHeightMean_cm` | 31 | 8 | 0 | 255 | cm | Mean height of the FR occupant |
| `OMS_FR_OccupantWeightStd_kg` | 23 | 8 | 0 | 255 | kg | Standard deviation of FR weight |
| `OMS_FR_OccupantWeightMean_kg` | 15 | 8 | 0 | 255 | kg | Mean weight of the FR occupant |
| `OMS_FR_OutOfPosition` | 3 | 12 | 0 | 4095 | raw | Out-of-position seating status |
| `OMS_FR_OccupantClassification` | 0 | 3 | 0 | 7 | enum | Occupant classification |

---

### 4.6 MON_SMA_VehicleState (ID: 176)

**Description:** Vehicle stability state from SMA.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `SMA_VehicleStable` | 0 | 1 | 0 | 1 | bool | 0=vehicle unstable, 1=vehicle stable |

---

## 5. SBS Function Group — Seatbelt System

The seatbelt system consists of multiple subsystems:

| Subsystem | Abbreviation | Function |
|---------|----------|-----------|
| ABL | Active Belt Lifter | Active lifting/lowering of the seatbelt |
| ACR | Active Crash Retractor | Retracts the seatbelt during a crash |
| BSW | Buckle Switch | Buckle lock sensor |
| ELK | E-Locking | Electromagnetic seatbelt lock |
| HB | Haptic Belt | Warning vibration in the seatbelt |
| ISB | Illuminated Seatbelt Buckle | Buckle with LED light |
| WMS | Webbing Movement Sensor | Sensor measuring belt movement |

---

### 5.1 ABL — Active Belt Lifter

#### 5.1.1 SBS_ABL_Activation (ID: 144) — Request

**Sender:** CAR_PC → PANTHER, PUMA_xx

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ABL_FL_RetractRequest` | 0 | 8 | 0 | 255 | enum | ABL command for the FL seat |
| `ABL_FR_RetractRequest` | 8 | 8 | 0 | 255 | enum | ABL command for the FR seat |
| `ABL_RL1_RetractRequest` | 16 | 8 | 0 | 255 | enum | ABL command for the RL1 seat |
| `ABL_RL2_RetractRequest` | 24 | 8 | 0 | 255 | enum | ABL command for the RL2 seat |
| `ABL_RR1_RetractRequest` | 32 | 8 | 0 | 255 | enum | ABL command for the RR1 seat |

**Values:**
- `0` = Nothing
- `1` = Offer Position
- `2` = S0 Position
- `3` = Dynamic
- `4` = Pre Crash
- `5` = Haptic
- `11` = Wake Up
- `12` = Park Position

#### 5.1.2 SBS_ABL_FL_Response (ID: 384) — Response

**Sender:** PUMA_FL → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ABL_FL_ActivationPhase` | 9 | 8 | 0 | 255 | enum | Current execution phase |
| `ABL_FL_ActivationLevelStatus` | 1 | 8 | 0 | 255 | enum | Current activation level |
| `ABL_FL_S0SensorStatus_bool` | 0 | 1 | 0 | 1 | bool | 0=S0 not reached yet, 1=S0 reached |

**`ActivationPhase` values:**
`0`=No activation, `1`=Welcome, `12`=S0 Calibration, `21`=Buckle Lift, `31`=Beltpark, `41`=Beltslack Removal, `51`=Haptic Warning, `71`=Retract 1, `72`=Retract 2, `74`=Reverse, `81`=Release, `90`=PTMP direct demand

**`ActivationLevelStatus` values:**
`0`=No activation, `1`=Welcome Request, `11`=Diagnoses Pulse, `12`=S0 calibration, `21`=Buckle Lift, `22`=Buckle Lift Post Crash, `31`=Beltpark Support, `41`=Beltslack Removal, `51`=Haptic Warning, `61`=Dynamic Support, `71`–`79`=Retract Level 1–9, `81`=Safety Release, `95`=PTMP Position Demand, `96`=PTMP Speed Demand, `97`=PTMP Current Demand, `98`=PTMP Voltage Demand, `99`=PTMP Dutycycle Demand

> Similar responses exist for FR (ID: 385), RL1 (ID: 386), RL2 (ID: 387), RR1 (ID: 388).

---

### 5.2 ACR — Active Crash Retractor

#### 5.2.1 SBS_ACR_Activation (ID: 145) — Request

**Sender:** CAR_PC → PANTHER, PUMA_xx

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ACR_FL_RetractRequest` | 0 | 8 | 0 | 255 | enum | ACR retract request for the FL seat |
| `ACR_FR_RetractRequest` | 8 | 8 | 0 | 255 | enum | ACR retract request for the FR seat |
| `ACR_RL1_RetractRequest` | 16 | 8 | 0 | 255 | enum | ACR retract request for the RL1 seat |
| `ACR_RL2_RetractRequest` | 24 | 8 | 0 | 255 | enum | ACR retract request for the RL2 seat |
| `ACR_RR1_RetractRequest` | 32 | 8 | 0 | 255 | enum | ACR retract request for the RR1 seat |

#### 5.2.2 SBS_ACR_FL_Response (ID: 392) — Response

**Sender:** PUMA_FL → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ACR_FL_SpoolFasterClutch` | 16 | 8 | 0 | 255 | raw | Spool motor speed |
| `ACR_FL_ActivationPhase` | 8 | 8 | 0 | 255 | enum | Execution phase |
| `ACR_FL_ActivationLevelStatus` | 0 | 8 | 0 | 255 | enum | Activation level |

**`ActivationLevelStatus` values (ACR):**
`0`=No activation, `11`=Diagnoses Pulse, `16`=PTMP Motor Speed+Current Limit, `17`=PTMP Motor Current, `18`=PTMP Motor Voltage, `19`=PTMP Motor PWM Dutycycle, `29`=BeltPark L1, `30`=BeltPark L2, `31`=Beltslack Reduction, `32`–`37`=Haptic Warning L1–L6, `41`–`49`=Retract L1–L9, `48`=Dynamik Belt, `51`–`53`=RePos L1–L3

> Similar responses exist for FR (ID: 393), RL1 (ID: 394), RL2 (ID: 395), RR1 (ID: 396).

---

### 5.3 BSW — Buckle Switch

#### SBS_BSW_Status (ID: 409)

**Description:** Seatbelt buckle status for all seats.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `BSW_FL_BuckleStatus` | 0 | 1 | 0 | 1 | bool | FL: 0=Unbuckled, 1=Buckled |
| `BSW_FR_BuckleStatus` | 1 | 1 | 0 | 1 | bool | FR: 0=Unbuckled, 1=Buckled |
| `BSW_RL1_BuckleStatus` | 2 | 1 | 0 | 1 | bool | RL1: 0=Unbuckled, 1=Buckled |
| `BSW_RL2_BuckleStatus` | 3 | 1 | 0 | 1 | bool | RL2: 0=Unbuckled, 1=Buckled |
| `BSW_RR1_BuckleStatus` | 4 | 1 | 0 | 1 | bool | RR1: 0=Unbuckled, 1=Buckled |

---

### 5.4 ELK — E-Locking (Electromagnetic Lock)

#### 5.4.1 SBS_ELK_Activation (ID: 146) — Request

**Sender:** CAR_PC → PANTHER, EL_ECU

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ELK_FL_LockingRequest` | 0 | 1 | 0 | 1 | bool | Lock request for FL |
| `ELK_FR_LockingRequest` | 1 | 1 | 0 | 1 | bool | Lock request for FR |
| `ELK_RL1_LockingRequest` | 2 | 1 | 0 | 1 | bool | Lock request for RL1 |
| `ELK_RL2_LockingRequest` | 3 | 1 | 0 | 1 | bool | Lock request for RL2 |
| `ELK_RR1_LockingRequest` | 4 | 1 | 0 | 1 | bool | Lock request for RR1 |

#### 5.4.2 SBS_ELK_Status (ID: 408) — Status

**Sender:** EL_ECU → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ELK_FL_LockingStatus` | 0 | 2 | 0 | 3 | enum | FL lock status |
| `ELK_FR_LockingStatus` | 2 | 2 | 0 | 3 | enum | FR lock status |
| `ELK_RL1_LockingStatus` | 4 | 2 | 0 | 3 | enum | RL1 lock status |
| `ELK_RL2_LockingStatus` | 6 | 2 | 0 | 3 | enum | RL2 lock status |
| `ELK_RR1_LockingStatus` | 8 | 2 | 0 | 3 | enum | RR1 lock status |

---

### 5.5 HB — Haptic Belt (Warning seatbelt vibration)

#### 5.5.1 SBS_HB_Status (ID: 410) — Activation Level

**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `HB_FL_ActivationLevel` | 0 | 3 | 0 | 7 | level | FL seat vibration level (Level 1–3) |
| `HB_FR_ActivationLevel` | 3 | 3 | 0 | 7 | level | FR seat vibration level (Level 1–4) |
| `HB_RL1_ActivationLevel` | 6 | 3 | 0 | 7 | level | RL1 seat vibration level (Level 1–5) |
| `HB_RL2_ActivationLevel` | 9 | 3 | 0 | 7 | level | RL2 seat vibration level (Level 1–6) |
| `HB_RR1_ActivationLevel` | 12 | 3 | 0 | 7 | level | RR1 seat vibration level (Level 1–7) |

#### 5.5.2 SBS_HB_GenericCmd (ID: 131)

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `HB_ActivationSync` | 0 | 1 | 0 | 1 | bool | Synchronize all vibration levels |

#### 5.5.3 SBS_HB_TargetTemp (ID: 133)

**Description:** Target temperature for the seatbelt-integrated heating/cooling system.

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `HB_ManualTargetTemp` | 32 | 32 | 0 | 4294967295 | °C | Temperature manually set from the HMI |
| `HB_DynamicTargetTemp` | 0 | 32 | 0 | 4294967295 | °C | Temperature calculated by the HB Boosting algorithm |

---

### 5.6 ISB — Illuminated Seatbelt Buckle (Seatbelt buckle with LED)

Each seat has its own request message. The structure is identical for FL/FR/RL1/RL2/RR1.

#### SBS_ISB_FL_Request (ID: 160)

**Sender:** CAR_PC → PANTHER, PUMA_FL

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `ISB_FL_ColorGreen_byte` | 0 | 8 | 0 | 255 | 0–255 | Green color channel |
| `ISB_FL_ColorBlue_byte` | 8 | 8 | 0 | 255 | 0–255 | Blue color channel |
| `ISB_FL_ColorRed_byte` | 16 | 8 | 0 | 255 | 0–255 | Red color channel |
| `ISB_FL_Intensity_perc` | 24 | 7 | 0 | 127 | % | Brightness intensity (0–127 ≈ 0–100%) |
| `ISB_FL_Normalization_bool` | 31 | 1 | 0 | 1 | bool | Color normalization |
| `ISB_FL_Transitionspeed_nibble` | 32 | 4 | 0 | 15 | level | Color transition speed |
| `ISB_FL_GroupOrModule_bool` | 36 | 1 | 0 | 1 | bool | 0=single module, 1=group |
| `ISB_FL_AddressByte0_byte` | 37 | 8 | 0 | 255 | addr | Address byte 0 |
| `ISB_FL_AddressByte1_byte` | 45 | 8 | 0 | 255 | addr | Address byte 1 |

> The structure is similar for FR (ID: 161), RL1 (ID: 162), RL2 (ID: 163), RR1 (ID: 164).

---

### 5.7 WMS — Webbing Movement Sensor

**Description:** Sensor measuring belt movement, inferred from the ACR spool rotation angle.

#### SBS_WMS_FL_Response (ID: 400)

**Sender:** PANTHER, PUMA_FL → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `WMS_FL_WebbingMovement_mm` | 0 | 13 | 0 | 8191 | mm | Length of belt webbing paid out |
| `WMS_FL_SpoolAngle_deg` | 13 | 14 | 0 | 16383 | deg | Spool rotation angle |
| `WMS_FL_SensorStatus_bool` | 27 | 3 | 0 | 7 | enum | WMS sensor status |

> Similar responses exist for FR (ID: 401), RL1 (ID: 402), RL2 (ID: 403), RR1 (ID: 404).

**`WebbingMovement_mm` explanation:**
- `0` = Belt not paid out (belt fully retracted)
- `8191` = Maximum payout level (13-bit unsigned, ~8.2 m if scale = 1 mm/LSB)
- The value increases as the belt pays out and decreases as it retracts

---

## 6. STS Function Group — Seat Position Status

### 6.1 STS_SPS_FL_SeatPosition (ID: 181)

**Description:** Position of FL seat components.  
**Sender:** PANTHER → CAR_PC, SIMI

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `SPS_FL_SeatDirectionX` | 0 | 12 | 0 | 4095 | mm | Seat position on the X axis (forward/backward) |
| `SPS_FL_SeatDirectionZ` | 12 | 12 | 0 | 4095 | mm | Seat position on the Z axis (up/down) |
| `SPS_FL_SeatBackRestPosition` | 24 | 12 | 0 | 4095 | deg | Backrest angle |
| `SPS_FL_FootRestPosition` | 36 | 12 | 0 | 4095 | deg | Footrest angle |
| `SPS_FL_HeadRestPosition` | 48 | 12 | 0 | 4095 | mm | Headrest position |

### 6.2 STS_SPS_FR_SeatPosition (ID: 182)

**Description:** Position of FR seat components.  
**Sender:** PANTHER → CAR_PC, SIMI

| Signal | Bit pos | Bits | Min | Max | Unit | Description |
|--------|---------|------|-----|-----|---------|-------|
| `SPS_FR_SeatDirectionX` | 0 | 12 | 0 | 4095 | mm | Seat position on the X axis |
| `SPS_FR_SeatDirectionZ` | 12 | 12 | 0 | 4095 | mm | Seat position on the Z axis |
| `SPS_FR_SeatBackRestPosition` | 24 | 12 | 0 | 4095 | deg | Backrest angle |
| `SPS_FR_FootRestPosition` | 36 | 12 | 0 | 4095 | deg | Footrest angle |
| `SPS_FR_HeadRestPosition` | 48 | 12 | 0 | 4095 | mm | Headrest position |

---

## 7. Summary Table of All Signals by Unit

| Unit | Signals |
|--------|---------|
| **mm** | WebbingMovement (0–8191), SeatDirectionX/Z (0–4095), HeadRestPosition (0–4095) |
| **deg** | SpoolAngle (0–16383), SeatBackRestPosition (0–4095), FootRestPosition (0–4095) |
| **cm** | OccupantHeightMean/Std (0–255) |
| **kg** | OccupantWeightMean/Std (0–255) |
| **years** | OccupantAge (0–127) |
| **°C** | HB_ManualTargetTemp, HB_DynamicTargetTemp (0–4294967295) |
| **% (0–127)** | ISB Intensity |
| **bool (0/1)** | BuckleStatus, LockingRequest, SeatbeltMisuse, ChildSeatDetected, SMA_VehicleStable, HB_ActivationSync |
| **enum** | CrashSeverity, SILG, ABL/ACR ActivationPhase/Level, ELK Status, OccupantClassification |
| **score (0–255)** | InjuryRisk Adaptive/NonAdaptive |
| **bitmask (0–31)** | Generic_SeatFunctionEnable |
| **RGB (0–255)** | ISB ColorRed/Green/Blue |

---

## 8. Bit/Signal Encoding Conventions in the DBC

DBC signal syntax:

```
SG_ <SignalName> : <BitStart>|<BitLen>@<ByteOrder><Signed> (<Factor>,<Offset>) [<Min>|<Max>] "<Unit>" <Receivers>
```

| Field | Meaning |
|--------|---------|
| `BitStart` | Starting bit (LSB) in the CAN frame |
| `BitLen` | Number of bits in the signal |
| `@1` | Little-endian (Intel byte order) |
| `@0` | Big-endian (Motorola byte order) |
| `+` | Unsigned |
| `-` | Signed |
| `Factor` | Multiplier for the physical value: `PhysVal = RawVal * Factor + Offset` |
| `Offset` | Offset value |
| `[Min|Max]` | Valid physical value range |

**Example:**
```
SG_ WMS_FL_WebbingMovement_mm : 0|13@1+ (1,0) [0|8191] "" PANTHER,CAR_PC
```
→ Bit 0, length 13 bits, little-endian, unsigned, factor=1, offset=0, range 0–8191 mm.

---

*Document automatically generated from DBC file analysis — 2026-06-29*
