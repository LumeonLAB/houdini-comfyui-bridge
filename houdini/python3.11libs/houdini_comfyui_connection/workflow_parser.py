"""
Workflow parser for the Lum3on ComfyUI ROP.

Handles:
- Loading workflow JSON (both workflow and API/prompt formats)
- Converting workflow format → API prompt format
- Extracting tweakable parameters for dynamic HDA parm generation
- Identifying LoadImage inputs and output nodes
- Detecting required custom node types
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---- Data Types ------------------------------------------------------------

@dataclass
class ParamInfo:
    """A tweakable parameter extracted from a workflow."""
    node_key: str
    node_title: str
    node_type: str
    input_name: str
    value_type: str  # "INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"
    default_value: object
    options: list[str] = field(default_factory=list)  # for COMBO types
    min_val: float | None = None
    max_val: float | None = None


@dataclass
class ImageInput:
    """A LoadImage node that can receive input from Houdini."""
    node_key: str
    node_title: str
    current_image: str  # the filename currently set


@dataclass
class OutputNode:
    """An output node (SaveImage, PreviewImage, etc.)."""
    node_key: str
    node_title: str
    node_type: str


@dataclass
class ParsedWorkflow:
    """Result of parsing a workflow."""
    prompt: dict  # API/prompt format ready for submission
    params: list[ParamInfo]
    image_inputs: list[ImageInput]
    outputs: list[OutputNode]
    required_node_types: set[str]


# ---- Format Detection & Loading -------------------------------------------

def load_workflow(path: Path) -> dict:
    """Load a workflow JSON file."""
    with open(path) as f:
        return json.load(f)


def is_workflow_format(data: dict) -> bool:
    """Check if data is in workflow format (has 'nodes' list) vs API format."""
    return "nodes" in data and isinstance(data["nodes"], list)


# ---- Workflow → Prompt Conversion ------------------------------------------

def workflow_to_prompt(workflow: dict, node_defs: dict | None = None) -> dict:
    """
    Convert a workflow-format JSON to API/prompt format.

    If node_defs is provided (from /object_info), widget values are mapped
    to named inputs. Otherwise, only connection-based inputs are mapped.
    """
    if not is_workflow_format(workflow):
        # Already in prompt/API format
        return workflow

    nodes = {n["id"]: n for n in workflow["nodes"]}
    links = _parse_links(workflow.get("links", []))

    prompt = {}

    for node_id, node in nodes.items():
        key = str(node_id)
        node_type = node.get("type", "")

        # Skip meta nodes
        if node_type in ("Reroute", "PrimitiveNode", "Note", "MarkdownNote"):
            continue

        inputs = {}

        # Map connections from links
        for inp in node.get("inputs", []):
            link_id = inp.get("link")
            if link_id is not None and link_id in links:
                link = links[link_id]
                # Resolve reroutes
                source_id, source_slot = _resolve_reroute(
                    link["origin_id"], link["origin_slot"], nodes, links
                )
                inputs[inp["name"]] = [str(source_id), source_slot]

        # Map widget values to named inputs
        widget_values = node.get("widgets_values", [])
        if widget_values and node_defs and node_type in node_defs:
            _map_widget_values(inputs, widget_values, node_defs[node_type], node)

        prompt[key] = {
            "class_type": node_type,
            "inputs": inputs,
            "_meta": {
                "title": node.get("title", node_type),
            },
        }

    return prompt


def _parse_links(raw_links: list) -> dict:
    """Parse links array into {link_id: {origin_id, origin_slot, target_id, target_slot, type}}."""
    result = {}
    for link in raw_links:
        if isinstance(link, list) and len(link) >= 6:
            result[link[0]] = {
                "origin_id": link[1],
                "origin_slot": link[2],
                "target_id": link[3],
                "target_slot": link[4],
                "type": link[5] if len(link) > 5 else "",
            }
        elif isinstance(link, dict):
            result[link["id"]] = link
    return result


def _resolve_reroute(origin_id, origin_slot, nodes, links):
    """Follow reroute chains to find the actual source node."""
    node = nodes.get(origin_id)
    if node and node.get("type") == "Reroute":
        for inp in node.get("inputs", []):
            link_id = inp.get("link")
            if link_id is not None and link_id in links:
                link = links[link_id]
                return _resolve_reroute(
                    link["origin_id"], link["origin_slot"], nodes, links
                )
    return origin_id, origin_slot


def _map_widget_values(
    inputs: dict, widget_values: list, node_def: dict, node: dict
):
    """
    Map positional widget_values to named inputs using node definition.
    Follows the same logic as ComfyUI's widget-to-input mapping.
    """
    input_defs = node_def.get("input", {})
    required = input_defs.get("required", {})
    optional = input_defs.get("optional", {})

    # Merge required + optional in order
    all_inputs = {}
    all_inputs.update(required)
    all_inputs.update(optional)

    # Track which inputs are already set via connections
    connected_inputs = set()
    for inp in node.get("inputs", []):
        if inp.get("link") is not None:
            connected_inputs.add(inp["name"])

    values = list(widget_values)
    val_idx = 0

    for input_name, input_config in all_inputs.items():
        if input_name in connected_inputs:
            # This input is connected via wire, don't set from widget
            continue
        if input_name in inputs:
            # Already set (from connection)
            continue

        # Determine if this input would be a widget
        if isinstance(input_config, list) and len(input_config) >= 1:
            type_info = input_config[0]
            if isinstance(type_info, list):
                # COMBO type — consumes one value
                if val_idx < len(values):
                    inputs[input_name] = values[val_idx]
                    val_idx += 1
            elif isinstance(type_info, str):
                if type_info in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                    if val_idx < len(values):
                        inputs[input_name] = values[val_idx]
                        val_idx += 1
                    # Check for control_after_generate
                    if len(input_config) > 1 and isinstance(input_config[1], dict):
                        if input_config[1].get("control_after_generate"):
                            val_idx += 1  # skip the control widget
                else:
                    # Non-widget type (connected via wire), skip
                    pass


# ---- Parameter Extraction --------------------------------------------------

def extract_params(prompt: dict, node_defs: dict | None = None) -> list[ParamInfo]:
    """
    Extract tweakable parameters from a prompt-format workflow.
    Returns parameters that artists would want to adjust.
    """
    params = []

    for node_key, node_data in prompt.items():
        node_type = node_data.get("class_type", "")
        title = node_data.get("_meta", {}).get("title", node_type)
        inputs = node_data.get("inputs", {})

        for input_name, value in inputs.items():
            # Skip connections (lists like [node_key, output_idx])
            if isinstance(value, list):
                continue

            # Determine type
            vtype = _infer_value_type(value, input_name, node_type, node_defs)
            options = []

            # Get options for COMBO from node_defs
            if vtype == "COMBO" and node_defs and node_type in node_defs:
                options = _get_combo_options(
                    node_defs[node_type], input_name
                )

            # Get min/max for numeric types
            min_val, max_val = None, None
            if vtype in ("INT", "FLOAT") and node_defs and node_type in node_defs:
                min_val, max_val = _get_numeric_range(
                    node_defs[node_type], input_name
                )

            params.append(ParamInfo(
                node_key=node_key,
                node_title=title,
                node_type=node_type,
                input_name=input_name,
                value_type=vtype,
                default_value=value,
                options=options,
                min_val=min_val,
                max_val=max_val,
            ))

    return params


def _infer_value_type(
    value: object, input_name: str, node_type: str,
    node_defs: dict | None,
) -> str:
    """Infer the type of a parameter value."""
    # First try node_defs if available
    if node_defs and node_type in node_defs:
        input_info = node_defs[node_type].get("input", {})
        for section in ("required", "optional"):
            inputs = input_info.get(section, {})
            if input_name in inputs:
                config = inputs[input_name]
                if isinstance(config, list) and len(config) >= 1:
                    type_info = config[0]
                    if isinstance(type_info, list):
                        return "COMBO"
                    if isinstance(type_info, str):
                        return type_info

    # Fallback: infer from Python type
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    return "STRING"


def _get_combo_options(node_def: dict, input_name: str) -> list[str]:
    """Get COMBO options from node definition."""
    input_info = node_def.get("input", {})
    for section in ("required", "optional"):
        inputs = input_info.get(section, {})
        if input_name in inputs:
            config = inputs[input_name]
            if isinstance(config, list) and len(config) >= 1:
                type_info = config[0]
                if isinstance(type_info, list):
                    return [str(x) for x in type_info]
    return []


def _get_numeric_range(
    node_def: dict, input_name: str
) -> tuple[float | None, float | None]:
    """Get min/max range from node definition."""
    input_info = node_def.get("input", {})
    for section in ("required", "optional"):
        inputs = input_info.get(section, {})
        if input_name in inputs:
            config = inputs[input_name]
            if isinstance(config, list) and len(config) >= 2:
                meta = config[1] if isinstance(config[1], dict) else {}
                return meta.get("min"), meta.get("max")
    return None, None


# ---- Image Inputs & Outputs -----------------------------------------------

def find_image_inputs(prompt: dict) -> list[ImageInput]:
    """Find all LoadImage nodes that could receive Houdini input."""
    results = []
    for node_key, node_data in prompt.items():
        node_type = node_data.get("class_type", "")
        if node_type in ("LoadImage", "LoadImageMask"):
            title = node_data.get("_meta", {}).get("title", node_type)
            image = node_data.get("inputs", {}).get("image", "")
            results.append(ImageInput(
                node_key=node_key,
                node_title=title,
                current_image=str(image),
            ))
    return results


def find_output_nodes(prompt: dict) -> list[OutputNode]:
    """Find all output/save/preview nodes."""
    output_types = {
        "SaveImage", "PreviewImage", "SaveGLB", "Hy3DExportMesh",
        "HouCuiStringAsImage",
    }
    results = []
    for node_key, node_data in prompt.items():
        node_type = node_data.get("class_type", "")
        if node_type in output_types:
            title = node_data.get("_meta", {}).get("title", node_type)
            results.append(OutputNode(
                node_key=node_key,
                node_title=title,
                node_type=node_type,
            ))
    return results


# ---- Custom Node Detection -------------------------------------------------

def get_required_node_types(prompt: dict) -> set[str]:
    """Get all unique node types from a prompt."""
    skip = {"Reroute", "PrimitiveNode", "Note", "MarkdownNote"}
    return {
        node_data.get("class_type", "")
        for node_data in prompt.values()
        if isinstance(node_data, dict)
        and node_data.get("class_type", "") not in skip
    }


# ---- Full Parse ------------------------------------------------------------

def parse_workflow(
    path: Path,
    node_defs: dict | None = None,
) -> ParsedWorkflow:
    """
    Parse a workflow file into a structured result.
    If node_defs is provided (from server /object_info), parameters
    are more accurately typed and COMBO options are included.
    """
    raw = load_workflow(path)

    if is_workflow_format(raw):
        prompt = workflow_to_prompt(raw, node_defs)
    else:
        prompt = raw

    params = extract_params(prompt, node_defs)
    image_inputs = find_image_inputs(prompt)
    outputs = find_output_nodes(prompt)
    required = get_required_node_types(prompt)

    return ParsedWorkflow(
        prompt=prompt,
        params=params,
        image_inputs=image_inputs,
        outputs=outputs,
        required_node_types=required,
    )
