"""REST route for the restraint system – find the video matching the crash conditions.

Video filename naming schema:
    {percentile}p_{seat_position}_{velocity}_{seatbelt}.ext

    - percentile   : 5 | 50 | 95  (derived from occupant weight or OMS CAN signal)
    - seat_position: front | mid | rear  (derived from SPS seat travel distance X)
    - velocity     : 35 | 40 | 50 | 56  (crash severity in km/h)
    - seatbelt     : SLL | CLL | MSLL

    Example: 50p_mid_40_SLL.mp4

Seat position (front / mid / rear) definition
─────────────────────────────────────────────
Source: CAN signal SPS_FL_SeatDirectionX / SPS_FR_SeatDirectionX
        (message STS_SPS_FL/FR_SeatPosition, ID 181/182, transmitter PANTHER)

The signal reports seat travel in mm along the X-axis (fore/aft):
    0 mm   = frontmost position (closest to instrument panel)
    227 mm = rearmost position

Three video seat-position zones are defined (nearest-neighbour to reference
positions 0 mm, 113.5 mm and 227 mm):

    Zone   Reference   Window
    front  0 mm        0 mm  ≤ x <  56.75 mm
    mid    113.5 mm    56.75 mm ≤ x < 170.25 mm
    rear   227 mm      170.25 mm ≤ x ≤ 227 mm  (or beyond)

For a measured value that falls between two reference points always pick the
video whose reference is numerically closer (e.g. 100 mm → mid, 40 mm → front).

The HMI may supply the seat travel distance explicitly via the `seat_x_mm`
parameter; if omitted the backend reads it from the live CAN signal store.

Occupant percentile thresholds (weight-based):
    5%  : weight < 65 kg
    50% : 65 kg ≤ weight ≤ 90 kg
    95% : weight > 90 kg

Crash severity → velocity mapping (filename uses velocity directly):
    OLC33 → 56 km/h
    OLC26 → 50 km/h
    OLC18 → 40 km/h
    OLC16 → 35 km/h

Seatbelt system codes:
    SLL  – Switchable Load Limiter (2 force levels)
    CLL  – Constant Load Limiter  (1 force level)
    MSLL – Multi Switchable Load Limiter (multiple force levels)
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

router = APIRouter()

MEDIA_DIR = Path(__file__).resolve().parents[3] / "media"

# ---------------------------------------------------------------------------
# Video filename schema: {percentile}p_{seat_position}_{velocity}{kmh?}_{seatbelt}.ext
# velocity is the crash speed in km/h: 35 | 40 | 50 | 56 (with or without "kmh" suffix)
# seatbelt can be: SLL | CLL | MSLL | SLL_MSLL (combined SLL/MSLL)
# ---------------------------------------------------------------------------
_FILENAME_PATTERN = re.compile(
    r"^(?P<percentile>\d+)p_(?P<seat_position>\w+)_(?P<velocity>\d+)(?:kmh?)?_(?P<seatbelt>[\w_]+)\.\w+$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Weight → dummy percentile (per project definition)
# ---------------------------------------------------------------------------
def _weight_to_percentile(weight_kg: float) -> int:
    """Return 5, 50, or 95 based on occupant weight."""
    if weight_kg < 65.0:
        return 5
    if weight_kg <= 90.0:
        return 50
    return 95


# ---------------------------------------------------------------------------
# Crash severity: accepted inputs and canonical velocity values
# ---------------------------------------------------------------------------
# Valid velocity values used in filenames
_VALID_VELOCITIES = {35, 40, 50, 56}

# ---------------------------------------------------------------------------
# Seat → seat_position zone
# Only two seats are supported: FL (front-left) and FR (front-right)
# Both default to "mid" when no SPS X reading is available.
# NOTE: actual zone is resolved from SPS_SeatDirectionX reading (see below).
# ---------------------------------------------------------------------------
_SEAT_DEFAULT_ZONE: dict[str, str] = {
    "fl": "mid",
    "fr": "mid",
}

# ---------------------------------------------------------------------------
# Seat travel X (mm) → zone name (nearest-neighbour among reference positions)
#
# Reference positions and zone boundaries:
#   front  0 mm     →  [0,   56.75)
#   mid    113.5 mm →  [56.75, 170.25)
#   rear   227 mm   →  [170.25, ∞)
# ---------------------------------------------------------------------------
_SEAT_X_REFERENCES: list[tuple[float, str]] = [
    (0.0,   "front"),
    (113.5, "mid"),
    (227.0, "rear"),
]

_SEAT_X_BOUNDARIES = [56.75, 170.25]   # thresholds between front/mid and mid/rear


def _seat_x_to_zone(seat_x_mm: float) -> str:
    """Map seat travel distance (mm) to a seat-position zone name.

    Zone boundaries (nearest-neighbour to reference positions 0, 113.5, 227 mm):
        front : 0 ≤ x < 56.75
        mid   : 56.75 ≤ x < 170.25
        rear  : x ≥ 170.25
    """
    if seat_x_mm < _SEAT_X_BOUNDARIES[0]:
        return "front"
    if seat_x_mm < _SEAT_X_BOUNDARIES[1]:
        return "mid"
    return "rear"


# CAN signal names for seat position (PANTHER → CAR_PC/SIMI)
_CAN_FL_SEAT_X = "SPS_FL_SeatDirectionX"
_CAN_FR_SEAT_X = "SPS_FR_SeatDirectionX"

# ---------------------------------------------------------------------------
# OMS OccupantClassification CAN signal value → percentile
# DBC comment: "25%, 50%, 95% Occupant" (3-bit field)
# Assumed encoding: 1 → 5th %, 2 → 50th %, 3 → 95th %
# ---------------------------------------------------------------------------
_OMS_CLASS_TO_PERCENTILE: dict[int, int] = {
    1: 5,
    2: 50,
    3: 95,
}

# CAN signal names from SIMI (OMS)
_CAN_FL_CLASSIFICATION = "OMS_FL_OccupantClassification"
_CAN_FR_CLASSIFICATION = "OMS_FR_OccupantClassification"
_CAN_FL_OOP = "OMS_FL_OutOfPosition"
_CAN_FR_OOP = "OMS_FR_OutOfPosition"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_video_filename(filename: str) -> dict | None:
    """Extract metadata from a video filename matching the naming schema."""
    m = _FILENAME_PATTERN.match(filename)
    if not m:
        return None
    velocity = int(m.group("velocity"))
    if velocity not in _VALID_VELOCITIES:
        return None  # skip files whose velocity token is not a known crash speed
    return {
        "filename": filename,
        "percentile": int(m.group("percentile")),
        "seat_position": m.group("seat_position").lower(),
        "velocity": velocity,
        "seatbelt": m.group("seatbelt").upper(),
    }


def _score_match(
    video: dict,
    target_percentile: int,
    target_velocity: int,
    target_seatbelt: str,
    preferred_position: str,
) -> float:
    """Score how well a video matches the query. Higher = better match.

    Scoring breakdown:
        +3.0  seatbelt system exact match (including SLL_MSLL matching SLL/MSLL)
        +2.0  percentile exact match
        +1.5  velocity exact match (required - no partial score)
        +1.0  seat_position zone match
    """
    score = 0.0

    # Check seatbelt match: exact match OR video has SLL_MSLL and target is SLL/MSLL
    video_seatbelt = video["seatbelt"].upper()
    target_seatbelt_upper = target_seatbelt.upper()
    
    if video_seatbelt == target_seatbelt_upper:
        score += 3.0
    elif video_seatbelt == "SLL_MSLL" and target_seatbelt_upper in {"SLL", "MSLL"}:
        score += 3.0

    if video["percentile"] == target_percentile:
        score += 2.0

    # Velocity must be exact match (no partial scoring)
    if video["velocity"] == target_velocity:
        score += 1.5

    if video["seat_position"] == preferred_position:
        score += 1.0

    return round(score, 3)


async def _read_can_signal(store, signal_name: str) -> float | None:
    """Read a single CAN signal value from the signal store (non-blocking)."""
    try:
        snapshot = await store.get_snapshot()
        sv = snapshot.get(signal_name)
        return sv.value if sv is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/match", summary="Find best-matching restraint video for crash conditions")
async def match_restraint(
    request: Request,
    weight: float = Query(..., description="Occupant weight in kg (derives percentile: <65→5%, 65-90→50%, >90→95%)"),
    height: float = Query(..., description="Occupant height in cm (recorded for reference)"),
    crash_severity: int = Query(..., description="Crash velocity in km/h (35, 40, 50, or 56)"),
    seatbelt_system: str = Query(..., description="Seatbelt system: SLL | CLL | MSLL"),
    seat: str = Query("fl", description="Seat identifier: fl (front-left) | fr (front-right)"),
    seat_x_mm: float | None = Query(
        None,
        description=(
            "Seat travel distance in mm from SPS_SeatDirectionX "
            "(0 mm = frontmost / closest to instrument panel, 227 mm = rearmost). "
            "If omitted the backend reads the live CAN signal. "
            "Zone mapping: front 0–56.75 mm, mid 56.75–170.25 mm, rear ≥170.25 mm."
        ),
    ),
):
    """Return the best-matching restraint video for the given crash parameters.

    Resolution order for seat-position zone:
    1. `seat_x_mm` query parameter (explicit value from HMI)
    2. Live CAN signal SPS_FL/FR_SeatDirectionX from signal store
    3. Fallback default from seat identifier (fl / fr → mid)

    Resolution order for occupant percentile:
    1. Live CAN signal OMS_FL/FR_OccupantClassification (sensor wins)
    2. Weight-derived percentile from `weight` parameter
    """
    if not MEDIA_DIR.exists():
        raise HTTPException(status_code=500, detail="Media directory not found")

    # ── 1. Derive percentile from weight ────────────────────────────────────
    derived_percentile = _weight_to_percentile(weight)

    # ── 2. Validate target velocity ───────────────────────────────────────────
    # Accept direct velocity values (35, 40, 50, 56 km/h)
    target_velocity = int(crash_severity)
    if target_velocity not in _VALID_VELOCITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Velocity {target_velocity} km/h is not supported. "
                   f"Use one of: {sorted(_VALID_VELOCITIES)} km/h.",
        )

    # ── 3. Validate seatbelt_system ──────────────────────────────────────────
    seatbelt_upper = seatbelt_system.strip().upper()
    if seatbelt_upper not in {"SLL", "CLL", "MSLL"}:
        raise HTTPException(
            status_code=422,
            detail=f"seatbelt_system '{seatbelt_system}' invalid. Use SLL, CLL, or MSLL.",
        )

    # ── 4. Read live CAN signals ──────────────────────────────────────────────
    store = getattr(request.app.state, "store", None)
    seat_lower = seat.strip().lower()
    if seat_lower not in ("fl", "fr"):
        raise HTTPException(
            status_code=422,
            detail=f"seat '{seat}' invalid. Use 'fl' (front-left) or 'fr' (front-right).",
        )

    # Choose FL or FR CAN signals based on seat identifier
    if seat_lower == "fl":
        class_signal  = _CAN_FL_CLASSIFICATION
        oop_signal    = _CAN_FL_OOP
        seat_x_signal = _CAN_FL_SEAT_X
    else:
        class_signal  = _CAN_FR_CLASSIFICATION
        oop_signal    = _CAN_FR_OOP
        seat_x_signal = _CAN_FR_SEAT_X

    can_classification:   float | None = None
    can_out_of_position:  float | None = None
    can_seat_x:           float | None = None
    if store is not None:
        can_classification  = await _read_can_signal(store, class_signal)
        can_out_of_position = await _read_can_signal(store, oop_signal)
        can_seat_x          = await _read_can_signal(store, seat_x_signal)

    # ── 5. Resolve seat-position zone ────────────────────────────────────────
    # Priority: explicit param > CAN signal > default from seat identifier
    resolved_seat_x: float | None = seat_x_mm if seat_x_mm is not None else can_seat_x
    seat_x_source: str

    if seat_x_mm is not None:
        preferred_position = _seat_x_to_zone(seat_x_mm)
        seat_x_source = "hmi_param"
    elif can_seat_x is not None:
        preferred_position = _seat_x_to_zone(can_seat_x)
        seat_x_source = "can_signal"
    else:
        preferred_position = _SEAT_DEFAULT_ZONE.get(seat_lower, "mid")
        seat_x_source = "default"

    # ── 6. Resolve occupant percentile (CAN wins over weight-derived) ────────
    effective_percentile = derived_percentile
    can_percentile: int | None = None
    if can_classification is not None:
        can_percentile = _OMS_CLASS_TO_PERCENTILE.get(int(can_classification))
        if can_percentile is not None:
            effective_percentile = can_percentile

    out_of_position = bool(can_out_of_position and int(can_out_of_position) != 0)

    # ── 7. Scan media directory and score candidates ─────────────────────────
    videos: list[dict] = []
    for f in MEDIA_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".mp4", ".avi", ".mkv", ".webm"}:
            meta = _parse_video_filename(f.name)
            if meta:
                videos.append(meta)

    _ctx = {
        "weight_kg":            weight,
        "height_cm":            height,
        "derived_percentile":   derived_percentile,
        "effective_percentile": effective_percentile,
        "can_percentile":       can_percentile,
        "target_velocity_kmh":  target_velocity,
        "seatbelt_system":      seatbelt_upper,
        "seat":                 seat,
        "seat_x_mm":            resolved_seat_x,
        "seat_x_source":        seat_x_source,
        "seat_position_zone":   preferred_position,
        "out_of_position":      out_of_position,
        "candidates_found":     len(videos),
    }

    if not videos:
        return {"matched": False, "video": None, "score": 0, "context": _ctx}

    best_video = None
    best_score = -1.0
    for v in videos:
        s = _score_match(v, effective_percentile, target_velocity, seatbelt_upper, preferred_position)
        if s > best_score:
            best_score = s
            best_video = v

    if best_video is None or best_score <= 0:
        return {"matched": False, "video": None, "score": 0, "context": _ctx}

    return {
        "matched": True,
        "video": {
            "filename":      best_video["filename"],
            "percentile":    best_video["percentile"],
            "seat_position": best_video["seat_position"],
            "velocity_kmh":  best_video["velocity"],
            "seatbelt":      best_video["seatbelt"],
            "url":           f"/api/restraints/video/{best_video['filename']}",
        },
        "score": best_score,
        "context": _ctx,
    }


@router.get("/video/{filename}", summary="Stream a video file from the media directory")
async def get_video(filename: str):
    """Serve a video file from the media directory."""
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = MEDIA_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    suffix = file_path.suffix.lower()
    media_types = {
        ".mp4":  "video/mp4",
        ".avi":  "video/x-msvideo",
        ".mkv":  "video/x-matroska",
        ".webm": "video/webm",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
    )
