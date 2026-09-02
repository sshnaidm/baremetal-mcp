#!/usr/bin/env python3
"""
Internal helper functions for Redfish API calls.
"""

import asyncio
import ipaddress
import re
import time
import httpx
from urllib.parse import unquote, urlsplit
from typing import Any, Dict, Optional

import config as cfg
from config import (
    CONFIG,
    VIRTUAL_MEDIA_PATH_CACHE,
    logger,
    request_logger,
    _load_config,
)
from handlers import VENDOR_MAP

# Lazily-initialised async HTTP clients.  Keep verified and unverified TLS
# traffic on different clients so one host's setting can never weaken another
# host's connection.
_http_client: Optional[httpx.AsyncClient] = None
_verified_http_client: Optional[httpx.AsyncClient] = None

# Cache handler instances so we don't re-create one on every API call.
_HANDLER_CACHE: Dict[str, Any] = {}

_HOST_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _get_http_client(verify: bool = False) -> httpx.AsyncClient:
    """Return a client whose TLS verification policy exactly matches *verify*."""
    global _http_client, _verified_http_client
    if verify:
        if _verified_http_client is None:
            _verified_http_client = httpx.AsyncClient(
                verify=True,
                timeout=httpx.Timeout(cfg.DEFAULT_TIMEOUT),
            )
        return _verified_http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(cfg.DEFAULT_TIMEOUT),
        )
    return _http_client


def _verify_ssl_setting(server_config: Dict[str, Any]) -> bool:
    """Return one host's explicit TLS policy, rejecting ambiguous YAML values."""
    if "verify_ssl" not in server_config:
        raise ValueError("verify_ssl must be explicitly configured for the Redfish endpoint")
    value = server_config.get("verify_ssl")
    if not isinstance(value, bool):
        raise ValueError("verify_ssl must be a boolean")
    return value


def _bmc_url_authority(value: Any) -> str:
    """Validate one configured BMC host and return its safe URL authority."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("bmc_ip must be a non-empty hostname or IP address")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("bmc_ip must not contain whitespace or control characters")
    if any(char in value for char in "/\\?#@[]%"):
        raise ValueError(
            "bmc_ip must be a hostname or bracketless IP address without scheme, "
            "userinfo, port, path, query, or fragment"
        )

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        host_without_final_dot = value[:-1] if value.endswith(".") else value
        numeric_labels = host_without_final_dot.split(".")
        numeric_like = bool(numeric_labels) and all(
            re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)", label) for label in numeric_labels
        )
        if ":" in value or numeric_like:
            raise ValueError("bmc_ip is not a valid IP address")
        try:
            hostname = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("bmc_ip is not a valid hostname") from exc
        if len(hostname) > 253:
            raise ValueError("bmc_ip is not a valid hostname")
        labels = hostname[:-1].split(".") if hostname.endswith(".") else hostname.split(".")
        if not labels or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
            raise ValueError("bmc_ip is not a valid hostname")
        return hostname

    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address}]"
    return str(address)


def _configured_port(value: Any, label: str) -> int:
    """Validate an explicitly configured TCP port without protocol defaults."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be explicitly configured between 1 and 65535")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be explicitly configured between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{label} must be explicitly configured between 1 and 65535")
    return port


def _redfish_url_authority(server_config: Dict[str, Any]) -> str:
    """Build one Redfish authority from YAML-provided host and port fields."""
    host = _bmc_url_authority(server_config.get("bmc_ip"))
    redfish = server_config.get("redfish")
    if not isinstance(redfish, dict):
        raise ValueError("redfish configuration with an explicit port is required")
    port = _configured_port(redfish.get("port"), "redfish.port")
    return f"{host}:{port}"


