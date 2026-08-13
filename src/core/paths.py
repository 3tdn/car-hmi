from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
TMP_DIR = PROJECT_ROOT / "tmp"
LOG_DIR = PROJECT_ROOT / "logs"

RELATIVE_CONFIG_DIR = "config"
RELATIVE_DATA_DIR = "data"
RELATIVE_TMP_DIR = "tmp"
RELATIVE_LOG_DIR = "logs"
RELATIVE_BACKUP_DIR = f"{RELATIVE_CONFIG_DIR}/backups"
RELATIVE_SQLITE_DB_PATH = f"{RELATIVE_DATA_DIR}/signals.db"
RELATIVE_LOG_FILE_PATH = f"{RELATIVE_LOG_DIR}/can-hmi.log"
RELATIVE_CAN_JSON_PATH = f"{RELATIVE_CONFIG_DIR}/can.json"
RELATIVE_CAN0_JSON_PATH = f"{RELATIVE_CONFIG_DIR}/can0.json"
RELATIVE_SIGNAL_STD_NAME_PATH = f"{RELATIVE_CONFIG_DIR}/signal_std_name.json"
RELATIVE_ADAPTIVE_RESTRAINT_DB_PATH = "db/adaptive_restraint_db/synthetic_data_out_gui.db"
RELATIVE_ADAPTIVE_RESTRAINT_CSV_PATH = "db/adaptive_restraint_db/synthetic_data_out_gui.csv"

DEFAULT_CONFIG_PATH = CONFIG_DIR / "system.json"
DEFAULT_ALARMS_PATH = CONFIG_DIR / "alarms.json"
DEFAULT_PROFILES_PATH = CONFIG_DIR / "profiles.json"
DEFAULT_BACKUP_DIR = CONFIG_DIR / "backups"
DEFAULT_DBC_WORK_DIR = TMP_DIR / "dbc_jobs"
DEFAULT_LOG_FILE_PATH = LOG_DIR / "can-hmi.log"
DEFAULT_SQLITE_DB_PATH = DATA_DIR / "signals.db"
DEFAULT_CAN_JSON_PATH = CONFIG_DIR / "can.json"
DEFAULT_CAN0_JSON_PATH = CONFIG_DIR / "can0.json"
DEFAULT_SIGNAL_STD_NAME_PATH = CONFIG_DIR / "signal_std_name.json"
DEFAULT_ADAPTIVE_RESTRAINT_DB_PATH = PROJECT_ROOT / "db" / "adaptive_restraint_db" / "synthetic_data_out_gui.db"
DEFAULT_ADAPTIVE_RESTRAINT_CSV_PATH = PROJECT_ROOT / "db" / "adaptive_restraint_db" / "synthetic_data_out_gui.csv"
