#!/usr/bin/env python3
"""
Dell-specific tools - firmware updates, hardware inventory export, ISO listing.
"""

import asyncio
import os
import time
from typing import Dict, List

from config import mcp, ISOS, _load_config, _flatten_dict, logger
from helpers import _redfish_call, _get_handler
from cache import RESPONSE_CACHE, TTL_DISK_CACHE


@mcp.tool(description="List all available ISOs with their URLs.")
async def list_isos() -> Dict:
    """List all available ISOs with their URLs as flattened key-value pairs.

    Keys are formed by joining the nested YAML structure keys with underscores.
    Example key: dell_model_640_idrac_version_5
    Example value: http://10.6.105.38/iDRAC-with-Lifecycle-Controller_Firmware_...

    Returns
    - {"status": "success", "data": {<flattened_key>: <url>, ...}}
    """
    _load_config()
    result = _flatten_dict(ISOS)
    return {"status": "success", "data": result}


@mcp.tool(description="List URL for a specific vendor and model and update target")
async def dell_list_url(model: str, target: str, version: str) -> Dict:
    """List URL for a specific to DELL model and version and update target

    Args:
        model: model name (e.g. 640)
        target: target name (e.g. idrac, bios)
        version: version number (e.g. 5, 6, 7)

    Returns:
        - {"status": "success", "data": <url>}
        - {"status": "error", "message": <error message>}

    Example:
        dell_list_url("640", "idrac", "5")
        => {"status": "success", "data": "http://10.6.105.38/iDRAC-with-Lifecycle-Controller_Firmware_....00_A00.EXE"}
        dell_list_url("640", "bios", "1")
        => {"status": "success", "data": "http://10.6.105.38/BIOS_YJXXX_WN64_1.6.13.EXE"}
    """
    _load_config()
    models = ISOS.get("dell", {})
    model_info = [i for i in models if model in i]
    if not model_info:
        return {"status": "error", "message": f"Model {model} not found for DELL"}
    model_info = model_info[0]
    if target.lower() == "idrac":
        return {"status": "success", "data": model_info.get("idrac_version", {}).get(version, {})}
    if target.lower() == "bios":
        return {"status": "success", "data": model_info.get("bios_version", {}).get(version, {})}
    return {"status": "error", "message": f"Target {target} not found for model {model} and version {version}"}


@mcp.tool(
    description=(
        "Dell-only: export full hardware inventory XML via OEM action and fetch it; "
        "caches per server in data/<server_id> if available."
    )
)
async def dell_export_hardware_inventory(server_ids: List[str]) -> List[Dict]:
    """Export and retrieve Dell OEM hardware inventory (XML). Uses on-disk cache if present.

    Notes
    - Works only on Dell iDRAC; returns error for other vendors.
    - Returns raw XML in data.
    """

    async def _get_hardware_inventory_single(server_id: str) -> Dict:
        try:
            handler = await _get_handler(server_id)
            if not handler.HW_INVENTORY_PATH:
                return {"server_id": server_id, "status": "error", "message": "No hardware inventory can be exported"}
            # Check cache first
            # Define path for cache
            cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
            os.makedirs(cache_dir, exist_ok=True)
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in server_id)
            cache_path = os.path.join(cache_dir, safe_id)
            if os.path.exists(cache_path):
                try:
                    if time.time() - os.path.getmtime(cache_path) > TTL_DISK_CACHE:
                        logger.info(f"Disk cache expired for {server_id}, re-fetching hardware inventory")
                    else:
                        with open(cache_path, "rb") as f:
                            cached_bytes = f.read()
                        cached_text = cached_bytes.decode("utf-8", errors="ignore")
                        return {
                            "server_id": server_id,
                            "status": "success",
                            "data": cached_text,
                            "file_path": cache_path,
                            "cached": True,
                        }
                except Exception as read_err:
                    logger.warning(f"Failed to read cached hardware inventory for {server_id}: {read_err}")

            # Trigger export and download
            response = await _redfish_call(server_id, "POST", handler.HW_INVENTORY_PATH, payload={"ShareType": "Local"})
            hardware_inventory_path = response.get("headers", {}).get("Location")
            logger.info(f"Hardware inventory path: {hardware_inventory_path}")
            if not hardware_inventory_path:
                return {"server_id": server_id, "status": "error", "message": "No hardware inventory path found"}

            hardware_inventory_response = await _redfish_call(
                server_id, "GET", hardware_inventory_path, json_response=False
            )
            if hardware_inventory_response.get("status") != "success":
                return {"server_id": server_id, **hardware_inventory_response}

            xml_payload = hardware_inventory_response.get("data")
            # Ensure text for JSON serialization
            if isinstance(xml_payload, bytes):
                xml_text = xml_payload.decode("utf-8", errors="ignore")
                xml_bytes = xml_payload
            else:
                xml_text = str(xml_payload)
                xml_bytes = xml_text.encode("utf-8", errors="ignore")

            try:
                with open(cache_path, "wb") as f:
                    f.write(xml_bytes)
            except Exception as write_err:
                logger.warning(f"Failed to cache hardware inventory for {server_id}: {write_err}")

            return {"server_id": server_id, "status": "success", "data": xml_text}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_get_hardware_inventory_single(sid) for sid in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Update DELL IDRAC or BIOS firmware for a specific model and version and target")
