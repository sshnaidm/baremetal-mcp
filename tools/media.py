#!/usr/bin/env python3
"""
Virtual media management tools - mount/unmount ISO images.
"""

import asyncio
from typing import Dict, List, Optional

from config import mcp
from helpers import (
    _redfish_call,
    _get_handler,
    _get_vm_path_and_state,
    _eject_virtual_media,
    _insert_virtual_media,
    _ensure_boot_once_single,
)


@mcp.tool(description="Declaratively ensure ISO/image is mounted as virtual media (idempotent), in parallel.")
async def inject_media(server_ids: List[str], image_url: str) -> List[Dict]:
    """Ensure desired ISO is mounted.

    Behavior
    - If same image already mounted: success with message (no change).
    - If different image mounted: eject then insert desired.
    - If none mounted: insert desired.
    """

    async def _inject_single(server_id: str) -> Dict:
        try:
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            currently_inserted = state["inserted"]
            current_image = state["image"]

            if currently_inserted and current_image == image_url:
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": f"Media {image_url} is already inserted; nothing to do",
                }

            # If something else is inserted, eject it first
            if currently_inserted and current_image != image_url:
                eject_result = await _eject_virtual_media(server_id, vm_path)
                if eject_result.get("status") != "success":
                    return {"server_id": server_id, **eject_result}

            # Insert desired media
            insert_result = await _insert_virtual_media(server_id, vm_path, image_url)
            if insert_result.get("status") == "success":
                return {"server_id": server_id, "status": "success", "message": f"Media {image_url} inserted"}
            return {"server_id": server_id, **insert_result}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_inject_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Declaratively ensure no virtual media is mounted (idempotent), in parallel.")
async def eject_media(server_ids: List[str]) -> List[Dict]:
    """Ensure no ISO is mounted.

    Behavior
    - If nothing mounted: success with message (no change).
    - If mounted: eject and report what was ejected.
    """

    async def _eject_single(server_id: str) -> Dict:
        try:
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            currently_inserted = state["inserted"]
            current_image = state["image"]

            if not currently_inserted:
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": "Nothing ejected because nothing was inserted",
                }

            result = await _eject_virtual_media(server_id, vm_path)
            if result.get("status") == "success":
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": f"Ejected media {current_image if current_image else ''}".strip(),
                }
            return {"server_id": server_id, **result}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_eject_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Ensure ISO is mounted, set one-time boot to CD, and reboot if requested (declarative).")
async def boot_from_iso(
    server_ids: List[str],
    image_url: str,
    mode: Optional[str] = None,
    reboot: bool = True,
    reboot_type: str = "ForceRestart",
) -> List[Dict]:
    """Ensure ISO is mounted and next boot is from CD (Once); reboot by default.

    - If the same ISO is already mounted, it will not re-insert.
    - If a different image is mounted, it will eject and insert the requested ISO.
    - Sets BootSourceOverride to CD Once (and optional mode) before rebooting if requested.
    """

    async def _process_single(server_id: str) -> Dict:
        try:
            actions: List[str] = []

            # Ensure media state
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            inserted = state["inserted"]
            current_image = state["image"]

            if inserted and current_image == image_url:
                actions.append(f"Media {image_url} already inserted")
            else:
                if inserted and current_image != image_url:
                    eject_result = await _eject_virtual_media(server_id, vm_path)
                    if eject_result.get("status") != "success":
                        return {"server_id": server_id, **eject_result}
                    actions.append(f"Ejected media {current_image}")

                insert_result = await _insert_virtual_media(server_id, vm_path, image_url)
                if insert_result.get("status") != "success":
                    return {"server_id": server_id, **insert_result}
                actions.append(f"Inserted media {image_url}")

            # Ensure boot once to CD
            ensure_result = await _ensure_boot_once_single(server_id, "Cd", mode=mode)
            if ensure_result.get("status") != "success":
                return ensure_result
            actions.append("Boot override set to Cd Once")

            # Reboot if requested
            if reboot:
                handler = await _get_handler(server_id)
                reset_path = f"{handler.SYSTEM_PATH}/Actions/ComputerSystem.Reset"
                reset_result = await _redfish_call(server_id, "POST", reset_path, {"ResetType": reboot_type})
                if reset_result.get("status") != "success":
                    return {"server_id": server_id, **reset_result}
                actions.append(f"Triggered {reboot_type}")

            return {
                "server_id": server_id,
                "status": "success",
                "message": "; ".join(actions) if actions else "No changes needed",
            }
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_process_single(sid) for sid in server_ids]
    return await asyncio.gather(*tasks)
