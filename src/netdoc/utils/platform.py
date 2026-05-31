"""Platform detection helpers."""

from __future__ import annotations

import os
import platform


def is_windows() -> bool:
    """Return whether the current Python process is running on Windows."""
    return platform.system().lower() == "windows"


def is_linux() -> bool:
    """Return whether the current Python process is running on Linux."""
    return platform.system().lower() == "linux"


def is_wsl() -> bool:
    """Return whether the current Linux process appears to run inside WSL."""
    if not is_linux():
        return False
    if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
        return True
    try:
        with open("/proc/version", encoding="utf-8") as version_file:
            return "microsoft" in version_file.read().casefold()
    except OSError:
        return False


def platform_name() -> str:
    """Return a compact platform label for observations."""
    if is_windows():
        return "windows"
    if is_wsl():
        return "wsl"
    if is_linux():
        return "linux"
    return platform.system().lower() or "unknown"
