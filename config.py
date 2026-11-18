"""Load a .env file into the process environment (no third-party dependency).

Precedence: real environment variables > .env file > defaults in code.
Lookup: ENV_FILE if set, else <project root>/.env. A missing file is not an error.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        quote = value[:1]
        if quote in ("'", '"'):
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]  # drop anything after the closing quote
        elif " #" in value:  # inline comment on an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        key = key.strip()
        if key:
            values[key] = value
    return values


def load_env(path: Path | None = None) -> list[str]:
    """Set variables from the env file that are not already set. Returns the keys it set."""
    target = path or Path(os.environ.get("ENV_FILE", ROOT / ".env"))
    if not target.is_file():
        return []
    applied: list[str] = []
    for key, value in parse_env(target.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied
