"""File-backed field policy and validation metadata for ``config/system.json``."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.core.paths import DEFAULT_CONFIG_FIELDS_PATH, PROJECT_ROOT


class ReloadLevel(StrEnum):
    LIVE = "live"
    REBOOT = "reboot"
    IMMUTABLE = "immutable"


class SettingMode(StrEnum):
    BASE = "base"
    EXPAND = "expand"


class ConfigFieldPolicyError(ValueError):
    """Raised when ``system.fields.json`` is malformed."""


@dataclass(frozen=True)
class ConfigFieldPolicy:
    path: str
    title: str
    value_type: str
    editable: bool
    setting_mode: SettingMode
    reload_level: ReloadLevel
    description: str
    examples: tuple[Any, ...]
    validation: dict[str, Any]
    ui: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": self.path,
            "title": self.title,
            "type": self.value_type,
            "reload_level": self.reload_level.value,
            "editable": self.editable,
            "setting_mode": self.setting_mode.value,
            "description": self.description,
            "ui": deepcopy(self.ui),
        }
        if self.examples:
            result["examples"] = deepcopy(list(self.examples))
        if self.validation:
            result["validation"] = deepcopy(self.validation)
        return result


_FIELD_KEYS = {
    "path",
    "title",
    "type",
    "editable",
    "setting_mode",
    "reload_level",
    "description",
    "examples",
    "validation",
    "ui",
}
_VALUE_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
_VALIDATION_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
    "enum",
    "items",
    "allowed_extensions",
    "must_exist",
    "must_parse_as",
}
_UI_KEYS = {"control", "directory", "accept", "sensitive"}
_UI_CONTROLS = {
    "text",
    "number",
    "checkbox",
    "json-textarea",
    "key-value",
    "select",
    "password",
    "file-path",
}
_MISSING = object()


def _validate_metadata(
    location: str,
    value_type: str,
    validation: dict[str, Any],
    ui: dict[str, Any],
) -> None:
    unknown_validation = sorted(set(validation) - _VALIDATION_KEYS)
    if unknown_validation:
        raise ConfigFieldPolicyError(
            f"{location}.validation has unsupported key(s): {', '.join(unknown_validation)}"
        )
    unknown_ui = sorted(set(ui) - _UI_KEYS)
    if unknown_ui:
        raise ConfigFieldPolicyError(
            f"{location}.ui has unsupported key(s): {', '.join(unknown_ui)}"
        )

    enum = validation.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ConfigFieldPolicyError(f"{location}.validation.enum must be a non-empty array")
    if isinstance(enum, list):
        if any(enum[index] in enum[:index] for index in range(len(enum))):
            raise ConfigFieldPolicyError(f"{location}.validation.enum must contain unique values")
        invalid_enum_values = [value for value in enum if not _matches_type(value, value_type)]
        if invalid_enum_values:
            raise ConfigFieldPolicyError(
                f"{location}.validation.enum values must match field type {value_type}"
            )
    extensions = validation.get("allowed_extensions")
    if extensions is not None and (
        not isinstance(extensions, list)
        or not extensions
        or any(not isinstance(item, str) or not item.startswith(".") for item in extensions)
    ):
        raise ConfigFieldPolicyError(
            f"{location}.validation.allowed_extensions must contain file suffix strings"
        )
    must_exist = validation.get("must_exist")
    if must_exist is not None and not isinstance(must_exist, bool):
        raise ConfigFieldPolicyError(f"{location}.validation.must_exist must be boolean")
    parser = validation.get("must_parse_as")
    if parser is not None and parser != "dbc":
        raise ConfigFieldPolicyError(f"{location}.validation.must_parse_as is unsupported")
    pattern = validation.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ConfigFieldPolicyError(f"{location}.validation.pattern must be a string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigFieldPolicyError(
                f"{location}.validation.pattern is invalid: {exc}"
            ) from exc

    control = ui.get("control")
    if control is not None and control not in _UI_CONTROLS:
        raise ConfigFieldPolicyError(f"{location}.ui.control is unsupported")
    if "sensitive" in ui and not isinstance(ui["sensitive"], bool):
        raise ConfigFieldPolicyError(f"{location}.ui.sensitive must be boolean")


def load_field_policies(
    path: str | Path = DEFAULT_CONFIG_FIELDS_PATH,
) -> tuple[ConfigFieldPolicy, ...]:
    """Load and structurally validate the fixed field-definition document."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigFieldPolicyError(f"Cannot load field policy '{source}': {exc}") from exc

    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ConfigFieldPolicyError("system.fields.json must use schema_version 1")
    raw_fields = document.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ConfigFieldPolicyError("system.fields.json fields must be a non-empty array")

    policies: list[ConfigFieldPolicy] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_fields):
        location = f"fields[{index}]"
        if not isinstance(raw, dict):
            raise ConfigFieldPolicyError(f"{location} must be an object")
        unknown = sorted(set(raw) - _FIELD_KEYS)
        if unknown:
            raise ConfigFieldPolicyError(f"{location} has unsupported key(s): {', '.join(unknown)}")

        field_path = raw.get("path")
        if not isinstance(field_path, str) or not field_path:
            raise ConfigFieldPolicyError(f"{location}.path must be a non-empty string")
        if field_path in seen:
            raise ConfigFieldPolicyError(f"Duplicate field policy path: {field_path}")
        seen.add(field_path)

        title = raw.get("title")
        description = raw.get("description")
        value_type = raw.get("type")
        if not isinstance(title, str) or not title:
            raise ConfigFieldPolicyError(f"{location}.title must be a non-empty string")
        if not isinstance(description, str) or not description:
            raise ConfigFieldPolicyError(f"{location}.description must be a non-empty string")
        if not isinstance(value_type, str) or value_type not in _VALUE_TYPES:
            raise ConfigFieldPolicyError(
                f"{location}.type must be one of {', '.join(sorted(_VALUE_TYPES))}"
            )
        try:
            reload_level = ReloadLevel(raw.get("reload_level"))
        except (TypeError, ValueError) as exc:
            raise ConfigFieldPolicyError(
                f"{location}.reload_level must be live, reboot, or immutable"
            ) from exc

        editable = raw.get("editable", reload_level != ReloadLevel.IMMUTABLE)
        if not isinstance(editable, bool):
            raise ConfigFieldPolicyError(f"{location}.editable must be boolean")
        if reload_level == ReloadLevel.IMMUTABLE and editable:
            raise ConfigFieldPolicyError(
                f"{location}.editable cannot be true when reload_level is immutable"
            )

        try:
            setting_mode = SettingMode(raw.get("setting_mode", SettingMode.EXPAND.value))
        except (TypeError, ValueError) as exc:
            raise ConfigFieldPolicyError(f"{location}.setting_mode must be base or expand") from exc

        examples = raw.get("examples", [])
        validation = raw.get("validation", {})
        ui = raw.get("ui", {})
        if not isinstance(examples, list):
            raise ConfigFieldPolicyError(f"{location}.examples must be an array")
        if not isinstance(validation, dict):
            raise ConfigFieldPolicyError(f"{location}.validation must be an object")
        if not isinstance(ui, dict):
            raise ConfigFieldPolicyError(f"{location}.ui must be an object")
        _validate_metadata(location, value_type, validation, ui)

        policies.append(
            ConfigFieldPolicy(
                path=field_path,
                title=title,
                value_type=value_type,
                editable=editable,
                setting_mode=setting_mode,
                reload_level=reload_level,
                description=description,
                examples=tuple(deepcopy(examples)),
                validation=deepcopy(validation),
                ui=deepcopy(ui),
            )
        )
    return tuple(policies)


