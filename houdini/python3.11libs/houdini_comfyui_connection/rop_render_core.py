"""
Lum3on ComfyUI ROP — Render Core

Handles the render pipeline:
1. Render Houdini camera → temp image
2. Upload to ComfyUI server
3. Patch workflow with uploaded image + param overrides
4. Submit to ComfyUI and poll for result
5. Download results to output directory
6. Cleanup temp files on server
"""
from __future__ import annotations

import os
import uuid
import tempfile
from pathlib import Path

import hou

from .graph_submission import (
    submit_graph_and_get_result,
    download_result,
    delete_input_image,
    delete_prompt_history,
    GraphValidationError,
    FunctionalityNotAvailable,
    FailedToDeleteImage,
)
from .upload_common import upload_image
from .compound_graph_tools import get_node_definitions
from .workflow_parser import (
    parse_workflow,
    ParsedWorkflow,
    ParamInfo,
    ImageInput,
)


# ---- Camera Rendering ------------------------------------------------------

def render_camera_to_image(
    camera_path: str,
    frame: float,
    resolution: tuple[int, int],
    output_path: Path,
):
    """
    Render a Houdini camera to an image file using OpenGL ROP.
    Creates a temporary ROP, renders, then cleans up.
    """
    rop_net = hou.node("/out")
    if rop_net is None:
        raise RuntimeError("Cannot find /out network")

    # Create temp OpenGL ROP
    gl_rop = rop_net.createNode("opengl", f"_lum3on_temp_render_{uuid.uuid4().hex[:8]}")
    try:
        gl_rop.parm("camera").set(camera_path)
        gl_rop.parm("picture").set(str(output_path))
        gl_rop.parm("res_overridex").set(resolution[0])
        gl_rop.parm("res_overridey").set(resolution[1])
        gl_rop.parm("res_override").set("specific")

        # Set frame and render
        old_frame = hou.frame()
        hou.setFrame(frame)
        try:
            gl_rop.render(
                frame_range=(frame, frame),
                ignore_inputs=True,
                verbose=False,
            )
        finally:
            hou.setFrame(old_frame)

        if not output_path.exists():
            raise RuntimeError(f"Render failed: output not created at {output_path}")
    finally:
        gl_rop.destroy()


# ---- Workflow Patching ------------------------------------------------------

def patch_image_inputs(
    prompt: dict,
    image_inputs: list[ImageInput],
    uploaded_filenames: dict[str, str],
):
    """
    Replace LoadImage node inputs with uploaded filenames.
    uploaded_filenames maps node_key → uploaded filename on server.
    """
    for img_input in image_inputs:
        if img_input.node_key in uploaded_filenames:
            fname = uploaded_filenames[img_input.node_key]
            if img_input.node_key in prompt:
                prompt[img_input.node_key]["inputs"]["image"] = fname


def patch_params(prompt: dict, overrides: dict[tuple[str, str], object]):
    """
    Apply parameter overrides to the prompt.
    overrides maps (node_key, input_name) → value.
    """
    for (node_key, input_name), value in overrides.items():
        if node_key in prompt:
            prompt[node_key].setdefault("inputs", {})[input_name] = value


def ensure_save_nodes(prompt: dict, output_keys: list[str]) -> dict:
    """
    Make sure output nodes are SaveImage (not PreviewImage) so we get
    downloadable results. Converts PreviewImage → SaveImage.
    Returns mapping of original_key → save_node_key.
    """
    save_map = {}
    for key in output_keys:
        if key not in prompt:
            continue
        node = prompt[key]
        if node.get("class_type") == "PreviewImage":
            # Convert to SaveImage for downloadable output
            node["class_type"] = "SaveImage"
            node["inputs"].setdefault(
                "filename_prefix", f"lum3on_render_{key}"
            )
        save_map[key] = key
    return save_map


# ---- Main Render Function ---------------------------------------------------

