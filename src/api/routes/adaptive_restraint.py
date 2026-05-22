# -*- coding: utf-8 -*-
"""API routes for adaptive restraint systems."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()
logger = logging.getLogger(__name__)

_DB_READY = False  # set to True only after _ensure_db() succeeds

# ---------------------------------------------------------------------------
# Paths relative to project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolve_paths() -> tuple[Path, Path]:
    """Read db_path and csv_path from config/system.json adaptive_restraint section."""
    _cfg = PROJECT_ROOT / "config" / "system.json"
    try:
        cfg = json.loads(_cfg.read_text(encoding="utf-8"))
        ar = cfg.get("adaptive_restraint", {})
        db_rel  = ar.get("db_path",  "db/adaptive_restraint_db/synthetic_data_out_gui.db")
        csv_rel = ar.get("csv_path", "db/adaptive_restraint_db/synthetic_data_out_gui.csv")
    except Exception:
        db_rel  = "db/adaptive_restraint_db/synthetic_data_out_gui.db"
        csv_rel = "db/adaptive_restraint_db/synthetic_data_out_gui.csv"
    return PROJECT_ROOT / db_rel, PROJECT_ROOT / csv_rel


DB_PATH, CSV_PATH = _resolve_paths()

def _ensure_db() -> bool:
    """Ensure the SQLite DB exists and is created from the CSV with proper indexes.

    Returns True on success, False when source data is unavailable (server keeps running).
    """
    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        if not CSV_PATH.exists():
            # Fallback check under adaptive_restraint_code
            alt_csv = PROJECT_ROOT / "adaptive_restraint_code" / "synthetic_data_out_gui.csv"
            if alt_csv.exists():
                import shutil
                shutil.copy(alt_csv, CSV_PATH)
            else:
                logger.warning(
                    "Adaptive restraint DB not built: CSV not found at '%s' or '%s'. "
                    "Update adaptive_restraint.db_path / csv_path in config/system.json.",
                    CSV_PATH, alt_csv,
                )
                return False

        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            # Skip first row (e.g., "Occupant Config,,,...")
            next(reader)
            # Second row has columns names
            headers = next(reader)

            rows = []
            for row in reader:
                if not row:
                    continue
                converted_row = []
                for idx, val in enumerate(row):
                    val = val.strip()
                    if idx in (1, 3):  # seat_position, velocity [km/h] — integers
                        try:
                            converted_row.append(int(float(val)))
                        except ValueError:
                            converted_row.append(None)
                    elif idx in (0, 2):  # weight, height — floats
                        try:
                            converted_row.append(float(val))
                        except ValueError:
                            converted_row.append(None)
                    elif idx in (4, 5, 6, 7, 8, 9):  # injury risk values (floats)
                        try:
                            converted_row.append(float(val))
                        except ValueError:
                            converted_row.append(None)
                    else:  # Seatbelt Component (string)
                        converted_row.append(val)
                rows.append(converted_row)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        col_definitions = []
        for i, header in enumerate(headers):
            if i in (1, 3):  # seat_position, velocity — INTEGER
                col_definitions.append(f'"{header}" INTEGER')
            elif i in (0, 2, 4, 5, 6, 7, 8, 9):  # weight, height, injury risks — REAL
                col_definitions.append(f'"{header}" REAL')
            else:  # Seatbelt Component — TEXT
                col_definitions.append(f'"{header}" TEXT')

        cursor.execute("DROP TABLE IF EXISTS crash_data")
        cursor.execute(f"CREATE TABLE crash_data ({', '.join(col_definitions)})")

        placeholders = ",".join(["?"] * len(headers))
        cursor.executemany(f"INSERT INTO crash_data VALUES ({placeholders})", rows)

        # Create indexes
        for idx_name, col in [
            ("idx_velocity", '"velocity [km/h]"'),
            ("idx_weight", "weight"),
            ("idx_height", "height"),
            ("idx_seat", "seat_position"),
            ("idx_seatbelt", '"Seatbelt Component"'),
        ]:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON crash_data ({col})")

        conn.commit()
        conn.close()

    return True


# Ensure database is accessible — failure is non-fatal (server keeps running)
try:
    _DB_READY = _ensure_db()
except Exception as _e:
    logger.warning("Adaptive restraint DB init failed: %s", _e)
    _DB_READY = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_unique(column: str) -> list[Any]:
    """Return sorted distinct values for the given column."""
    if not _DB_READY:
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT DISTINCT "{column}" FROM crash_data WHERE "{column}" IS NOT NULL ORDER BY "{column}"')
        return [row[0] for row in cursor.fetchall()]

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/available", summary="Get all available options for adaptive restraint filters")
async def get_available() -> dict[str, list[Any]]:
    """Get list of possible criteria values to populate the UI select elements."""
    return {
        "System": ["fusion", "camera", "non_adapt"],
        "Age": ["35y", "65y"],
        "Seatbelt": get_unique("Seatbelt Component"),
        "Velocity": get_unique("velocity [km/h]"),
        "Weight": get_unique("weight"),
        "Height": get_unique("height"),
        "Distance": get_unique("seat_position"),
    }

@router.get("/chart_info", summary="Get statistic and chart information for adaptive restraint systems")
async def get_chart_info(
    request: Request,
    System: list[str] = Query(None),
    Age: list[str] = Query(None),
    Seatbelt: list[str] = Query(None),
    Velocity: list[float] = Query(None),
    Weight: list[float] = Query(None),
    Height: list[float] = Query(None),
    Distance: list[float] = Query(None),
    RawData: bool = Query(True, description="Include raw_rows in response (up to 100 rows). Set false to reduce payload size."),
) -> dict[str, Any]:
    """
    Get detailed Box-plot statistics and raw values filtered by input parameters.
    Supports GET with query lists or parsing a JSON request body if present or needed.
    """
    if not _DB_READY:
        raise HTTPException(
            status_code=503,
            detail="Adaptive restraint database is not available. "
                   "Check db_path / csv_path in config/system.json under 'adaptive_restraint'.",
        )

    # 1. Resolve filter parameters with fallback defaults
    all_avail = await get_available()
    
    systems_sel = System if System else all_avail["System"]
    ages_sel = Age if Age else all_avail["Age"]
    seatbelt_sel = Seatbelt if Seatbelt else all_avail["Seatbelt"]
    velocity_sel = Velocity if Velocity else all_avail["Velocity"]
    weight_sel = Weight if Weight else all_avail["Weight"]
    height_sel = Height if Height else all_avail["Height"]
    distance_sel = Distance if Distance else all_avail["Distance"]

    # Cast to lists to be safe
    systems_sel = list(systems_sel)
    ages_sel = list(ages_sel)
    seatbelt_sel = list(seatbelt_sel)
    velocity_sel = [float(v) for v in velocity_sel]
    weight_sel = [float(w) for w in weight_sel]
    height_sel = [float(h) for h in height_sel]
    distance_sel = [float(d) for d in distance_sel]

    # 2. Build sql query
    # We query the SQLite and filter by velocity, weight, height, distance, seatbelt
    conditions = []
    params = []

    if velocity_sel:
        conditions.append(f'"velocity [km/h]" IN ({",".join(["?"] * len(velocity_sel))})')
        params.extend(velocity_sel)
    if weight_sel:
        _ph = ",".join(["?"] * len(weight_sel))
        conditions.append(f"weight IN ({_ph})")
        params.extend(weight_sel)
    if height_sel:
        _ph = ",".join(["?"] * len(height_sel))
        conditions.append(f"height IN ({_ph})")
        params.extend(height_sel)
    if distance_sel:
        _ph = ",".join(["?"] * len(distance_sel))
        conditions.append(f"seat_position IN ({_ph})")
        params.extend(distance_sel)
    if seatbelt_sel:
        conditions.append(f'"Seatbelt Component" IN ({",".join(["?"] * len(seatbelt_sel))})')
        params.extend(seatbelt_sel)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM crash_data {where_clause}"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        db_rows = cursor.fetchall()

    # 3. Compute stats for each requested System + Age combination
    datas = []
    for system in systems_sel:
        for age in ages_sel:
            col_name = f"injury_risk_{system}_{age}"
            
            # Fetch valid values for this column from matching rows
            values = []
            for r in db_rows:
                try:
                    val = r[col_name]
                    if val is not None:
                        # Values in database are ratio (e.g. 0.0031).
                        # Let's return them as percentages or keep raw.
                        # The streamlit app converts to percentage (val * 100), but let's keep exact raw/percentage
                        # Let's provide raw/processed floats as matches original SQLite data.
                        values.append(float(val))
                except (IndexError, KeyError, ValueError, TypeError):
                    continue

            # Calculate box plot stats
            if values:
                vals_arr = np.array(values)
                min_val = float(np.min(vals_arr))
                max_val = float(np.max(vals_arr))
                median = float(np.median(vals_arr))
                q1 = float(np.percentile(vals_arr, 25))
                q3 = float(np.percentile(vals_arr, 75))
                iqr = q3 - q1
                # Standard box plot fences
                lower_fence = float(np.max([min_val, q1 - 1.5 * iqr]))
                upper_fence = float(np.min([max_val, q3 + 1.5 * iqr]))
            else:
                min_val = 0.0
                max_val = 0.0
                median = 0.0
                q1 = 0.0
                q3 = 0.0
                lower_fence = 0.0
                upper_fence = 0.0

            datas.append({
                col_name: {
                    "values": values,
                    "max": max_val,
                    "min": min_val,
                    "upper fence": upper_fence,
                    "q3": q3,
                    "median": median,
                    "q1": q1,
                    "lower fence": lower_fence,
                }
            })

    result: dict[str, Any] = {
        "controls": {
            "System": systems_sel,
            "Age": ages_sel,
            "Seatbelt": seatbelt_sel,
            "Velocity": velocity_sel,
            "Weight": weight_sel,
            "Height": height_sel,
            "Distance": distance_sel,
            "RawData": RawData,
        },
        "datas": datas,
    }
    if RawData:
        result["raw_rows"] = [dict(r) for r in db_rows[:100]]
    return result
