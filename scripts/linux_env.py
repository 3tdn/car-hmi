"""Validate .env assignments before emitting shell exports and a systemd snapshot."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
READONLY_KEYS = {"BASHOPTS", "BASH_VERSINFO", "EUID", "PPID", "SHELLOPTS", "UID"}


def read_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if match is None or match[1] in READONLY_KEYS:
            raise ValueError(f"invalid assignment at line {number}")
        key, raw = match.groups()
        try:
            lexer = shlex.shlex(raw, posix=True, punctuation_chars=";&|()<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            raise ValueError(f"invalid quoting at line {number}") from None
        if not raw.strip() or raw.lstrip().startswith("#"):
            value = ""
        elif not tokens or (len(tokens) > 1 and not tokens[1].startswith("#")):
            raise ValueError(f"expected one value at line {number}")
        else:
            value = tokens[0]
        if "\x00" in value:
            raise ValueError(f"invalid character at line {number}")
        if key in {"PORT", "CAR_HMI_FRONTEND_PORT"} and value and (
            not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535
        ):
            raise ValueError(f"invalid port at line {number}")
        values[key] = value
    return values


def main() -> int:
    try:
        values = read_environment(Path(sys.argv[1]))
    except (OSError, UnicodeError, ValueError) as exc:
        # Never echo assignment values: the file may contain credentials.
        detail = str(exc) if type(exc) is ValueError else "unable to read file"
        print(
            f"[env] ERROR: .env {detail}; ignoring the whole file and using defaults.",
            file=sys.stderr,
        )
        return 1
    exports = [f"export {key}={shlex.quote(value)}" for key, value in values.items()]
    snapshot = "\n".join(
        key + '=\"' + value.replace("\\", "\\\\").replace('"', '\\"') + '\"'
        for key, value in values.items()
    )
    exports.append("HMI_ENV_SYSTEMD_CONTENT=" + shlex.quote(snapshot))
    print("\n".join(exports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