def render_frame(
    host: str,
    workflow_path: Path,
    frame: float,
    output_dir: Path,
    output_prefix: str,
    *,
    camera_path: str | None = None,
    resolution: tuple[int, int] = (1024, 1024),
    param_overrides: dict[tuple[str, str], object] | None = None,
    output_pass_keys: list[str] | None = None,
    api_key: str | None = None,
    cleanup: bool = True,
    long_op=None,
) -> dict[str, Path]:
    """
    Render a single frame through ComfyUI.

    Returns mapping of output_key → downloaded file path.
    """
    # Get node definitions from server for accurate param mapping
    if long_op:
        long_op.updateLongProgress(-1, "Fetching node definitions...")

    node_defs = get_node_definitions(host, api_key=api_key)

    # Parse workflow
    parsed = parse_workflow(workflow_path, node_defs=node_defs)
    prompt = parsed.prompt

    # Upload camera render as input image(s)
    uploaded: dict[str, str] = {}
    temp_files: list[Path] = []

    if camera_path and parsed.image_inputs:
        if long_op:
            long_op.updateLongProgress(-1, "Rendering camera...")

        for img_input in parsed.image_inputs:
            temp_dir = Path(tempfile.mkdtemp(prefix="lum3on_"))
            temp_file = temp_dir / f"camera_input_{img_input.node_key}.png"
            temp_files.append(temp_file)

            render_camera_to_image(camera_path, frame, resolution, temp_file)

            if long_op:
                long_op.updateLongProgress(-1, "Uploading to ComfyUI...")

            server_filename = f"houdini_lum3on/{uuid.uuid4().hex}.png"
            subdir, fname = server_filename.rsplit("/", 1)
            upload_image(host, temp_file, subdir, fname, api_key=api_key)
            uploaded[img_input.node_key] = server_filename

    # Patch workflow
    patch_image_inputs(prompt, parsed.image_inputs, uploaded)

    if param_overrides:
        patch_params(prompt, param_overrides)

    # Determine output nodes
    if output_pass_keys:
        active_outputs = output_pass_keys
    else:
        active_outputs = [o.node_key for o in parsed.outputs]

    # Ensure outputs are SaveImage type
    save_map = ensure_save_nodes(prompt, active_outputs)

    # Submit
    if long_op:
        long_op.updateLongProgress(-1, "Submitting to ComfyUI...")

    res, prompt_id = submit_graph_and_get_result(
        host, prompt, long_op=long_op, api_key=api_key
    )

    # Download results
    if long_op:
        long_op.updateLongProgress(-1, "Downloading results...")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Expand frame token in output prefix
    frame_str = str(int(frame)).zfill(4)
    expanded_prefix = output_prefix.replace("$F4", frame_str).replace(
        "$F", str(int(frame))
    )

    result_paths: dict[str, Path] = {}

    for orig_key, save_key in save_map.items():
        if save_key not in res:
            continue

        output_data = res[save_key]
        images = output_data.get("images", output_data.get("3d", []))

        for i, img_data in enumerate(images):
            ext = img_data["filename"].rsplit(".", 1)[-1] if "." in img_data["filename"] else "png"
            if len(images) == 1:
                local_name = f"{expanded_prefix}.{ext}"
            else:
                local_name = f"{expanded_prefix}_{i}.{ext}"

            local_path = output_dir / local_name
            download_result(
                host,
                img_data["filename"],
                img_data.get("subfolder", ""),
                local_path,
                api_key=api_key,
            )
            result_paths[orig_key] = local_path

    # Cleanup
    if cleanup:
        if long_op:
            long_op.updateLongProgress(-1, "Cleaning up...")

        # Clean uploaded images
        for server_filename in uploaded.values():
            try:
                subdir, fname = server_filename.rsplit("/", 1)
                delete_input_image(host, fname, subdir, api_key=api_key)
            except (FailedToDeleteImage, FunctionalityNotAvailable):
                pass

        # Clean prompt history
        try:
            delete_prompt_history(host, prompt_id, api_key=api_key)
        except Exception:
            pass

    # Clean temp files
    for tf in temp_files:
        try:
            tf.unlink(missing_ok=True)
            tf.parent.rmdir()
        except OSError:
            pass

    return result_paths


# ---- Frame Range Render ----------------------------------------------------

def render_frame_range(
    host: str,
    workflow_path: Path,
    frame_start: float,
    frame_end: float,
    frame_step: float,
    output_dir: Path,
    output_prefix: str,
    *,
    camera_path: str | None = None,
    resolution: tuple[int, int] = (1024, 1024),
    param_overrides: dict[tuple[str, str], object] | None = None,
    output_pass_keys: list[str] | None = None,
    api_key: str | None = None,
    cleanup: bool = True,
    long_op=None,
) -> list[dict[str, Path]]:
    """Render a range of frames."""
    results = []
    frame = frame_start

    total_frames = max(1, int((frame_end - frame_start) / frame_step) + 1)
    current = 0

    while frame <= frame_end:
        if long_op:
            long_op.updateLongProgress(
                current / total_frames,
                f"Rendering frame {int(frame)} ({current + 1}/{total_frames})"
            )

        frame_result = render_frame(
            host, workflow_path, frame, output_dir, output_prefix,
            camera_path=camera_path,
            resolution=resolution,
            param_overrides=param_overrides,
            output_pass_keys=output_pass_keys,
            api_key=api_key,
            cleanup=cleanup,
            long_op=long_op,
        )
        results.append(frame_result)

        frame += frame_step
        current += 1

    return results
