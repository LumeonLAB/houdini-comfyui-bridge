"""
ComfyUI server API helpers for the manager.
Checks server status, installed nodes, installs custom nodes.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path


def check_server(url: str, timeout: float = 5.0) -> bool:
    """Check if ComfyUI server is reachable."""
    try:
        resp = urllib.request.urlopen(f"{url}/system_stats", timeout=timeout)
        return resp.status == 200
    except Exception:
        return False


def get_installed_node_types(url: str, timeout: float = 15.0) -> set[str]:
    """Get all installed node types from a running ComfyUI server."""
    resp = urllib.request.urlopen(f"{url}/object_info", timeout=timeout)
    data = json.loads(resp.read())
    return set(data.keys())


def extract_workflow_node_types(workflow_path: Path) -> set[str]:
    """Extract all required node types from a workflow JSON file."""
    skip = {"Reroute", "PrimitiveNode", "Note", "MarkdownNote"}
    types = set()

    with open(workflow_path) as f:
        data = json.load(f)

    if "nodes" in data and isinstance(data["nodes"], list):
        # Workflow format
        for node in data["nodes"]:
            t = node.get("type", "")
            if t and t not in skip:
                types.add(t)
    else:
        # API/prompt format
        for val in data.values():
            if isinstance(val, dict) and "class_type" in val:
                types.add(val["class_type"])

    return types


def extract_node_pack_info(workflow_path: Path) -> dict[str, str | None]:
    """Extract node type -> pack name mapping from workflow properties."""
    mapping = {}

    with open(workflow_path) as f:
        data = json.load(f)

    if "nodes" in data and isinstance(data["nodes"], list):
        for node in data["nodes"]:
            node_type = node.get("type", "")
            props = node.get("properties", {})
            pack = props.get("cnr_id") or props.get("aux_id")
            if node_type:
                mapping[node_type] = pack

    return mapping


def find_missing_nodes(
    url: str, workflow_paths: list[Path]
) -> dict[str, str | None]:
    """
    Returns {node_type: pack_name_or_None} for all node types
    in the given workflows that are NOT installed on the server.
    """
    installed = get_installed_node_types(url)

    missing: dict[str, str | None] = {}
    for wf_path in workflow_paths:
        try:
            required = extract_workflow_node_types(wf_path)
            pack_info = extract_node_pack_info(wf_path)
            for t in required - installed:
                if t not in missing:
                    missing[t] = pack_info.get(t)
        except Exception:
            continue

    return missing


def install_custom_node_from_git(
    comfyui_path: Path, git_url: str
) -> tuple[bool, str]:
    """Clone a custom node repo into ComfyUI's custom_nodes directory."""
    import subprocess

    custom_nodes_dir = comfyui_path / "custom_nodes"
    custom_nodes_dir.mkdir(exist_ok=True)

    repo_name = git_url.rstrip("/").split("/")[-1].replace(".git", "")
    dest = custom_nodes_dir / repo_name

    if dest.exists():
        return True, f"Already exists: {repo_name}"

    try:
        result = subprocess.run(
            ["git", "clone", git_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            # Install requirements if they exist
            req_file = dest / "requirements.txt"
            if req_file.exists():
                subprocess.run(
                    ["pip", "install", "-r", str(req_file)],
                    capture_output=True,
                    timeout=120,
                )
            return True, f"Installed: {repo_name}"
        else:
            return False, f"Failed: {result.stderr}"
    except Exception as e:
        return False, f"Error: {e}"