def _origin_relative_path(path: str) -> str:
    """Validate and normalize a path without ever accepting another origin."""
    if not isinstance(path, str) or not path:
        raise ValueError("Redfish path must be a non-empty string")
    if path != path.strip() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in path):
        raise ValueError("Redfish path must not contain whitespace or control characters")
    if "\\" in path:
        raise ValueError("Redfish path must use forward slashes")

    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        raise ValueError("Redfish path must be origin-relative; absolute and network-path URLs are forbidden")
    if parsed.fragment:
        raise ValueError("Redfish path must not contain a fragment")
    if not parsed.path:
        raise ValueError("Redfish path must include a resource path")
    for segment in parsed.path.split("/"):
        decoded = segment
        while True:
            next_decoded = unquote(decoded)
            if next_decoded in {".", ".."}:
                raise ValueError("Redfish path must not contain dot-segments")
            if next_decoded == decoded:
                break
            decoded = next_decoded
        if "/" in decoded or "\\" in decoded:
            raise ValueError("Redfish path must not contain encoded path separators")
    return path if path.startswith("/") else f"/{path}"


async def _get_vendor_from_api(
    bmc_ip: str,
    redfish_port: Any,
    verify_ssl: bool = False,
) -> str:
    """Detect vendor by querying the Redfish API asynchronously."""
    authority = f"{_bmc_url_authority(bmc_ip)}:{_configured_port(redfish_port, 'redfish.port')}"
    logger.info("Detecting vendor for %s...", authority)
    url = f"https://{authority}/redfish/v1"
    try:
        client = _get_http_client(verify=True) if verify_ssl else _get_http_client()
        response = await client.get(
            url,
            timeout=cfg.DEFAULT_TIMEOUT,
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
    bmc_ip = server_config.get("bmc_ip")
    _redfish_url_authority(server_config)

    creds = cfg.get_server_credentials(server_id) or {}
    username = creds.get("username")
    password = creds.get("password")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password.strip():
        raise ValueError(f"Missing BMC username or password for server '{server_id}'")

    verify_ssl = _verify_ssl_setting(server_config)

    configured_vendor = server_config.get("vendor")
    vendor = cfg.normalize_vendor(configured_vendor)
    if not vendor:
        vendor = cfg.normalize_vendor(
            await _get_vendor_from_api(
                bmc_ip,
                server_config["redfish"]["port"],
                verify_ssl=verify_ssl,
            )
        )
        # Cache the detected vendor for future calls
        CONFIG[server_id]["vendor"] = vendor
    elif vendor != configured_vendor:
        CONFIG[server_id]["vendor"] = vendor

    handler_class = VENDOR_MAP.get(vendor.lower())
    if not handler_class:
        raise ValueError(f"Unsupported vendor: {vendor}")

    handler = handler_class(username, password)
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
    method_name = method.upper() if isinstance(method, str) else ""
    read_only = method_name in {"GET", "HEAD", "OPTIONS"}
    request_attempted = False
    response_received = False
    try:
        normalized_path = _origin_relative_path(path)
        if not method_name:
            raise ValueError("Redfish method must be a non-empty string")
        handler = await _get_handler(server_id)
        server_config = CONFIG[server_id]
        authority = _redfish_url_authority(server_config)
        url = f"https://{authority}{normalized_path}"

        request_args = handler.get_request_args()
        request_args["timeout"] = cfg.DEFAULT_TIMEOUT if timeout is None else timeout
        if payload is not None:
            request_args["json"] = payload

        verify_ssl = _verify_ssl_setting(server_config)
        client = _get_http_client(verify=True) if verify_ssl else _get_http_client()
        # Retrying an action after a response or transport failure can execute
        # it twice.  Automatic retries are therefore limited to methods whose
        # semantics are idempotent and read-only in this server.
        max_attempts = cfg.MAX_RETRIES if read_only else 1
        response_headers = {}
        start = time.monotonic()
        for attempt in range(1, max_attempts + 1):
            try:
                request_attempted = True
                response = await client.request(method_name, url, **request_args)
                response_received = True
                response.raise_for_status()
                elapsed = time.monotonic() - start
                # httpx exposes case-insensitive headers, but converting them to
                # a plain dict normalizes their names to lowercase. Keep that
                # normalization as the public result contract and use
                # ``_response_header`` at call sites that need a specific
                # header such as Location or Retry-After.
                response_headers = {key.lower(): value for key, value in response.headers.items()}

                request_logger.info(
                    "[%s] %s %s -> %d in %.2fs", server_id, method_name, url, response.status_code, elapsed
                )

                if not response.content:
                    result = {
                        "status": "success",
                        "data": "Operation successful, no content returned.",
                        "headers": response_headers,
                        "status_code": response.status_code,
                    }
                    if not read_only:
                        result.update(
                            remote_request_sent=True,
                            retry_safe=False,
                            outcome_unknown=False,
                        )
                    return result

                if json_response:
                    response_data = response.json()
                else:
                    response_data = response.content

                result = {
                    "status": "success",
                    "data": response_data,
                    "headers": response_headers,
                    "status_code": response.status_code,
                }
                if not read_only:
                    result.update(
                        remote_request_sent=True,
                        retry_safe=False,
                        outcome_unknown=False,
                    )
                return result
            except httpx.HTTPStatusError as http_err:
                status_code = http_err.response.status_code
                if 500 <= status_code < 600 and attempt < max_attempts:
                    await asyncio.sleep(cfg.BACKOFF_FACTOR * attempt)
                    continue
                elapsed = time.monotonic() - start
                request_logger.info("[%s] %s %s -> %d in %.2fs", server_id, method_name, url, status_code, elapsed)
                error_headers = {key.lower(): value for key, value in http_err.response.headers.items()}
                try:
                    error_data: Any = http_err.response.json()
                except (ValueError, UnicodeDecodeError):
                    error_data = http_err.response.text
                result = {
                    "status": "error",
                    "message": f"HTTP {status_code}: {http_err.response.reason_phrase}",
                    "data": error_data,
                    "headers": error_headers,
                    "status_code": status_code,
                }
                if not read_only:
                    result.update(
                        remote_request_sent=True,
                        retry_safe=False,
                        outcome_unknown=500 <= status_code < 600,
                    )
                return result
            except httpx.RequestError as req_err:
                if attempt < max_attempts:
                    await asyncio.sleep(cfg.BACKOFF_FACTOR * attempt)
                    continue
                elapsed = time.monotonic() - start
                request_logger.info("[%s] %s %s -> ERROR (%s) in %.2fs", server_id, method_name, url, req_err, elapsed)
                if not read_only:
                    return {
                        "status": "error",
                        "message": str(req_err),
                        "headers": {},
                        "status_code": None,
                        "remote_request_sent": None,
                        "retry_safe": False,
                        "outcome_unknown": True,
                    }
                raise

        result = {
            "status": "error",
            "message": f"All {max_attempts} attempts failed for {server_id}",
            "headers": {},
            "status_code": None,
        }
        if not read_only:
            result.update(
                remote_request_sent=None if request_attempted else False,
                retry_safe=not request_attempted,
                outcome_unknown=request_attempted,
            )
        return result

    except Exception as e:
        logger.error(f"Redfish call failed for {server_id}: {e}")
        result = {"status": "error", "message": str(e), "headers": {}, "status_code": None}
        if not read_only:
            result.update(
                remote_request_sent=True if response_received else None if request_attempted else False,
                retry_safe=not request_attempted,
                outcome_unknown=request_attempted,
            )
        return result


def _response_header(response: Dict[str, Any], name: str) -> Optional[str]:
    """Return one response header without depending on its original casing."""
    headers = response.get("headers") or {}
    if not isinstance(headers, dict):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


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

        return {
            "server_id": server_id,
            "status": "success",
            "message": message,
            "power_state": (sys_info.get("data", {}) or {}).get("PowerState"),
        }
    except Exception as e:
        return {"server_id": server_id, "status": "error", "message": str(e)}
