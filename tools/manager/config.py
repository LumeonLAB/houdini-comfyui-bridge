"""
Persistent configuration for the Lum3on ComfyUI Bridge Manager.
Stores paths, preferences, and state across sessions.
"""
from __future__ import annotations

import json
import platform
from pathlib import Path


APP_NAME = "Lum3on ComfyUI Bridge"
APP_VERSION = "1.0.0"


def get_config_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".config"
    d = base / "lum3on-comfyui-bridge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def load_config() -> dict:
    path = get_config_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict):
    path = get_config_path()
    path.write_text(json.dumps(config, indent=2))


def find_houdini_pref_dir() -> Path | None:
    system = platform.system()
    candidates = []
    if system == "Darwin":
        base = Path.home() / "Library" / "Preferences" / "houdini"
        candidates = sorted(base.glob("*.*"), reverse=True)
    elif system == "Windows":
        base = Path.home() / "Documents"
        candidates = sorted(base.glob("houdini*.*"), reverse=True)
    else:  # Linux
        candidates = sorted(Path.home().glob("houdini*.*"), reverse=True)

    for c in candidates:
        if c.is_dir():
            return c
    return None


def find_comfyui_path() -> Path | None:
    """Try common ComfyUI install locations."""
    candidates = [
        Path.home() / "ComfyUI",
        Path.home() / "Documents" / "ComfyUI",
        Path.home() / "Documents" / "ComfyUI_fresh",
    ]
    if platform.system() == "Windows":
        candidates.append(Path("C:/ComfyUI"))

    for c in candidates:
        if c.is_dir() and (c / "main.py").exists():
            return c
    return None
