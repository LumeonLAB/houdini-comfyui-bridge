"""
Houdini package and ComfyUI bridge installer logic.
Creates symlinks, writes Houdini package JSONs, validates installations.
"""
from __future__ import annotations

import json
import platform
import os
from pathlib import Path

from .config import find_houdini_pref_dir


def validate_comfyui(path: Path) -> tuple[bool, str]:
    """Check if a path is a valid ComfyUI installation."""
    if not path.exists():
        return False, "Path does not exist"
    if (path / "main.py").exists():
        return True, "ComfyUI found (main.py)"
    if (path / "comfyui").exists():
        return True, "ComfyUI found (binary)"
    return False, "Not a valid ComfyUI directory (no main.py found)"


def validate_houdini_prefs(path: Path) -> tuple[bool, str]:
    """Check if a path is a valid Houdini preferences directory."""
    if not path.exists():
        return False, "Path does not exist"
    # Should contain things like houdini.env, houdini.pref, etc.
    markers = ["houdini.env", "houdini.pref", "otls"]
    for m in markers:
        if (path / m).exists():
            return True, f"Houdini prefs found ({m})"
    # Might be a fresh install with just packages
    return True, "Directory exists (assuming valid)"


def create_comfyui_symlink(bridge_dir: Path, comfyui_path: Path) -> tuple[bool, str]:
    """Create symlink from ComfyUI custom_nodes to the bridge repo."""
    custom_nodes = comfyui_path / "custom_nodes"
    custom_nodes.mkdir(exist_ok=True)

    link_path = custom_nodes / "houdini-comfyui-connection"

    if link_path.is_symlink():
        return True, f"Symlink already exists"
    if link_path.exists():
        return False, f"Directory already exists (not a symlink): {link_path}"

    system = platform.system()
    try:
        if system == "Windows":
            # Windows needs special handling for symlinks
            import subprocess
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(bridge_dir)],
                check=True, capture_output=True
            )
        else:
            os.symlink(bridge_dir, link_path)
        return True, f"Created symlink: {link_path}"
    except OSError as e:
        return False, f"Failed to create symlink: {e}"


def write_houdini_package(
    bridge_dir: Path,
    houdini_pref_dir: Path,
    comfyui_path: Path,
) -> tuple[bool, str]:
    """Write the Houdini package JSON that loads the bridge."""
    packages_dir = houdini_pref_dir / "packages"
    packages_dir.mkdir(exist_ok=True)

    package_file = packages_dir / "lum3on-comfyui-bridge.json"
    houdini_dir = bridge_dir / "houdini"

    package_data = {
        "env": [
            {
                "HOUDINI_PATH": {
                    "value": str(houdini_dir),
                    "method": "append",
                }
            },
            {"COMFYUI_PATH": str(comfyui_path)},
        ],
        "path": str(houdini_dir),
    }

    try:
        package_file.write_text(json.dumps(package_data, indent=4))
        return True, f"Written: {package_file}"
    except OSError as e:
        return False, f"Failed to write: {e}"


def run_full_install(
    bridge_dir: Path,
    comfyui_path: Path,
    houdini_pref_dir: Path,
) -> list[tuple[str, bool, str]]:
    """
    Run the complete installation.
    Returns list of (step_name, success, message).
    """
    results = []

    # 1. Validate ComfyUI
    ok, msg = validate_comfyui(comfyui_path)
    results.append(("Validate ComfyUI", ok, msg))
    if not ok:
        return results

    # 2. Create ComfyUI symlink
    ok, msg = create_comfyui_symlink(bridge_dir, comfyui_path)
    results.append(("ComfyUI Bridge Plugin", ok, msg))

    # 3. Write Houdini package
    ok, msg = write_houdini_package(bridge_dir, houdini_pref_dir, comfyui_path)
    results.append(("Houdini Package", ok, msg))

    return results
