#!/usr/bin/env python3
"""
MCP Resources for host configuration access.
"""

from typing import List

from config import mcp, CONFIG


@mcp.resource("hosts://all")
def get_all_hosts() -> dict:
    """List all known hosts with their configuration.

    Returns
    - Dict keyed by server_id with host configuration records.

    Example output
    {
      "srv01": {"bmc_ip": "10.0.0.5", "vendor": "dell", "lab": "labA", "tags": ["gpu"]},
      "srv02": {"bmc_ip": "10.0.0.6", "vendor": "hpe"}
    }
    """
    return CONFIG


@mcp.resource("hosts://id/{server_id}")
def get_host_details(server_id: str) -> dict:
    """Get configuration for a specific server.

    Args
    - server_id: Host identifier as defined in configuration.

    Errors
    - Raises ValueError if server_id is unknown.

    Example output
    {"bmc_ip": "10.0.0.5", "vendor": "dell", "lab": "labA", "tags": ["gpu"]}
    """
    if server_id not in CONFIG:
        raise ValueError(f"Server '{server_id}' not found in configuration.")
    return CONFIG[server_id]


@mcp.resource("hosts://ids/{server_ids}")
def get_multiple_host_details(server_ids: str) -> List[dict]:
    """Get configurations for multiple servers.

    Args
    - server_ids: Comma-separated list like "srv01,srv02".

    Returns
    - List of host configs in the same order.

    Errors
    - Raises ValueError if any server_id is unknown.
    """
    ids = [sid.strip() for sid in server_ids.split(",") if sid.strip()]
    hosts: List[dict] = []
    for sid in ids:
        if sid not in CONFIG:
            raise ValueError(f"Server '{sid}' not found in configuration.")
        hosts.append(CONFIG[sid])
    return hosts


@mcp.resource("hosts://lab/{lab}")
def get_all_hosts_from_lab(lab: str) -> List[dict]:
    """List hosts for a given lab.

    Args
    - lab: Lab name to match against host configuration.

    Returns
    - List of host configs, or a single message record when none found.
    """
    hosts = []
    for sid, config in CONFIG.items():
        if config.get("lab") == lab:
            hosts.append(config)
    if not hosts:
        return [{"message": f"No servers found in lab '{lab}'"}]
    return hosts


@mcp.resource("hosts://tag/{tag}")
def get_all_hosts_for_tag(tag: str) -> List[dict]:
    """List hosts that have a given tag.

    Args
    - tag: Tag to filter hosts by (e.g., "gpu").

    Returns
    - List of host configs, or a single message record when none found.
    """
    hosts = []
    for sid, config in CONFIG.items():
        tags = config.get("tags") or []
        if tag in tags:
            hosts.append(config)
    if not hosts:
        return [{"message": f"No servers found for tag '{tag}'"}]
    return hosts
