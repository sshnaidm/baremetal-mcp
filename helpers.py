#!/usr/bin/env python3
"""
Internal helper functions for Redfish API calls.
"""

import asyncio
import time
import httpx
from urllib.parse import urljoin
from typing import Any, Dict, Optional

from config import (
    CONFIG,
    SECRETS,
    DEFAULT_TIMEOUT,
    VIRTUAL_MEDIA_PATH_CACHE,
    logger,
    request_logger,
    _load_config,
)
from handlers import VENDOR_MAP

# Lazily-initialised async HTTP client (one per process, verify=False for BMC
# self-signed certificates which is the overwhelmingly common case).
_http_client: Optional[httpx.AsyncClient] = None

# Cache handler instances so we don't re-create one on every API call.
_HANDLER_CACHE: Dict[str, Any] = {}


def _get_http_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared async HTTP client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        )
    return _http_client


async def _get_vendor_from_api(bmc_ip: str) -> str:
    """Detect vendor by querying the Redfish API asynchronously."""
    logger.info(f"Detecting vendor for {bmc_ip}...")
    url = f"https://{bmc_ip}/redfish/v1"
    try:
        client = _get_http_client()
        response = await client.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        oem = response.json().get("Oem", {})
        if "Dell" in oem:
            return "dell"
        if "Hpe" in oem:
            return "hpe"
        if "Supermicro" in oem:
            return "supermicro"
        raise ValueError("Unknown vendor in OEM data")
    except (httpx.HTTPError, ValueError) as e:
        logger.error(f"Could not auto-detect vendor for {bmc_ip}: {e}")
        raise ConnectionError(f"Could not auto-detect vendor for {bmc_ip}")


async def _get_handler(server_id: str):
    """Get the appropriate vendor handler for a given server.

    Returns a cached instance when one already exists for *server_id*.
    """
    cached = _HANDLER_CACHE.get(server_id)
    if cached is not None:
        return cached

    _load_config()

    from config import CONFIG_FILE
    import os

    if not CONFIG and not os.path.exists(CONFIG_FILE):
        raise ValueError(
            f"Redfish configuration file not found at {CONFIG_FILE}. Please set REDFISH_CONFIG or create the YAML file."
        )

    server_config = CONFIG.get(server_id)
    if not server_config:
        raise ValueError(f"Server '{server_id}' not found in configuration.")

    vendor = server_config.get("vendor")
    if not vendor:
        vendor = await _get_vendor_from_api(server_config["bmc_ip"])
        # Cache the detected vendor for future calls
        CONFIG[server_id]["vendor"] = vendor

    handler_class = VENDOR_MAP.get(vendor.lower())
    if not handler_class:
        raise ValueError(f"Unsupported vendor: {vendor}")

    creds = SECRETS.get(server_id, {})
    handler = handler_class(creds.get("username"), creds.get("password"))
    _HANDLER_CACHE[server_id] = handler
    return handler


async def _redfish_call(
    server_id: str,
    method: str,
    path: str,
    payload: Optional[Dict] = None,
    timeout: Optional[int] = None,
    json_response: bool = True,
) -> Dict:
    """Internal function to make a generic Redfish API call."""
    try:
        handler = await _get_handler(server_id)
        server_config = CONFIG[server_id]
        base_url = f"https://{server_config['bmc_ip']}"
        url = urljoin(base_url, path)

        request_args = handler.get_request_args()
        # verify is handled at the client level (verify=False)
        request_args["timeout"] = timeout or DEFAULT_TIMEOUT
        if payload is not None:
            request_args["json"] = payload

        client = _get_http_client()
        max_attempts = 3
        response_headers = {}
        start = time.monotonic()
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.request(method.upper(), url, **request_args)
                response.raise_for_status()
                elapsed = time.monotonic() - start
                response_headers = dict(response.headers)

                request_logger.info(
                    "[%s] %s %s -> %d in %.2fs", server_id, method.upper(), url, response.status_code, elapsed
                )

                if not response.content:
                    return {
                        "status": "success",
                        "data": "Operation successful, no content returned.",
                        "headers": response_headers,
                    }

                if json_response:
                    response_data = response.json()
                else:
                    response_data = response.content

                return {"status": "success", "data": response_data, "headers": response_headers}
            except httpx.HTTPStatusError as http_err:
                status_code = http_err.response.status_code
                if 500 <= status_code < 600 and attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                elapsed = time.monotonic() - start
                request_logger.info("[%s] %s %s -> %d in %.2fs", server_id, method.upper(), url, status_code, elapsed)
                raise
            except httpx.RequestError as req_err:
                if attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                elapsed = time.monotonic() - start
                request_logger.info(
                    "[%s] %s %s -> ERROR (%s) in %.2fs", server_id, method.upper(), url, req_err, elapsed
                )
                raise

        return {"status": "error", "message": f"All {max_attempts} attempts failed for {server_id}", "headers": {}}

    except Exception as e:
        logger.error(f"Redfish call failed for {server_id}: {e}")
        return {"status": "error", "message": str(e), "headers": {}}


