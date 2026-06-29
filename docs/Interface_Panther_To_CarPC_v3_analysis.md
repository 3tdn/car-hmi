# Interface_Panther_To_CarPC_v3.dbc — Phân tích chi tiết

> **Nguồn:** `Interface_Panther_To_CarPC_v3.dbc`  
> **Phiên bản:** Generated from CSV

---

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Danh sách ECU (Network Nodes)](#2-danh-sách-ecu-network-nodes)
3. [Nhóm chức năng INC — HMI Input Commands](#3-nhóm-inc--hmi-input-commands)
4. [Nhóm chức năng MON — Monitor / Sensor Data](#4-nhóm-mon--monitor--sensor-data)
5. [Nhóm chức năng SBS — Seatbelt System](#5-nhóm-sbs--seatbelt-system)
6. [Nhóm chức năng STS — Seat Position Status](#6-nhóm-sts--seat-position-status)
7. [Bảng tổng hợp tất cả signal theo đơn vị](#7-bảng-tổng-hợp-tất-cả-signal-theo-đơn-vị)
8. [Quy ước mã hóa bit/signal trong DBC](#8-quy-ước-mã-hóa-bitsignal-trong-dbc)

---

## 1. Tổng quan hệ thống

File DBC này định nghĩa giao tiếp CAN giữa **PANTHER** (hệ thống an toàn thông minh) và **CAR_PC** (HMI/CarPC), bao gồm các chức năng:

| Nhóm prefix | Chức năng chính |
|-------------|----------------|
| `INC_`      | HMI gửi lệnh vào hệ thống (Input Commands) |
| `MON_`      | Dữ liệu cảm biến/monitor từ các ECU về CAR_PC |
| `SBS_`      | Hệ thống dây an toàn (Seatbelt System) — Request & Response |
| `STS_`      | Trạng thái vị trí ghế (Seat Position Status) |

---

## 2. Danh sách ECU (Network Nodes)

| ECU | Vai trò |
|-----|---------|
| `CAR_PC` | HMI / giao diện người dùng, điều phối lệnh |
| `PANTHER` | Bộ xử lý trung tâm hệ thống an toàn thích nghi |
| `EL_ECU` | ECU điều khiển khóa điện từ (E-Locking) |
| `PUMA_FL` | Actuator ghế Front-Left |
| `PUMA_FR` | Actuator ghế Front-Right |
| `PUMA_R1` | Actuator ghế Rear-Left 1 |
| `PUMA_R2` | Actuator ghế Rear-Left 2 |
| `PUMA_RR1` | Actuator ghế Rear-Right 1 |
| `SIMI` | Cảm biến OMS (Occupant Monitoring System) |

---

## 3. Nhóm INC — HMI Input Commands

Các message này do **CAR_PC** gửi đến **PANTHER**, mang lệnh điều khiển từ HMI.

---

### 3.1 INC_Generic_MnlCpnActivation (ID: 132)

**Mô tả:** Kích hoạt thủ công các thành phần ghế từ HMI.  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `Generic_SeatFunctionEnable` | 0 | 5 | 0 | 31 | (bitmask) | Bit encoding kích hoạt ghế: bit0=FL, bit1=FR, bit2=R1, bit3=R2, bit4=RR |

**Giá trị:**
- `1` = Seat FL enable
- `2` = Seat FR enable
- `4` = Seat R1 enable
- `8` = Seat R2 enable
- `16` = Seat RR enable
- Có thể kết hợp nhiều bit (e.g. `3` = FL + FR)

---

### 3.2 INC_HMI_CrashInfo (ID: 128)

**Mô tả:** Thông tin va chạm từ HMI gửi đến PANTHER.  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `HMI_CrashImpactTrigger` | 17 | 1 | 0 | 1 | bool | Trigger: va chạm xảy ra ngay lúc này |
| `HMI_FR_OccupantAge_years` | 10 | 7 | 0 | 127 | years | Tuổi hành khách ghế FR |
| `HMI_FL_OccupantAge_years` | 3 | 7 | 0 | 127 | years | Tuổi hành khách ghế FL |
| `HMI_CrashSeverity` | 0 | 3 | 0 | 7 | enum | Mức độ nghiêm trọng va chạm |

**Giá trị `HMI_CrashSeverity`:**
- `0` = Không có gì
- `1` = 32 km/h (low severity)
- `2` = 56 km/h (high severity)

---

### 3.3 INC_HMI_SILGRequest (ID: 129)

**Mô tả:** Yêu cầu kích hoạt SILG (Side Impact / Lumbar Guard) từ HMI.  
**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `HMI_SILG_ActivationRequest` | 0 | 2 | 0 | 3 | enum | Yêu cầu kích hoạt SILG |

**Giá trị:**
- `0` = Nothing
- `1` = Blow in
- `2` = Blow out

---

## 4. Nhóm MON — Monitor / Sensor Data

Các message này do **PANTHER** hoặc **SIMI** gửi về **CAR_PC** để giám sát.

---

### 4.1 MON_ARS_InjuryRisk (ID: 177)

**Mô tả:** Mức độ rủi ro chấn thương tính toán bởi thuật toán ARS (Adaptive Restraint System).  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_FR_InjuryRiskNonAdaptive` | 24 | 8 | 0 | 255 | score | Rủi ro chấn thương FR — không dùng tham số adaptive |
| `ARS_FR_InjuryRiskAdaptive` | 16 | 8 | 0 | 255 | score | Rủi ro chấn thương FR — dùng tham số adaptive |
| `ARS_FL_InjuryRiskNonAdaptive` | 8 | 8 | 0 | 255 | score | Rủi ro chấn thương FL — không dùng tham số adaptive |
| `ARS_FL_InjuryRiskAdaptive` | 0 | 8 | 0 | 255 | score | Rủi ro chấn thương FL — dùng tham số adaptive |

---

### 4.2 MON_ARS_MSLLRequest (ID: 130)

**Mô tả:** Trigger từ hệ thống restraint chủ động.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_MSLL_ActivationRequest` | 0 | 8 | 0 | 255 | raw | Tín hiệu kích hoạt từ ARS |

---

### 4.3 MON_ARS_TTF (ID: 178)

**Mô tả:** Thời gian đến khi kích hoạt actuator (Time To Fire) của ARS.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ARS_FR_TimeToFireSeatbelt` | 24 | 8 | 0 | 255 | ms (scaled) | TTF cho seatbelt actuator ghế FR |
| `ARS_FR_TimeToFireAirbag` | 16 | 8 | 0 | 255 | ms (scaled) | TTF cho airbag vent ghế FR |
| `ARS_FL_TimeToFireSeatbelt` | 8 | 8 | 0 | 255 | ms (scaled) | TTF cho seatbelt actuator ghế FL |
| `ARS_FL_TimeToFireAirbag` | 0 | 8 | 0 | 255 | ms (scaled) | TTF cho airbag vent ghế FL |

---

### 4.4 MON_OMS_FL_Status (ID: 179)

**Mô tả:** Trạng thái giám sát hành khách ghế FL từ cảm biến OMS/DMS.  
**Sender:** SIMI, PANTHER → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `OMS_FL_OccupantGender` | 50 | 2 | 0 | 3 | enum | Giới tính hành khách FL |
| `OMS_FL_SeatbeltMisuse_bool` | 49 | 1 | 0 | 1 | bool | Dây an toàn đang bị sử dụng sai |
| `OMS_FL_OccupantHeightStd_cm` | 41 | 8 | 0 | 255 | cm | Độ lệch chuẩn chiều cao hành khách FL |
| `OMS_FL_OccupantHeightMean_cm` | 33 | 8 | 0 | 255 | cm | Chiều cao trung bình hành khách FL |
| `OMS_FL_OccupantWeightStd_kg` | 25 | 8 | 0 | 255 | kg | Độ lệch chuẩn cân nặng hành khách FL |
| `OMS_FL_OccupantWeightMean_kg` | 17 | 8 | 0 | 255 | kg | Cân nặng trung bình hành khách FL |
| `OMS_FL_OutOfPosition` | 5 | 12 | 0 | 4095 | raw | Trạng thái ra khỏi vị trí ngồi chuẩn |
| `OMS_FL_OccupantClassification` | 2 | 3 | 0 | 7 | enum | Phân loại hành khách: 25%/50%/95% occupant |
| `OMS_FL_HandsOnWheel` | 0 | 2 | 0 | 3 | enum | Tay trên vô lăng (0=off, 1=on) |

---

### 4.5 MON_OMS_FR_Status (ID: 180)

**Mô tả:** Trạng thái giám sát hành khách ghế FR.  
**Sender:** SIMI, PANTHER → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `OMS_FR_OccupantGender` | 49 | 2 | 0 | 3 | enum | Giới tính hành khách FR |
| `OMS_FR_SeatbeltMisuse_bool` | 48 | 1 | 0 | 1 | bool | Dây an toàn bị sử dụng sai |
| `OMS_FR_ChildSeatDetected_bool` | 47 | 1 | 0 | 1 | bool | Phát hiện ghế trẻ em |
| `OMS_FR_OccupantHeightStd_cm` | 39 | 8 | 0 | 255 | cm | Độ lệch chuẩn chiều cao FR |
| `OMS_FR_OccupantHeightMean_cm` | 31 | 8 | 0 | 255 | cm | Chiều cao trung bình hành khách FR |
| `OMS_FR_OccupantWeightStd_kg` | 23 | 8 | 0 | 255 | kg | Độ lệch chuẩn cân nặng FR |
| `OMS_FR_OccupantWeightMean_kg` | 15 | 8 | 0 | 255 | kg | Cân nặng trung bình hành khách FR |
| `OMS_FR_OutOfPosition` | 3 | 12 | 0 | 4095 | raw | Trạng thái ra khỏi vị trí ngồi chuẩn |
| `OMS_FR_OccupantClassification` | 0 | 3 | 0 | 7 | enum | Phân loại hành khách |

---

### 4.6 MON_SMA_VehicleState (ID: 176)

**Mô tả:** Trạng thái ổn định xe từ SMA.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `SMA_VehicleStable` | 0 | 1 | 0 | 1 | bool | 0=xe không ổn định, 1=xe ổn định |

---

## 5. Nhóm SBS — Seatbelt System

Hệ thống dây an toàn gồm nhiều phân hệ:

| Phân hệ | Viết tắt | Chức năng |
|---------|----------|-----------|
| ABL | Active Belt Lifter | Nâng/hạ dây an toàn chủ động |
| ACR | Active Crash Retractor | Cuộn dây an toàn khi va chạm |
| BSW | Buckle Switch | Cảm biến khóa dây |
| ELK | E-Locking | Khóa điện từ dây an toàn |
| HB | Haptic Belt | Rung dây an toàn cảnh báo |
| ISB | Illuminated Seatbelt Buckle | Khóa dây có đèn LED |
| WMS | Webbing Movement Sensor | Cảm biến đo dịch chuyển dây |

---

### 5.1 ABL — Active Belt Lifter

#### 5.1.1 SBS_ABL_Activation (ID: 144) — Request

**Sender:** CAR_PC → PANTHER, PUMA_xx

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ABL_FL_RetractRequest` | 0 | 8 | 0 | 255 | enum | Lệnh ABL ghế FL |
| `ABL_FR_RetractRequest` | 8 | 8 | 0 | 255 | enum | Lệnh ABL ghế FR |
| `ABL_RL1_RetractRequest` | 16 | 8 | 0 | 255 | enum | Lệnh ABL ghế RL1 |
| `ABL_RL2_RetractRequest` | 24 | 8 | 0 | 255 | enum | Lệnh ABL ghế RL2 |
| `ABL_RR1_RetractRequest` | 32 | 8 | 0 | 255 | enum | Lệnh ABL ghế RR1 |

**Giá trị:**
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

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ABL_FL_ActivationPhase` | 9 | 8 | 0 | 255 | enum | Phase thực thi hiện tại |
| `ABL_FL_ActivationLevelStatus` | 1 | 8 | 0 | 255 | enum | Mức kích hoạt hiện tại |
| `ABL_FL_S0SensorStatus_bool` | 0 | 1 | 0 | 1 | bool | 0=chưa đạt S0, 1=đã đạt S0 |

**Giá trị `ActivationPhase`:**
`0`=No activation, `1`=Welcome, `12`=S0 Calibration, `21`=Buckle Lift, `31`=Beltpark, `41`=Beltslack Removal, `51`=Haptic Warning, `71`=Retract 1, `72`=Retract 2, `74`=Reverse, `81`=Release, `90`=PTMP direct demand

**Giá trị `ActivationLevelStatus`:**
`0`=No activation, `1`=Welcome Request, `11`=Diagnoses Pulse, `12`=S0 calibration, `21`=Buckle Lift, `22`=Buckle Lift Post Crash, `31`=Beltpark Support, `41`=Beltslack Removal, `51`=Haptic Warning, `61`=Dynamic Support, `71`–`79`=Retract Level 1–9, `81`=Safety Release, `95`=PTMP Position Demand, `96`=PTMP Speed Demand, `97`=PTMP Current Demand, `98`=PTMP Voltage Demand, `99`=PTMP Dutycycle Demand

> Response tương tự cho FR (ID: 385), RL1 (ID: 386), RL2 (ID: 387), RR1 (ID: 388).

---

### 5.2 ACR — Active Crash Retractor

#### 5.2.1 SBS_ACR_Activation (ID: 145) — Request

**Sender:** CAR_PC → PANTHER, PUMA_xx

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ACR_FL_RetractRequest` | 0 | 8 | 0 | 255 | enum | Yêu cầu retract ACR ghế FL |
| `ACR_FR_RetractRequest` | 8 | 8 | 0 | 255 | enum | Yêu cầu retract ACR ghế FR |
| `ACR_RL1_RetractRequest` | 16 | 8 | 0 | 255 | enum | Yêu cầu retract ACR ghế RL1 |
| `ACR_RL2_RetractRequest` | 24 | 8 | 0 | 255 | enum | Yêu cầu retract ACR ghế RL2 |
| `ACR_RR1_RetractRequest` | 32 | 8 | 0 | 255 | enum | Yêu cầu retract ACR ghế RR1 |

#### 5.2.2 SBS_ACR_FL_Response (ID: 392) — Response

**Sender:** PUMA_FL → PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ACR_FL_SpoolFasterClutch` | 16 | 8 | 0 | 255 | raw | Tốc độ spool motor |
| `ACR_FL_ActivationPhase` | 8 | 8 | 0 | 255 | enum | Phase thực thi |
| `ACR_FL_ActivationLevelStatus` | 0 | 8 | 0 | 255 | enum | Mức kích hoạt |

**Giá trị `ActivationLevelStatus` (ACR):**
`0`=No activation, `11`=Diagnoses Pulse, `16`=PTMP Motor Speed+Current Limit, `17`=PTMP Motor Current, `18`=PTMP Motor Voltage, `19`=PTMP Motor PWM Dutycycle, `29`=BeltPark L1, `30`=BeltPark L2, `31`=Beltslack Reduction, `32`–`37`=Haptic Warning L1–L6, `41`–`49`=Retract L1–L9, `48`=Dynamik Belt, `51`–`53`=RePos L1–L3

> Response tương tự cho FR (ID: 393), RL1 (ID: 394), RL2 (ID: 395), RR1 (ID: 396).

---

### 5.3 BSW — Buckle Switch

#### SBS_BSW_Status (ID: 409)

**Mô tả:** Trạng thái khóa dây an toàn tất cả ghế.  
**Sender:** PANTHER → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `BSW_FL_BuckleStatus` | 0 | 1 | 0 | 1 | bool | FL: 0=Unbuckled, 1=Buckled |
| `BSW_FR_BuckleStatus` | 1 | 1 | 0 | 1 | bool | FR: 0=Unbuckled, 1=Buckled |
| `BSW_RL1_BuckleStatus` | 2 | 1 | 0 | 1 | bool | RL1: 0=Unbuckled, 1=Buckled |
| `BSW_RL2_BuckleStatus` | 3 | 1 | 0 | 1 | bool | RL2: 0=Unbuckled, 1=Buckled |
| `BSW_RR1_BuckleStatus` | 4 | 1 | 0 | 1 | bool | RR1: 0=Unbuckled, 1=Buckled |

---

### 5.4 ELK — E-Locking (Khóa điện từ)

#### 5.4.1 SBS_ELK_Activation (ID: 146) — Request

**Sender:** CAR_PC → PANTHER, EL_ECU

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ELK_FL_LockingRequest` | 0 | 1 | 0 | 1 | bool | Yêu cầu khóa FL |
| `ELK_FR_LockingRequest` | 1 | 1 | 0 | 1 | bool | Yêu cầu khóa FR |
| `ELK_RL1_LockingRequest` | 2 | 1 | 0 | 1 | bool | Yêu cầu khóa RL1 |
| `ELK_RL2_LockingRequest` | 3 | 1 | 0 | 1 | bool | Yêu cầu khóa RL2 |
| `ELK_RR1_LockingRequest` | 4 | 1 | 0 | 1 | bool | Yêu cầu khóa RR1 |

#### 5.4.2 SBS_ELK_Status (ID: 408) — Status

**Sender:** EL_ECU → CAR_PC, PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ELK_FL_LockingStatus` | 0 | 2 | 0 | 3 | enum | Trạng thái khóa FL |
| `ELK_FR_LockingStatus` | 2 | 2 | 0 | 3 | enum | Trạng thái khóa FR |
| `ELK_RL1_LockingStatus` | 4 | 2 | 0 | 3 | enum | Trạng thái khóa RL1 |
| `ELK_RL2_LockingStatus` | 6 | 2 | 0 | 3 | enum | Trạng thái khóa RL2 |
| `ELK_RR1_LockingStatus` | 8 | 2 | 0 | 3 | enum | Trạng thái khóa RR1 |

---

### 5.5 HB — Haptic Belt (Dây an toàn rung cảnh báo)

#### 5.5.1 SBS_HB_Status (ID: 410) — Activation Level

**Sender:** CAR_PC → PANTHER

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `HB_FL_ActivationLevel` | 0 | 3 | 0 | 7 | level | Mức rung ghế FL (Level 1–3) |
| `HB_FR_ActivationLevel` | 3 | 3 | 0 | 7 | level | Mức rung ghế FR (Level 1–4) |
| `HB_RL1_ActivationLevel` | 6 | 3 | 0 | 7 | level | Mức rung ghế RL1 (Level 1–5) |
| `HB_RL2_ActivationLevel` | 9 | 3 | 0 | 7 | level | Mức rung ghế RL2 (Level 1–6) |
| `HB_RR1_ActivationLevel` | 12 | 3 | 0 | 7 | level | Mức rung ghế RR1 (Level 1–7) |

#### 5.5.2 SBS_HB_GenericCmd (ID: 131)

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `HB_ActivationSync` | 0 | 1 | 0 | 1 | bool | Đồng bộ tất cả mức rung |

#### 5.5.3 SBS_HB_TargetTemp (ID: 133)

**Mô tả:** Nhiệt độ mục tiêu cho hệ thống sưởi/làm mát tích hợp dây an toàn.

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `HB_ManualTargetTemp` | 32 | 32 | 0 | 4294967295 | °C | Nhiệt độ đặt thủ công từ HMI |
| `HB_DynamicTargetTemp` | 0 | 32 | 0 | 4294967295 | °C | Nhiệt độ tính toán từ thuật toán HB Boosting |

---

### 5.6 ISB — Illuminated Seatbelt Buckle (Khóa dây có đèn LED)

Mỗi ghế có một message request riêng. Cấu trúc giống nhau cho FL/FR/RL1/RL2/RR1.

#### SBS_ISB_FL_Request (ID: 160)

**Sender:** CAR_PC → PANTHER, PUMA_FL

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `ISB_FL_ColorGreen_byte` | 0 | 8 | 0 | 255 | 0–255 | Kênh màu Xanh lá (Green) |
| `ISB_FL_ColorBlue_byte` | 8 | 8 | 0 | 255 | 0–255 | Kênh màu Xanh dương (Blue) |
| `ISB_FL_ColorRed_byte` | 16 | 8 | 0 | 255 | 0–255 | Kênh màu Đỏ (Red) |
| `ISB_FL_Intensity_perc` | 24 | 7 | 0 | 127 | % | Cường độ sáng (0–127 ≈ 0–100%) |
| `ISB_FL_Normalization_bool` | 31 | 1 | 0 | 1 | bool | Chuẩn hóa màu |
| `ISB_FL_Transitionspeed_nibble` | 32 | 4 | 0 | 15 | level | Tốc độ chuyển màu |
| `ISB_FL_GroupOrModule_bool` | 36 | 1 | 0 | 1 | bool | 0=module đơn, 1=group |
| `ISB_FL_AddressByte0_byte` | 37 | 8 | 0 | 255 | addr | Địa chỉ byte 0 |
| `ISB_FL_AddressByte1_byte` | 45 | 8 | 0 | 255 | addr | Địa chỉ byte 1 |

> Cấu trúc tương tự cho FR (ID: 161), RL1 (ID: 162), RL2 (ID: 163), RR1 (ID: 164).

---

### 5.7 WMS — Webbing Movement Sensor

**Mô tả:** Cảm biến đo dịch chuyển dây đai, suy ra từ góc quay spool ACR.

#### SBS_WMS_FL_Response (ID: 400)

**Sender:** PANTHER, PUMA_FL → CAR_PC

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `WMS_FL_WebbingMovement_mm` | 0 | 13 | 0 | 8191 | mm | Chiều dài dây đai đã nhả ra |
| `WMS_FL_SpoolAngle_deg` | 13 | 14 | 0 | 16383 | deg | Góc quay spool |
| `WMS_FL_SensorStatus_bool` | 27 | 3 | 0 | 7 | enum | Trạng thái cảm biến WMS |

> Response tương tự cho FR (ID: 401), RL1 (ID: 402), RL2 (ID: 403), RR1 (ID: 404).

**Giải thích `WebbingMovement_mm`:**
- `0` = Dây đai không nhả (belt fully retracted)
- `8191` = Mức nhả tối đa (13-bit unsigned, ~8.2 m nếu scale 1 mm/LSB)
- Giá trị tăng khi dây đai nhả ra, giảm khi cuộn lại

---

## 6. Nhóm STS — Seat Position Status

### 6.1 STS_SPS_FL_SeatPosition (ID: 181)

**Mô tả:** Vị trí các thành phần ghế FL.  
**Sender:** PANTHER → CAR_PC, SIMI

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `SPS_FL_SeatDirectionX` | 0 | 12 | 0 | 4095 | mm | Vị trí ghế theo trục X (trước/sau) |
| `SPS_FL_SeatDirectionZ` | 12 | 12 | 0 | 4095 | mm | Vị trí ghế theo trục Z (lên/xuống) |
| `SPS_FL_SeatBackRestPosition` | 24 | 12 | 0 | 4095 | deg | Góc tựa lưng |
| `SPS_FL_FootRestPosition` | 36 | 12 | 0 | 4095 | deg | Góc gác chân |
| `SPS_FL_HeadRestPosition` | 48 | 12 | 0 | 4095 | mm | Vị trí tựa đầu |

### 6.2 STS_SPS_FR_SeatPosition (ID: 182)

**Mô tả:** Vị trí các thành phần ghế FR.  
**Sender:** PANTHER → CAR_PC, SIMI

| Signal | Bit pos | Bits | Min | Max | Đơn vị | Mô tả |
|--------|---------|------|-----|-----|---------|-------|
| `SPS_FR_SeatDirectionX` | 0 | 12 | 0 | 4095 | mm | Vị trí ghế theo trục X |
| `SPS_FR_SeatDirectionZ` | 12 | 12 | 0 | 4095 | mm | Vị trí ghế theo trục Z |
| `SPS_FR_SeatBackRestPosition` | 24 | 12 | 0 | 4095 | deg | Góc tựa lưng |
| `SPS_FR_FootRestPosition` | 36 | 12 | 0 | 4095 | deg | Góc gác chân |
| `SPS_FR_HeadRestPosition` | 48 | 12 | 0 | 4095 | mm | Vị trí tựa đầu |

---

## 7. Bảng tổng hợp tất cả signal theo đơn vị

| Đơn vị | Signals |
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

## 8. Quy ước mã hóa bit/signal trong DBC

Cú pháp signal trong DBC:

```
SG_ <TênSignal> : <BitStart>|<BitLen>@<ByteOrder><Signed> (<Factor>,<Offset>) [<Min>|<Max>] "<Unit>" <Receivers>
```

| Trường | Ý nghĩa |
|--------|---------|
| `BitStart` | Bit bắt đầu (LSB) trong frame CAN |
| `BitLen` | Số bit của signal |
| `@1` | Little-endian (Intel byte order) |
| `@0` | Big-endian (Motorola byte order) |
| `+` | Unsigned |
| `-` | Signed |
| `Factor` | Hệ số nhân để ra giá trị vật lý: `PhysVal = RawVal * Factor + Offset` |
| `Offset` | Giá trị offset |
| `[Min|Max]` | Khoảng giá trị vật lý hợp lệ |

**Ví dụ:**
```
SG_ WMS_FL_WebbingMovement_mm : 0|13@1+ (1,0) [0|8191] "" PANTHER,CAR_PC
```
→ Bit 0, dài 13 bit, little-endian, unsigned, factor=1, offset=0, range 0–8191 mm.

---

*Tài liệu được tạo tự động từ phân tích file DBC — 2026-06-29*