async def dell_update_firmware(server_id: str, url: str, reboot: bool = False) -> Dict:
    """Update DELL IDRAC or BIOS firmware using DMTF SimpleUpdate action.

    Uses the standard Redfish SimpleUpdate action with ImageURI for remote firmware files.
    Reference: https://github.com/dell/iDRAC-Redfish-Scripting

    Args:
        server_id: server id (e.g. cnfdt1)
        url: HTTP/HTTPS URL of the firmware file (.EXE Dell Update Package)
        reboot: if True, reboot server after scheduling update (needed for BIOS, not for iDRAC)

    Returns:
        - {"status": "success", "message": "Firmware update initiated", "job_id": <job_id>}
        - {"status": "error", "message": <error message>}

    Example:
        dell_update_firmware(
            "cnfdt1", "http://10.6.105.38/iDRAC-with-Lifecycle-Controller_Firmware_RTTPH_WN64_5.10.50.00_A00.EXE")
        => {"status": "success", "message": "Firmware update initiated", "job_id": "JID_123456789"}
    """
    try:
        handler = await _get_handler(server_id)

        # Use DMTF SimpleUpdate action with ImageURI for remote URL updates
        simple_update_path = "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"

        # Validate URL protocol
        if not url.lower().startswith(("http://", "https://")):
            return {"server_id": server_id, "status": "error", "message": "URL must start with http:// or https://"}

        payload = {"ImageURI": url, "@Redfish.OperationApplyTime": "Immediate"}

        result = await _redfish_call(server_id, "POST", simple_update_path, payload)

        if result.get("status") == "success":
            RESPONSE_CACHE.invalidate_prefix(f"{server_id}:firmware_inventory")
            # Extract job ID from Location header
            job_id = result.get("headers", {}).get("Location", "").split("/")[-1]
            message = "Firmware update job created"

            # Reboot if requested (needed for BIOS updates, not for iDRAC direct updates)
            if reboot:
                reset_path = f"{handler.SYSTEM_PATH}/Actions/ComputerSystem.Reset"
                reset_result = await _redfish_call(server_id, "POST", reset_path, {"ResetType": "GracefulRestart"})
                if reset_result.get("status") == "success":
                    message += "; server reboot initiated"
                else:
                    message += f"; reboot failed: {reset_result.get('message')}"

            return {
                "server_id": server_id,
                "status": "success",
                "message": message,
                "job_id": job_id if job_id else None,
                "data": result.get("data"),
            }
        return {"server_id": server_id, **result}
    except Exception as e:
        return {"server_id": server_id, "status": "error", "message": str(e)}
