"""Project-local paths used by system configuration management."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "system.json"
DEFAULT_CONFIG_TEMPLATE_PATH = CONFIG_DIR / "system_bk.json"
DEFAULT_CONFIG_FIELDS_PATH = CONFIG_DIR / "system.fields.json"
DEFAULT_CONFIG_BACKUP_DIR = CONFIG_DIR / "backups"
