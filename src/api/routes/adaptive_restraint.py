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
    """Return sorted distinct values for *column*.

    Uses the in-memory numpy cache when available (fast), falls back to a
    SQLite query only before the cache is loaded (first request).
    """
    if _np_cache["columns"] is not None:
        col_arr = _np_cache["columns"].get(column)
        if col_arr is not None:
            if col_arr.dtype == object:
                return sorted({v for v in col_arr.tolist() if v})
            elif np.issubdtype(col_arr.dtype, np.integer):
                return np.unique(col_arr[col_arr != -1]).tolist()
            else:  # float
                return np.unique(col_arr[~np.isnan(col_arr)]).tolist()
    if not _DB_READY:
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT DISTINCT "{column}" FROM crash_data'
            f' WHERE "{column}" IS NOT NULL ORDER BY "{column}"'
        )
        return [row[0] for row in cursor.fetchall()]

# ---------------------------------------------------------------------------
# NumPy columnar cache — vectorized filtering, no Python-level row iteration
# ---------------------------------------------------------------------------
_np_cache: dict = {
    "columns":     None,   # dict[str, np.ndarray] — one array per DB column
    "params_norm": None,   # frozenset-dict of last request params (for hit detection)
    "mask":        None,   # np.ndarray[bool]      — current filter mask
}


def _to_py(v: Any) -> Any:
    """Convert a numpy scalar/string to a plain Python type for JSON output."""
    if isinstance(v, np.floating) and np.isnan(v):       return None
    if isinstance(v, (int, np.integer)) and v == -1:     return None
    if isinstance(v, np.generic):                        return v.item()
    return v


def _normalize_params(params: dict) -> dict:
    """Return params as a dict of frozensets (used for exact-match cache hit check)."""
    return {
        "velocity_sel": frozenset(int(float(v)) for v in params.get("velocity_sel", [])),
        "weight_sel":   frozenset(float(v)      for v in params.get("weight_sel",   [])),
        "height_sel":   frozenset(float(v)      for v in params.get("height_sel",   [])),
        "distance_sel": frozenset(int(float(v)) for v in params.get("distance_sel", [])),
        "seatbelt_sel": frozenset(str(v)        for v in params.get("seatbelt_sel", [])),
    }


_NPZ_PATH     = DB_PATH.parent / "synthetic_data_out_gui.npz"
_NPZ_MAP_PATH = DB_PATH.parent / "synthetic_data_out_gui_col_map.json"

# Characters that are illegal in numpy .npz key names
_NPZ_KEY_TR = str.maketrans({" ": "_", "[": "", "]": "", "/": "_"})


def _col_to_npz_key(col: str) -> str:
    return col.translate(_NPZ_KEY_TR)


def _save_npz(columns: dict[str, np.ndarray]) -> None:
    """Persist columnar numpy arrays to .npz + a JSON key-map (one-time operation)."""
    import json as _json
    key_map = {_col_to_npz_key(c): c for c in columns}
    np.savez_compressed(_NPZ_PATH, **{_col_to_npz_key(c): v for c, v in columns.items()})
    _NPZ_MAP_PATH.write_text(_json.dumps(key_map, indent=2), encoding="utf-8")
    logger.info("Adaptive restraint .npz cache saved → %s (%.1f MB)",
                _NPZ_PATH, _NPZ_PATH.stat().st_size / 1024 / 1024)


def _load_npz() -> dict[str, np.ndarray]:
    """Load columnar arrays from .npz (~140 ms vs ~2600 ms from SQLite)."""
    import json as _json
    key_map: dict[str, str] = _json.loads(_NPZ_MAP_PATH.read_text(encoding="utf-8"))
    data = np.load(_NPZ_PATH, allow_pickle=True)
    return {key_map[k]: data[k] for k in data.files}


def _npz_is_fresh() -> bool:
    """Return True when the .npz exists AND is newer than the SQLite DB."""
    return (
        _NPZ_PATH.exists()
        and _NPZ_MAP_PATH.exists()
        and _NPZ_PATH.stat().st_mtime >= DB_PATH.stat().st_mtime
    )


def _load_np_data() -> None:
    """Load crash_data once into per-column numpy arrays (cold start).

    Fast path  (~140 ms): loads from a pre-built .npz binary cache.
    Slow path (~2600 ms): reads SQLite, builds arrays, then writes .npz for
                          next time.

    Integer columns  → int32   (velocity, seat_position)
    Float columns    → float64 (weight, height, injury_risk_*)
    String columns   → object  (Seatbelt Component)
    NULL values      → -1 (int) / NaN (float) / "" (str)
    """
    # ── fast path: .npz cache ────────────────────────────────────────────────
    if _npz_is_fresh():
        logger.info("Loading adaptive restraint data from .npz cache…")
        _np_cache["columns"] = _load_npz()
        return

    # ── slow path: read SQLite, then cache to .npz ───────────────────────────
    logger.info("Cold-loading adaptive restraint data from SQLite (first run)…")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        raw = conn.execute("SELECT * FROM crash_data").fetchall()

    if not raw:
        _np_cache["columns"] = {}
        return

    col_names_sql = raw[0].keys()
    columns: dict[str, np.ndarray] = {}

    for col in col_names_sql:
        vals = [r[col] for r in raw]
        if col in ("velocity [km/h]", "seat_position"):
            columns[col] = np.array(
                [int(v) if v is not None else -1 for v in vals], dtype=np.int32
            )
        elif col == "Seatbelt Component":
            columns[col] = np.array([v or "" for v in vals], dtype=object)
        else:  # weight, height, injury_risk_* — all float
            columns[col] = np.array(
                [v if v is not None else np.nan for v in vals], dtype=np.float64
            )

    _np_cache["columns"] = columns

    # Save .npz so the next server start uses the fast path
    try:
        _save_npz(columns)
    except Exception as exc:
        logger.warning("Could not save .npz cache: %s", exc)


