"""Route REST cho hệ thống restraint – tìm video phù hợp với điều kiện va chạm."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter()

MEDIA_DIR = Path(__file__).resolve().parents[3] / "media"

# Regex để parse tên file video: {percentile}p_{seat_position}_{weight}_{seat_belt}.ext
_FILENAME_PATTERN = re.compile(
    r"^(?P<percentile>\d+)p_(?P<seat_position>\w+)_(?P<weight>\d+)_(?P<seat_belt>\w+)\.\w+$"
)

# Mapping seat → seat_position ưu tiên
_SEAT_POSITION_MAP: dict[str, str] = {
    "driver": "mid",
    "passenger": "mid",
    "rear_left": "rear",
    "rear_right": "rear",
    "rear_center": "rear",
}

# Crash pulse → percentile mặc định (OLC càng cao → percentile càng cao)
_CRASH_PULSE_PERCENTILE: dict[str, int] = {
    "OLC10": 5,
    "OLC12": 5,
    "OLC14": 25,
    "OLC16": 50,
    "OLC18": 50,
    "OLC20": 75,
    "OLC22": 95,
    "OLC24": 95,
}


def _parse_video_filename(filename: str) -> dict | None:
    """Trích xuất metadata từ tên file video."""
    m = _FILENAME_PATTERN.match(filename)
    if not m:
        return None
    return {
        "filename": filename,
        "percentile": int(m.group("percentile")),
        "seat_position": m.group("seat_position"),
        "weight": int(m.group("weight")),
        "seat_belt": m.group("seat_belt"),
    }


def _score_match(
    video: dict,
    seat_belt: str,
    preferred_position: str,
    target_percentile: int,
) -> float:
    """Tính điểm phù hợp giữa video và điều kiện truy vấn. Điểm càng cao càng khớp."""
    score = 0.0

    # seat_belt khớp chính xác: +2 điểm
    if video["seat_belt"].upper() == seat_belt.upper():
        score += 2.0

    # seat_position khớp: +1 điểm
    if video["seat_position"] == preferred_position:
        score += 1.0

    # percentile gần target: +0 đến +0.5 (càng gần càng cao)
    percentile_diff = abs(video["percentile"] - target_percentile)
    score += max(0, 0.5 - percentile_diff / 200.0)

    return round(score, 2)


@router.get("/match", summary="Tìm video restraint phù hợp với điều kiện va chạm")
async def match_restraint(
    seat: str = Query(..., description="Vị trí ghế: driver, passenger, rear_left, ..."),
    seat_belt: str = Query(..., description="Loại dây an toàn: SLL, SLR, ..."),
    crash_pulse: str = Query(..., description="Xung va chạm: OLC10, OLC18, ..."),
):
    """Trả về video restraint phù hợp nhất với các tham số đầu vào."""
    if not MEDIA_DIR.exists():
        raise HTTPException(status_code=500, detail="Media directory not found")

    # Xác định seat_position ưu tiên và target percentile
    preferred_position = _SEAT_POSITION_MAP.get(seat.lower(), "mid")
    target_percentile = _CRASH_PULSE_PERCENTILE.get(crash_pulse.upper(), 50)

    # Quét tất cả video trong thư mục media
    videos: list[dict] = []
    for f in MEDIA_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".mp4", ".avi", ".mkv", ".webm"}:
            meta = _parse_video_filename(f.name)
            if meta:
                videos.append(meta)

    if not videos:
        return {"matched": False, "video": None, "score": 0}

    # Tính score cho từng video và chọn video có score cao nhất
    best_video = None
    best_score = -1.0
    for v in videos:
        s = _score_match(v, seat_belt, preferred_position, target_percentile)
        if s > best_score:
            best_score = s
            best_video = v

    if best_video is None or best_score <= 0:
        return {"matched": False, "video": None, "score": 0}

    return {
        "matched": True,
        "video": {
            "filename": best_video["filename"],
            "percentile": best_video["percentile"],
            "seat_position": best_video["seat_position"],
            "weight": best_video["weight"],
            "seat_belt": best_video["seat_belt"],
            "url": f"/api/restraints/video/{best_video['filename']}",
        },
        "score": best_score,
    }


@router.get("/video/{filename}", summary="Stream video file từ thư mục media")
async def get_video(filename: str):
    """Phục vụ file video từ thư mục media."""
    # Ngăn path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = MEDIA_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    # Xác định media type
    suffix = file_path.suffix.lower()
    media_types = {
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
    )