def match_policy(path: str) -> ConfigFieldPolicy | None:
    segments = path.split(".") if path else []
    for item in FIELD_POLICIES:
        pattern = item.path.split(".") if item.path else []
        if len(pattern) != len(segments):
            continue
        if all(
            expected == "*" or expected == actual
            for expected, actual in zip(pattern, segments, strict=True)
        ):
            return item
    return None


def diff_paths(old: Any, new: Any, prefix: str = "") -> list[str]:
    """Return changed leaf paths, including concrete list indexes."""
    if isinstance(old, dict) and isinstance(new, dict):
        paths: list[str] = []
        for key in sorted(set(old) | set(new)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in old or key not in new:
                paths.extend(_leaf_paths(new.get(key, old.get(key)), child))
            else:
                paths.extend(diff_paths(old[key], new[key], child))
        return paths
    if isinstance(old, list) and isinstance(new, list):
        paths = []
        for index in range(max(len(old), len(new))):
            child = f"{prefix}.{index}"
            if index >= len(old) or index >= len(new):
                paths.extend(_leaf_paths(new[index] if index < len(new) else old[index], child))
            else:
                paths.extend(diff_paths(old[index], new[index], child))
        return paths
    return [prefix] if old != new else []


def _leaf_paths(value: Any, prefix: str) -> list[str]:
    if isinstance(value, dict) and value:
        paths: list[str] = []
        for key, child_value in value.items():
            paths.extend(_leaf_paths(child_value, f"{prefix}.{key}"))
        return paths
    if isinstance(value, list) and value:
        paths = []
        for index, child_value in enumerate(value):
            paths.extend(_leaf_paths(child_value, f"{prefix}.{index}"))
        return paths
    return [prefix]


def _value_at_path(raw: Any, path: str) -> Any:
    value = raw
    for segment in path.split("."):
        if isinstance(value, list) and segment.isdigit():
            index = int(segment)
            if index >= len(value):
                return _MISSING
            value = value[index]
        elif isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            return _MISSING
    return value


def _matches_type(value: Any, value_type: str) -> bool:
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def _validate_value(path: str, value: Any, policy: ConfigFieldPolicy) -> list[str]:
    validation = policy.validation
    errors: list[str] = []
    if not _matches_type(value, policy.value_type):
        return [f"{path} must be {policy.value_type}"]

    allowed = validation.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        errors.append(f"{path} must be one of {allowed}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        comparisons = (
            ("minimum", lambda limit: value < limit, ">="),
            ("maximum", lambda limit: value > limit, "<="),
            ("exclusiveMinimum", lambda limit: value <= limit, ">"),
            ("exclusiveMaximum", lambda limit: value >= limit, "<"),
        )
        for key, invalid, operator in comparisons:
            limit = validation.get(key)
            if isinstance(limit, (int, float)) and invalid(limit):
                errors.append(f"{path} must be {operator} {limit}")

    if isinstance(value, (str, list)):
        minimum = validation.get("minLength" if isinstance(value, str) else "minItems")
        maximum = validation.get("maxLength" if isinstance(value, str) else "maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} must contain at least {minimum} item(s)")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} must contain at most {maximum} item(s)")

    pattern = validation.get("pattern")
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        errors.append(f"{path} does not match the required pattern")

    item_rule = validation.get("items")
    if isinstance(value, list) and isinstance(item_rule, dict):
        item_type = item_rule.get("type")
        if item_type in _VALUE_TYPES and any(not _matches_type(item, item_type) for item in value):
            errors.append(f"Every item in {path} must be {item_type}")

    if isinstance(value, str):
        extensions = validation.get("allowed_extensions")
        if isinstance(extensions, list) and extensions:
            suffixes = tuple(str(extension).lower() for extension in extensions)
            if not value.lower().endswith(suffixes):
                errors.append(f"{path} must use one of these extensions: {extensions}")

        resolved = Path(value)
        if not resolved.is_absolute():
            resolved = PROJECT_ROOT / resolved
        if validation.get("must_exist") is True and not resolved.is_file():
            errors.append(f"{path} file does not exist: {value}")
        elif validation.get("must_parse_as") == "dbc":
            from src.can_io.parser import DatabaseLoader

            try:
                DatabaseLoader().load_dbc(resolved)
            except Exception as exc:
                errors.append(f"{path} is not a valid DBC file: {exc}")
    return errors