async def _find_virtual_cd_path(server_id: str) -> str:
    """Finds and returns the path to a virtual CD/DVD drive for the server.

    Returns a cached path when available. Inserted state is not considered here.
    """
    # Return cached path if available
    cached_path = VIRTUAL_MEDIA_PATH_CACHE.get(server_id)
    if cached_path:
        return cached_path

    handler = await _get_handler(server_id)
    vm_collection_path = f"{handler.MANAGER_PATH}/VirtualMedia"
    vm_collection = await _redfish_call(server_id, "GET", vm_collection_path)

    if vm_collection.get("status") != "success":
        raise ValueError(f"Could not retrieve virtual media collection from {vm_collection_path}")

    for member in vm_collection.get("data", {}).get("Members", []):
        vm_path = member.get("@odata.id")
        if not vm_path:
            continue

        vm_details = await _redfish_call(server_id, "GET", vm_path)
        if vm_details.get("status") != "success":
            logger.warning(f"Could not get details for virtual media {vm_path} on {server_id}")
            continue

        media_types = vm_details.get("data", {}).get("MediaTypes", [])
        is_cd = any("CD" in mt or "DVD" in mt for mt in media_types)

        if is_cd:
            # Cache the first discovered CD/DVD device path for the server
            VIRTUAL_MEDIA_PATH_CACHE.setdefault(server_id, vm_path)
            return vm_path

    raise ValueError(f"No suitable virtual CD drive found on {server_id}")


async def _get_vm_path_and_state(server_id: str) -> Dict[str, Any]:
    """Returns a dict with vm_path and current state for the server's virtual media.

    Shape: {"vm_path": str, "inserted": bool, "image": Optional[str], "raw": dict}
    """
    vm_path = await _find_virtual_cd_path(server_id)
    current = await _redfish_call(server_id, "GET", vm_path)
    if current.get("status") != "success":
        raise RuntimeError(current.get("message") or "Failed to get virtual media state")
    data = current.get("data", {})
    return {
        "vm_path": vm_path,
        "inserted": data.get("Inserted", False),
        "image": data.get("Image"),
        "raw": data,
    }


async def _eject_virtual_media(server_id: str, vm_path: str) -> Dict:
    """Executes the EjectMedia action and returns the raw result dict."""
    action = f"{vm_path}/Actions/VirtualMedia.EjectMedia"
    return await _redfish_call(server_id, "POST", action, {})


async def _insert_virtual_media(server_id: str, vm_path: str, image_url: str) -> Dict:
    """Executes the InsertMedia action for the given image_url and returns result."""
    action = f"{vm_path}/Actions/VirtualMedia.InsertMedia"
    payload = {"Image": image_url, "Inserted": True}
    return await _redfish_call(server_id, "POST", action, payload)


async def _ensure_boot_once_single(
    server_id: str,
    desired_target: str,
    mode: Optional[str] = None,
    reboot: bool = False,
    reboot_type: str = "GracefulRestart",
) -> Dict:
    """Ensure next boot uses *desired_target* once for a single server.

    Args:
        server_id: Target host id.
        desired_target: Already-normalised Redfish boot target enum (e.g. "Cd", "Pxe").
        mode: Raw boot mode string ("uefi" / "legacy") — normalised internally.
        reboot: Whether to trigger a reset after setting the override.
        reboot_type: Redfish ResetType enum value.
    """
    desired_mode = None
    if mode:
        m = mode.strip().lower()
        if m in ("uefi", "legacy"):
            desired_mode = "UEFI" if m == "uefi" else "Legacy"

    try:
        handler = await _get_handler(server_id)
        # Read current boot settings
        sys_info = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
        if sys_info.get("status") != "success":
            return {"server_id": server_id, **sys_info}

        boot = (sys_info.get("data", {}) or {}).get("Boot", {}) or {}
        current_enabled = boot.get("BootSourceOverrideEnabled")
        current_target = boot.get("BootSourceOverrideTarget")
        current_mode = boot.get("BootSourceOverrideMode")

        already = (
            current_enabled == "Once"
            and current_target == desired_target
            and (desired_mode is None or current_mode == desired_mode)
        )

        if not already:
            payload: Dict[str, Any] = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": desired_target,
                }
            }
            if desired_mode:
                payload["Boot"]["BootSourceOverrideMode"] = desired_mode

            patch_result = await _redfish_call(server_id, "PATCH", handler.SYSTEM_PATH, payload)
            if patch_result.get("status") != "success":
                return {"server_id": server_id, **patch_result}

        message = (
            f"Boot override already set to {desired_target} Once"
            if already
            else f"Boot override set to {desired_target} Once"
        )

        if reboot:
            reset_path = f"{handler.SYSTEM_PATH}/Actions/ComputerSystem.Reset"
            reset_result = await _redfish_call(server_id, "POST", reset_path, {"ResetType": reboot_type})
            if reset_result.get("status") != "success":
                return {"server_id": server_id, **reset_result}
            message = f"{message}; triggered {reboot_type}"

        return {"server_id": server_id, "status": "success", "message": message}
    except Exception as e:
        return {"server_id": server_id, "status": "error", "message": str(e)}
