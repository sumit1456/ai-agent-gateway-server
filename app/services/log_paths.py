"""Shared filesystem location for all application log files."""

import os
from pathlib import Path


def get_log_dir() -> Path:
    """Return and create the common log directory configured by LOG_DIR."""
    path = Path(os.getenv("LOG_DIR", "logs")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path