def validate_policy_values(raw: dict[str, Any], paths: list[str]) -> list[str]:
    """Validate changed values against metadata constraints from the policy file."""
    errors: list[str] = []
    for path in paths:
        value = _value_at_path(raw, path)
        policy = match_policy(path)
        if value is _MISSING or policy is None:
            continue
        errors.extend(_validate_value(path, value, policy))
    return errors


def classify_paths(paths: list[str]) -> dict[str, list[str]]:
    result = {level.value: [] for level in ReloadLevel}
    for path in paths:
        policy = match_policy(path)
        level = policy.reload_level if policy else ReloadLevel.IMMUTABLE
        result[level.value].append(path)
    return result


def policy_payload() -> dict[str, Any]:
    return {
        "fields_schema_version": 1,
        "setting_modes": {
            "base": "Shown in the simple Settings view.",
            "expand": "Shown in the expanded Settings view together with base fields.",
        },
        "reload_levels": {
            "live": "Applied immediately to runtime references.",
            "reboot": "Saved to disk; reboot Car-HMI to apply fully.",
            "immutable": "Cannot be changed through the API.",
        },
        "fields": [item.to_dict() for item in FIELD_POLICIES],
    }


FIELD_POLICIES: tuple[ConfigFieldPolicy, ...] = load_field_policies()
