#!/usr/bin/env python3
"""
Low-level Redfish API tools - direct API access.
"""

import asyncio
from typing import Dict, List, Optional

from config import mcp
from helpers import _redfish_call


@mcp.tool(description="Low-level Redfish call for a single server. Use when no higher-level tool fits.")
async def redfish_call(server_id: str, method: str, path: str, payload: Optional[Dict] = None) -> Dict:
    """Make a low-level Redfish API call.

    Args
    - server_id: Target host id.
    - method: HTTP method (GET, POST, PATCH, DELETE).
    - path: Redfish resource path (e.g., /redfish/v1/Systems/1).
    - payload: Optional JSON body for POST/PATCH.

    Returns (success)
    {"status": "success", "data": <json-or-bytes>, "headers": { ... }}

    Returns (error)
    {"status": "error", "message": "..."}
    """
    return await _redfish_call(server_id, method, path, payload)


@mcp.tool(description="Low-level Redfish call over multiple servers in parallel. Adds server_id to each result.")
async def parallel_redfish_call(
    server_ids: List[str], method: str, path: str, payload: Optional[Dict] = None
) -> List[Dict]:
    """Make the same Redfish call across many servers in parallel.

    Args
    - server_ids: List of host ids.
    - method/path/payload: Same as redfish_call.

    Returns
    - List of per-server results, each including server_id.
    """
    tasks = [_redfish_call(server_id, method, path, payload) for server_id in server_ids]
    results = await asyncio.gather(*tasks)
    # Add server_id to each result for context
    for i, result in enumerate(results):
        result["server_id"] = server_ids[i]
    return results
