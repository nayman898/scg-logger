"""Locations shared by the launcher and desktop app."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_session_directory() -> Path:
    # Keep recordings alongside a source checkout. Installed copies use a
    # writable user directory rather than trying to write to site-packages.
    if (PROJECT_ROOT / "Setup.cmd").is_file():
        return PROJECT_ROOT / "sessions"
    data_root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return data_root / "SCG Logger" / "sessions"
