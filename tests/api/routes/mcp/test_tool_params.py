"""
MCP tool parameter consistency tests.

Verifies that the parameters defined in each MCP tool function (in __init__.py)
match what the corresponding handler function actually reads via params.get().

This catches issues like:
  - A tool parameter is defined but never passed to the handler
  - A handler reads a param that was never defined or passed
"""
import ast
import os
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[4] / "gns3server" / "api" / "routes" / "mcp"
TOOL_FILE = MCP_DIR / "__init__.py"

HANDLER_FILES = {
    "list_projects_handler": "projects.py",
    "get_project_handler": "projects.py",
    "create_project_handler": "projects.py",
    "delete_project_handler": "projects.py",
    "open_project_handler": "projects.py",
    "close_project_handler": "projects.py",
    "get_project_stats_handler": "projects.py",
    "update_project_handler": "projects.py",
    "duplicate_project_handler": "projects.py",
    "get_project_readme_handler": "projects.py",
    "update_project_readme_handler": "projects.py",
    "lock_project_handler": "projects.py",
    "unlock_project_handler": "projects.py",
    "get_locked_project_handler": "projects.py",
    "load_project_handler": "projects.py",
    "get_nodes_handler": "nodes.py",
    "get_node_handler": "nodes.py",
    "start_node_handler": "nodes.py",
    "stop_node_handler": "nodes.py",
    "reload_node_handler": "nodes.py",
    "suspend_node_handler": "nodes.py",
    "create_node_handler": "nodes.py",
    "delete_node_handler": "nodes.py",
    "update_node_handler": "nodes.py",
    "get_node_console_info_handler": "nodes.py",
    "list_node_files_handler": "nodes.py",
    "get_node_file_handler": "nodes.py",
    "write_node_file_handler": "nodes.py",
    "delete_node_file_handler": "nodes.py",
    "start_all_nodes_handler": "nodes.py",
    "stop_all_nodes_handler": "nodes.py",
    "suspend_all_nodes_handler": "nodes.py",
    "reload_all_nodes_handler": "nodes.py",
    "duplicate_node_handler": "nodes.py",
    "isolate_node_handler": "nodes.py",
    "unisolate_node_handler": "nodes.py",
    "get_node_links_handler": "nodes.py",
    "get_links_handler": "links.py",
    "get_link_handler": "links.py",
    "create_link_handler": "links.py",
    "delete_link_handler": "links.py",
    "update_link_handler": "links.py",
    "reset_link_handler": "links.py",
    "start_capture_handler": "links.py",
    "stop_capture_handler": "links.py",
    "download_capture_file_handler": "links.py",
    "list_templates_handler": "templates.py",
    "get_template_handler": "templates.py",
    "create_template_handler": "templates.py",
    "update_template_handler": "templates.py",
    "delete_template_handler": "templates.py",
    "list_computes_handler": "computes.py",
    "get_compute_handler": "computes.py",
    "get_compute_images_handler": "computes.py",
    "get_snapshots_handler": "snapshots.py",
    "create_snapshot_handler": "snapshots.py",
    "delete_snapshot_handler": "snapshots.py",
    "restore_snapshot_handler": "snapshots.py",
    "get_drawings_handler": "drawings.py",
    "create_drawing_handler": "drawings.py",
    "get_drawing_handler": "drawings.py",
    "update_drawing_handler": "drawings.py",
    "delete_drawing_handler": "drawings.py",
    "get_symbols_handler": "symbols.py",
    "get_symbol_handler": "symbols.py",
    "get_symbol_dimensions_handler": "symbols.py",
    "get_default_symbols_handler": "symbols.py",
    "upload_symbol_handler": "symbols.py",
    "delete_symbol_handler": "symbols.py",
    "get_appliances_handler": "appliances.py",
    "get_appliance_handler": "appliances.py",
    "install_appliance_handler": "appliances.py",
    "get_version_handler": "server.py",
    "get_statistics_handler": "server.py",
    "get_images_handler": "images.py",
    "get_image_handler": "images.py",
    "delete_image_handler": "images.py",
    "prune_images_handler": "images.py",
    "install_images_handler": "images.py",
    "device_config_send_handler": "device_config.py",
    "device_show_run_handler": "device_config.py",
    "vpcs_config_set_handler": "device_config.py",
}


