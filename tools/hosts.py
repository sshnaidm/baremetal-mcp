#!/usr/bin/env python3
"""
Host management tools - list and query host configurations.
"""

from typing import Any, Dict, List

from config import mcp, CONFIG, _load_config, CONFIG_FILE
import os


def _check_config() -> Dict:
    """Load config and check if it exists, return error dict if not found."""
    _load_config()
    if not CONFIG and not os.path.exists(CONFIG_FILE):
        return {
            "status": "error",
            "message": (
                f"Redfish configuration file not found at {CONFIG_FILE}. "
                "Please set REDFISH_CONFIG or create the file."
            ),
        }
    return {}


@mcp.tool(description="List all known hosts with their configuration (mirror of hosts://all).")
def list_hosts() -> Dict:
    """Return the complete hosts mapping.

    Returns
    - {"status": "success", "data": {<server_id>: <config>, ...}}

    Example
    {"status": "success", "data": {"srv01": {"bmc_ip": "10.0.0.5", "vendor": "dell"}}}
    """
    err = _check_config()
    if err:
        return err
    return {"status": "success", "data": CONFIG}


@mcp.tool(description="Get a single host configuration by id (mirror of hosts://id/{server_id}).")
def get_host(server_id: str) -> Dict:
    """Get configuration for a specific server.

    Args
    - server_id: Host identifier as defined in configuration.

    Returns
    - success: {"status": "success", "data": <config>}
    - error:   {"status": "error", "message": "..."}
    """
    err = _check_config()
    if err:
        return err
    if server_id not in CONFIG:
        return {"status": "error", "message": f"Server '{server_id}' not found in configuration."}
    return {"status": "success", "data": CONFIG[server_id]}


@mcp.tool(description="Get multiple host configurations by ids (mirror of hosts://ids/{server_ids}).")
def get_hosts(server_ids: List[str]) -> Dict:
    """Get configurations for multiple servers.

    Args
    - server_ids: List of host identifiers.

    Returns
    - {"status": "success", "data": {<server_id>: <config>, ...}, "missing": [<id> ...]}
    """
    err = _check_config()
    if err:
        return err
    data: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for sid in server_ids:
        cfg = CONFIG.get(sid)
        if cfg is None:
            missing.append(sid)
        else:
            data[sid] = cfg
    return {"status": "success", "data": data, "missing": missing}


@mcp.tool(description="List hosts that belong to a given lab (mirror of hosts://lab/{lab}).")
def list_hosts_by_lab(lab: str) -> Dict:
    """Return hosts for a given lab.

    Args
    - lab: Lab name to match against host configuration.

    Returns
    - {"status": "success", "data": {<server_id>: <config>, ...}}
    - When none found, data is an empty object.
    """
    err = _check_config()
    if err:
        return err
    data = {sid: cfg for sid, cfg in CONFIG.items() if cfg.get("lab") == lab}
    return {"status": "success", "data": data}


@mcp.tool(description="List hosts that have a specific tag (mirror of hosts://tag/{tag}).")
def list_hosts_by_tag(tag: str) -> Dict:
    """Return hosts that contain the given tag.

    Args
    - tag: Tag to filter hosts by (e.g., "gpu").

    Returns
    - {"status": "success", "data": {<server_id>: <config>, ...}}
    - When none found, data is an empty object.
    """
    err = _check_config()
    if err:
        return err
    data = {sid: cfg for sid, cfg in CONFIG.items() if tag in (cfg.get("tags") or [])}
    return {"status": "success", "data": data}