def _build_np_mask(params: dict) -> np.ndarray:
    """Return a boolean mask over all rows matching *params* (vectorized np.isin)."""
    cols = _np_cache["columns"]
    n    = len(next(iter(cols.values())))
    mask = np.ones(n, dtype=bool)

    if (vs := params.get("velocity_sel")):
        mask &= np.isin(cols["velocity [km/h]"],
                        np.array([int(float(v)) for v in vs], dtype=np.int32))
    if (ws := params.get("weight_sel")):
        mask &= np.isin(cols["weight"],
                        np.array([float(v) for v in ws], dtype=np.float64))
    if (hs := params.get("height_sel")):
        mask &= np.isin(cols["height"],
                        np.array([float(v) for v in hs], dtype=np.float64))
    if (ds := params.get("distance_sel")):
        mask &= np.isin(cols["seat_position"],
                        np.array([int(float(v)) for v in ds], dtype=np.int32))
    if (sbs := params.get("seatbelt_sel")):
        mask &= np.isin(cols["Seatbelt Component"],
                        np.array([str(v) for v in sbs], dtype=object))

    return mask


# Dimension map used by _compute_available_options
# (response_key, params_key, col_name, col_dtype)
_AVAIL_DIM_MAP = [
    ("Velocity", "velocity_sel", "velocity [km/h]",   "int"),
    ("Weight",   "weight_sel",   "weight",             "float"),
    ("Height",   "height_sel",   "height",             "float"),
    ("Distance", "distance_sel", "seat_position",      "int"),
    ("Seatbelt", "seatbelt_sel", "Seatbelt Component", "str"),
]


def _compute_available_options(params: dict) -> dict[str, list]:
    """Return per-dimension available values using cross-dimensional masks.

    For each dimension D, builds a mask from ALL other filters (excluding D),
    then returns the distinct values of column D that pass that mask.
    This implements faceted-search: the frontend can gray-out values that would
    yield zero results when combined with the current selection.
    """
    cols   = _np_cache["columns"]
    result: dict[str, list] = {}

    for out_key, param_key, col_name, col_dtype in _AVAIL_DIM_MAP:
        cross_params = {k: v for k, v in params.items() if k != param_key}
        cross_mask   = _build_np_mask(cross_params)
        col_arr      = cols[col_name][cross_mask]

        if col_dtype == "float":
            valid = col_arr[~np.isnan(col_arr)]
            result[out_key] = np.unique(valid).tolist()
        elif col_dtype == "int":
            result[out_key] = np.unique(col_arr).tolist()
        else:  # str / object
            valid = col_arr[col_arr != ""]
            result[out_key] = sorted(set(valid.tolist()))

    return result


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

    # 2. Build numpy boolean mask — vectorized np.isin, no Python-level row loop
    current_params = {
        "velocity_sel": velocity_sel,
        "weight_sel":   weight_sel,
        "height_sel":   height_sel,
        "distance_sel": distance_sel,
        "seatbelt_sel": seatbelt_sel,
    }

    if _np_cache["columns"] is None:
        _load_np_data()  # cold start: load all rows into columnar arrays (once)

    curr_norm = _normalize_params(current_params)
    if _np_cache["params_norm"] != curr_norm:   # miss → rebuild mask
        _np_cache["mask"]        = _build_np_mask(current_params)
        _np_cache["params_norm"] = curr_norm
    mask = _np_cache["mask"]

    # 3. Compute stats using numpy array slicing — no Python iteration over rows
    cols  = _np_cache["columns"]
    datas = []
    for system in systems_sel:
        for age in ages_sel:
            col_name = f"injury_risk_{system}_{age}"
            raw  = cols.get(col_name)
            if raw is None:
                vals = np.empty(0, dtype=np.float64)
            else:
                raw  = raw[mask]
                vals = raw[~np.isnan(raw)]

            if len(vals):
                q1, q3      = np.percentile(vals, [25, 75])
                iqr         = float(q3 - q1)
                min_val     = float(vals.min())
                max_val     = float(vals.max())
                lower_fence = float(max(min_val, q1 - 1.5 * iqr))
                upper_fence = float(min(max_val, q3 + 1.5 * iqr))
                median      = float(np.median(vals))
                q1          = float(q1)
                q3          = float(q3)
            else:
                min_val = max_val = median = q1 = q3 = lower_fence = upper_fence = 0.0

            datas.append({
                col_name: {
                    "values":      vals.tolist(),
                    "max":         max_val,
                    "min":         min_val,
                    "upper fence": upper_fence,
                    "q3":          q3,
                    "median":      median,
                    "q1":          q1,
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
        "available_options": _compute_available_options(current_params),
    }
    if RawData:
        indices   = np.where(mask)[0][:100]
        col_names = list(cols.keys())
        result["raw_rows"] = [
            {c: _to_py(cols[c][i]) for c in col_names}
            for i in indices
        ]
    return result