def _get_handler_params(handler_name):
    """Parse handler file and extract all params.get('xxx') calls."""
    filename = HANDLER_FILES.get(handler_name)
    if not filename:
        return None
    filepath = MCP_DIR / filename
    if not filepath.exists():
        return None

    tree = ast.parse(filepath.read_text())

    params = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != handler_name:
            continue
        # Found the handler function, search for params.get("xxx")
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if not hasattr(sub.func, "attr") or sub.func.attr != "get":
                continue
            # params.get("xxx") or params_data.get("xxx")
            func_obj = sub.func
            if (hasattr(func_obj.value, "id") and func_obj.value.id in ("params", "params_data", "link_data", "node_data")) or \
               (hasattr(func_obj.value, "attr") and func_obj.value.attr == "get"):
                if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                    params.add(sub.args[0].value)
    return params


def _get_tool_params(tool_name, tool_file=TOOL_FILE):
    """Parse __init__.py and extract params passed to _run_handler_sync for a given tool.

    Returns the dict literal keys from the _run_handler_sync call.
    """
    tree = ast.parse(tool_file.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != tool_name:
            continue

        # Search for _run_handler_sync calls inside this function
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if not hasattr(sub.func, "id") or sub.func.id != "_run_handler_sync":
                continue
            # _run_handler_sync(handler, {dict}) or _run_handler_sync(handler, params)
            if len(sub.args) >= 2:
                second_arg = sub.args[1]
                if isinstance(second_arg, ast.Dict):
                    keys = set()
                    for k in second_arg.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
                    return keys
                elif isinstance(second_arg, ast.Name) and second_arg.id == "params":
                    return {"*params*"}  # special marker for all params passed through
    return None


def test_handler_params_all_readable():
    """Every handler registered in __init__.py should have a corresponding file."""
    # Extract all handler names from __init__.py by looking for _run_handler_sync calls
    tree = ast.parse(TOOL_FILE.read_text())
    handlers_found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and hasattr(node.func, "id") and node.func.id == "_run_handler_sync":
            if node.args and isinstance(node.args[0], ast.Name):
                handlers_found.add(node.args[0].id)

    unknown = [h for h in handlers_found if h not in HANDLER_FILES]
    assert not unknown, f"Handlers not mapped in HANDLER_FILES: {unknown}"


def _get_tool_fn_name(handler_name):
    """Reverse lookup: find which MCP tool function calls this handler."""
    tree = ast.parse(TOOL_FILE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and hasattr(node.func, "id") and node.func.id == "_run_handler_sync":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == handler_name:
                # Find enclosing function
                for parent in ast.walk(tree):
                    if isinstance(parent, ast.FunctionDef):
                        for child in ast.walk(parent):
                            if child is node:
                                return parent.name
    return None


def test_tool_handler_param_consistency():
    """For each tool, the params passed to the handler should match what the handler reads."""
    tree = ast.parse(TOOL_FILE.read_text())

    # Collect all _run_handler_sync calls with dict literals
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not hasattr(node.func, "id") or node.func.id != "_run_handler_sync":
            continue
        if len(node.args) < 2:
            continue

        handler_name = node.args[0].id if isinstance(node.args[0], ast.Name) else None
        if not handler_name:
            continue

        # Find the tool function name (enclosing function)
        tool_name = None
        for parent in ast.walk(tree):
            if isinstance(parent, ast.FunctionDef):
                for child in ast.walk(parent):
                    if child is node:
                        tool_name = parent.name
                        break
        if not tool_name:
            continue

        second_arg = node.args[1]
        if isinstance(second_arg, ast.Dict):
            passed_keys = set()
            for k in second_arg.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    passed_keys.add(k.value)

            handler_params = _get_handler_params(handler_name)
            if handler_params is None:
                continue

            # Check: every passed key is read by the handler
            extra_passed = passed_keys - handler_params
            assert not extra_passed, (
                f"[{tool_name}] Params passed to handler '{handler_name}' but not read: {extra_passed}"
            )

            # Check: every handler param is passed (except common/optional ones)
            missing = handler_params - passed_keys
            # Filter out well-known optional params that handlers check
            known_optional = {"fields", "template", "name", "version", "compute_id",
                             "x", "y", "link_type", "filters", "suspend", "link_style",
                             "show_filters_icon", "label"}
            truly_missing = missing - known_optional
            if truly_missing:
                pytest.fail(
                    f"[{tool_name}] Handler '{handler_name}' reads params not passed: {truly_missing}"
                )
