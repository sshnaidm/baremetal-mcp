#!/usr/bin/env python3
"""
Dell-specific tools - firmware updates, hardware inventory export, ISO listing.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

import config as cfg
from config import mcp, ISOS, _load_config, _flatten_dict, logger
from helpers import _redfish_call, _get_handler, _redfish_url_authority, _response_header
from cache import RESPONSE_CACHE

_COLLECTION_LOCKS: Dict[str, asyncio.Lock] = {}
_FAILED_TASK_STATES = {
    "cancelled",
    "canceled",
    "exception",
    "failed",
    "interrupted",
    "killed",
}
_SUCCESS_TASK_STATES = {"completed", "success", "succeeded"}
_ALLOWED_INVENTORY_ROOTS = {"cim", "inventory"}


@mcp.tool(description="List all available ISOs with their URLs.")
async def list_isos() -> Dict:
    """List all available ISOs with their URLs as flattened key-value pairs.

    Keys are formed by joining the nested YAML structure keys with underscores.
    Example key: dell_model_640_idrac_version_5
    Example value: https://firmware.example/iDRAC-with-Lifecycle-Controller_Firmware_...

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
        => {"status": "success", "data": "https://firmware.example/iDRAC-with-Lifecycle-Controller_Firmware_....EXE"}
        dell_list_url("640", "bios", "1")
        => {"status": "success", "data": "https://firmware.example/BIOS_example.EXE"}
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hardware_inventory_root() -> Path:
    """Return the configured root under which XML collections may be written."""
    _load_config()
    configured = os.getenv("HARDWARE_INVENTORY_DIR") or cfg.SETTINGS.get(
        "hardware_inventory_dir",
        getattr(cfg, "HARDWARE_INVENTORY_DIR", "data/hardware_inventory"),
    )
    return Path(os.path.expandvars(str(configured))).expanduser().resolve()


def _collection_directory(collection: Optional[str]) -> Path:
    root = _hardware_inventory_root()
    if collection is None:
        return root
    if not isinstance(collection, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", collection):
        raise ValueError("collection must be one safe path component containing only letters, numbers, '.', '_' or '-'")
    if collection in {".", ".."}:
        raise ValueError("collection must not be '.' or '..'")
    destination = (root / collection).resolve()
    if destination.parent != root:
        raise ValueError("collection must remain directly under the hardware inventory root")
    return destination


def _safe_server_filename(server_id: str) -> str:
    return f"dell_{quote(server_id, safe='-_.')}.xml"


def _safe_redfish_location(server_id: str, location: str) -> str:
    """Reject task redirects that could send BMC credentials to another host."""
    value = str(location).strip()
    if not value:
        raise ValueError("Hardware inventory location was empty")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        configured = urlsplit(f"//{_redfish_url_authority(cfg.CONFIG.get(server_id, {}))}")
        same_host = (parsed.hostname or "").casefold() == (configured.hostname or "").casefold()
        same_port = parsed.port is None or parsed.port == configured.port
        if parsed.scheme.lower() != "https" or not same_host or not same_port:
            raise ValueError("Hardware inventory location points outside the configured BMC")
        value = parsed.path or "/"
        if parsed.query:
            value += f"?{parsed.query}"
    elif not value.startswith("/"):
        value = "/" + value
    return value


def _validate_remote_url(value: Any, *, label: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return f"{label} must be a non-empty HTTP or HTTPS URL"
    url = value.strip()
    if len(url) > 4096 or any(character.isspace() or ord(character) < 32 for character in url):
        return f"{label} contains whitespace, control characters, or is too long"
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return f"{label} must be an absolute HTTP or HTTPS URL"
    if parsed.username is not None or parsed.password is not None:
        return f"{label} must not contain embedded credentials"
    return None


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _xml_details(payload: Any) -> tuple[bytes, str, str]:
    if isinstance(payload, bytes):
        xml_bytes = payload
    elif isinstance(payload, str):
        xml_bytes = payload.encode("utf-8")
    else:
        raise ValueError("Hardware inventory response was not XML text")

    try:
        max_bytes = int(getattr(cfg, "HARDWARE_INVENTORY_MAX_BYTES", 50 * 1024 * 1024))
    except (TypeError, ValueError):
        max_bytes = 50 * 1024 * 1024
    max_bytes = max(1024, min(max_bytes, 268_435_456))
    if not xml_bytes:
        raise ValueError("Hardware inventory response was empty")
    if len(xml_bytes) > max_bytes:
        raise ValueError(f"Hardware inventory XML exceeds the configured {max_bytes}-byte limit")
    xml_prefix = xml_bytes[:8192].upper()
    if b"<!DOCTYPE" in xml_prefix or b"<!ENTITY" in xml_prefix:
        raise ValueError("Hardware inventory XML must not contain DTD or entity declarations")

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Hardware inventory response is not valid XML: {exc}") from exc
    root_tag = str(root.tag).rsplit("}", 1)[-1]
    if root_tag.casefold() not in _ALLOWED_INVENTORY_ROOTS:
        raise ValueError(
            f"Hardware inventory response has unexpected XML root {root_tag!r}; expected Dell CIM or Inventory"
        )
    xml_text = xml_bytes.decode("utf-8-sig", errors="replace")
    return xml_bytes, xml_text, root_tag


def _xml_system_service_tags(xml_bytes: bytes) -> set[str]:
    """Extract only DCIM_SystemView.ServiceTag values from Dell CIM XML."""
    root = ET.fromstring(xml_bytes)
    values: set[str] = set()
    for instance in root.iter():
        if str(instance.tag).rsplit("}", 1)[-1].casefold() != "instance":
            continue
        class_name = next(
            (
                str(value)
                for name, value in instance.attrib.items()
                if str(name).rsplit("}", 1)[-1].casefold() == "classname"
            ),
            "",
        )
        if class_name.casefold() != "dcim_systemview":
            continue
        for property_element in instance:
            if str(property_element.tag).rsplit("}", 1)[-1].casefold() != "property":
                continue
            property_name = next(
                (
                    str(value)
                    for name, value in property_element.attrib.items()
                    if str(name).rsplit("}", 1)[-1].casefold() == "name"
                ),
                "",
            )
            if property_name.casefold() != "servicetag":
                continue
            for value_element in property_element:
                if str(value_element.tag).rsplit("}", 1)[-1].casefold() != "value":
                    continue
                value = str(value_element.text or "").strip()
                if value:
                    values.add(value)
    return values


def _validate_xml_identity(xml_bytes: bytes, identity: Dict[str, Any]) -> Dict[str, Any]:
    """Bind downloaded or cached XML to the live BMC identity before it is used."""
    trusted = {
        str(value).strip()
        for value in (
            identity.get("expected_serial_number"),
            identity.get("observed_serial_number"),
        )
        if str(value or "").strip()
    }
    if not trusted:
        raise ValueError("Cannot bind hardware inventory XML because the live BMC serial is unavailable")
    embedded = _xml_system_service_tags(xml_bytes)
    if not embedded:
        raise ValueError("Dell hardware inventory XML does not contain DCIM_SystemView ServiceTag")
    trusted_folded = {value.casefold() for value in trusted}
    embedded_folded = {value.casefold() for value in embedded}
    if len(trusted_folded) != 1:
        raise ValueError("Live BMC identity contains conflicting serial numbers")
    if len(embedded_folded) != 1:
        raise ValueError("Dell hardware inventory XML contains conflicting DCIM_SystemView ServiceTag values")
    if embedded_folded != trusted_folded:
        raise ValueError("Dell hardware inventory XML service tag does not match the live BMC identity")
    return {
        "status": "verified",
        "service_tags": sorted(embedded),
        "matched_serial_numbers": sorted(value for value in embedded if value.casefold() in trusted_folded),
    }


def _task_document(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return payload if isinstance(payload, dict) else None


def _task_state(document: Optional[Dict[str, Any]]) -> Optional[str]:
    if not document:
        return None
    candidates = [
        document.get("TaskState"),
        document.get("JobState"),
        document.get("State"),
    ]
    oem = document.get("Oem")
    if isinstance(oem, dict):
        for vendor_data in oem.values():
            if isinstance(vendor_data, dict):
                candidates.extend((vendor_data.get("TaskState"), vendor_data.get("JobState")))
    for candidate in candidates:
        if candidate is not None:
            return str(candidate).strip().lower()
    return None


def _document_location(document: Optional[Dict[str, Any]]) -> Optional[str]:
    if not document:
        return None
    for key in ("TaskMonitor", "Location", "location", "@odata.id"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _retry_delay(response: Dict[str, Any], fallback: float) -> float:
    raw = _response_header(response, "Retry-After")
    if raw:
        try:
            return max(0.1, min(float(raw), 30.0))
        except ValueError:
            pass
    return fallback


async def _download_exported_xml(
    server_id: str,
    initial_location: str,
    *,
    poll_interval_seconds: float,
    timeout_seconds: float,
) -> tuple[bytes, str, str]:
    """Follow a Dell task monitor until it returns validated inventory XML."""
    deadline = time.monotonic() + timeout_seconds
    location = _safe_redfish_location(server_id, initial_location)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for hardware inventory XML after {timeout_seconds:g} seconds")

        response = await _redfish_call(
            server_id,
            "GET",
            location,
            timeout=max(1.0, min(remaining, float(cfg.DEFAULT_TIMEOUT))),
            json_response=False,
        )
        if response.get("status") != "success":
            raise RuntimeError(response.get("message") or "Failed to retrieve hardware inventory XML")

        redirected = _response_header(response, "Location")

        payload = response.get("data")
        try:
            return _xml_details(payload)
        except ValueError as xml_error:
            document = _task_document(payload)
            if document is None:
                raise ValueError(
                    f"Hardware inventory task returned neither valid Dell XML nor a JSON task document: {xml_error}"
                ) from xml_error

        state = _task_state(document)
        if state in _FAILED_TASK_STATES:
            raise RuntimeError(f"Hardware inventory export task ended in state '{state}'")

        document_location = _document_location(document)
        next_location = redirected or document_location
        safe_next_location = _safe_redfish_location(server_id, next_location) if next_location else None
        if safe_next_location and safe_next_location != location:
            location = safe_next_location
        elif state in _SUCCESS_TASK_STATES:
            raise RuntimeError("Hardware inventory export completed without an XML download location")

        delay = _retry_delay(response, poll_interval_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        await asyncio.sleep(min(delay, remaining))


def _success_metadata(
    server_id: str,
    path: Path,
    xml_bytes: bytes,
    root_tag: str,
    *,
    cached: bool,
    include_xml: bool,
    xml_text: Optional[str] = None,
    identity: Optional[Dict[str, Any]] = None,
    xml_identity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    host_config = cfg.CONFIG.get(server_id, {})
    try:
        modified = (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except OSError:
        modified = _utc_now()
    result: Dict[str, Any] = {
        "server_id": server_id,
        "status": "success",
        "vendor": cfg.normalize_vendor(host_config.get("vendor")) or "dell",
        "bmc_address": host_config.get("bmc_ip"),
        "file_path": str(path),
        "bytes": len(xml_bytes),
        "sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "root_tag": root_tag,
        "cached": cached,
        "observed_at": modified,
        "identity": identity or {"status": "unverified"},
        "xml_identity": xml_identity or {"status": "unverified"},
    }
    if include_xml:
        result["data"] = xml_text if xml_text is not None else xml_bytes.decode("utf-8-sig", errors="replace")
    return result


async def _dell_identity(server_id: str, handler: Any) -> Dict[str, Any]:
    response = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
    if response.get("status") != "success" or not isinstance(response.get("data"), dict):
        return {
            "status": "unverified",
            "message": (
                "System identity was unavailable" + (f": {response.get('message')}" if response.get("message") else "")
            ),
        }
    data = response["data"]
    observed = str(data.get("SerialNumber") or "").strip()
    host_config = cfg.CONFIG.get(server_id, {})
    expected = str(host_config.get("serial_number") or host_config.get("service_tag") or "").strip()
    if not observed:
        return {
            "status": "unverified",
            "expected_serial_number": expected or None,
            "observed_serial_number": None,
            "message": "System identity was unavailable: Redfish returned no system serial number",
        }
    if expected and observed and expected.casefold() != observed.casefold():
        return {
            "status": "mismatch",
            "expected_serial_number": expected,
            "observed_serial_number": observed,
            "message": "Configured host identity does not match the target BMC",
        }
    return {
        "status": "verified" if expected and observed else "observed",
        "expected_serial_number": expected or None,
        "observed_serial_number": observed or None,
        "model": data.get("Model"),
        "manufacturer": data.get("Manufacturer"),
        "uuid": data.get("UUID"),
    }


async def _export_hardware_inventory_single(
    server_id: str,
    destination: Path,
    *,
    refresh: bool,
    include_xml: bool,
    poll_interval_seconds: float,
    timeout_seconds: float,
) -> Dict[str, Any]:
    path = destination / _safe_server_filename(server_id)
    submission_started = False
    try:
        handler = await _get_handler(server_id)
        if not handler.HW_INVENTORY_PATH:
            return {
                "server_id": server_id,
                "status": "error",
                "unsupported": True,
                "message": "Hardware inventory XML export is not supported for this vendor",
            }
        identity = await _dell_identity(server_id, handler)
        if identity.get("status") in {"mismatch", "unverified"}:
            return {
                "server_id": server_id,
                "status": "error",
                "identity_mismatch": identity.get("status") == "mismatch",
                "identity": identity,
                "message": identity.get("message") or "Live BMC identity could not be verified",
            }

        if not refresh and path.exists():
            try:
                age = time.time() - path.stat().st_mtime
                if age <= cfg.TTL_DISK_CACHE:
                    cached_bytes, cached_text, root_tag = _xml_details(path.read_bytes())
                    xml_identity = _validate_xml_identity(cached_bytes, identity)
                    return _success_metadata(
                        server_id,
                        path,
                        cached_bytes,
                        root_tag,
                        cached=True,
                        include_xml=include_xml,
                        xml_text=cached_text,
                        identity=identity,
                        xml_identity=xml_identity,
                    )
            except (OSError, ValueError) as exc:
                logger.warning("Ignoring invalid hardware inventory cache for %s: %s", server_id, exc)

        response = await _redfish_call(
            server_id,
            "POST",
            handler.HW_INVENTORY_PATH,
            payload={"ShareType": "Local"},
            timeout=max(1.0, min(timeout_seconds, float(cfg.DEFAULT_TIMEOUT))),
        )
        if response.get("status") != "success":
            result = {"server_id": server_id, **response}
            result.setdefault("message", "Failed to start hardware inventory export")
            return result
        submission_started = True

        location = _response_header(response, "Location") or _document_location(
            response.get("data") if isinstance(response.get("data"), dict) else None
        )
        logger.info("Hardware inventory path for %s: %s", server_id, location)
        if not location:
            return {
                "server_id": server_id,
                "status": "error",
                "message": "No hardware inventory path found in the export response",
                "remote_request_sent": True,
                "submission_accepted": True,
                "retry_safe": False,
                "outcome_unknown": True,
            }

        xml_bytes, xml_text, root_tag = await _download_exported_xml(
            server_id,
            location,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        xml_identity = _validate_xml_identity(xml_bytes, identity)
        _atomic_write(path, xml_bytes)
        return _success_metadata(
            server_id,
            path,
            xml_bytes,
            root_tag,
            cached=False,
            include_xml=include_xml,
            xml_text=xml_text,
            identity=identity,
            xml_identity=xml_identity,
        )
    except Exception as exc:
        result = {"server_id": server_id, "status": "error", "message": str(exc)}
        if submission_started:
            result.update(
                remote_request_sent=True,
                submission_accepted=True,
                retry_safe=False,
                outcome_unknown=True,
            )
        return result


def _manifest_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in result.items() if key != "data"}


@mcp.tool(
    description=(
        "Export validated Dell hardware inventory XML for multiple servers with bounded concurrency, "
        "task polling, atomic persistence, and a manifest. Non-Dell hosts are reported as unsupported."
    )
)
async def export_hardware_inventory_xml(
    server_ids: List[str],
    collection: Optional[str] = None,
    refresh: bool = False,
    concurrency: Optional[int] = None,
    include_xml: bool = False,
    poll_interval_seconds: Optional[float] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Export Dell XML under the configured, constrained inventory root."""
    _load_config()
    if concurrency is None:
        concurrency = int(getattr(cfg, "BATCH_CONCURRENCY", 4))
    if poll_interval_seconds is None:
        poll_interval_seconds = float(getattr(cfg, "HARDWARE_INVENTORY_POLL_INTERVAL", 2.0))
    if timeout_seconds is None:
        timeout_seconds = float(getattr(cfg, "HARDWARE_INVENTORY_TIMEOUT", 180.0))
    if not isinstance(server_ids, list) or not server_ids:
        return {"status": "error", "message": "server_ids must be a non-empty list"}
    if any(not isinstance(server_id, str) or not server_id.strip() for server_id in server_ids):
        return {"status": "error", "message": "Every server_id must be a non-empty string"}
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
        return {"status": "error", "message": "concurrency must be an integer between 1 and 16"}
    try:
        poll_interval = float(poll_interval_seconds)
        overall_timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        return {"status": "error", "message": "poll and timeout values must be numeric"}
    if not 0.1 <= poll_interval <= 30:
        return {"status": "error", "message": "poll_interval_seconds must be between 0.1 and 30"}
    if not 1 <= overall_timeout <= 3600:
        return {"status": "error", "message": "timeout_seconds must be between 1 and 3600"}

    try:
        destination = _collection_directory(collection)
        destination.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    unique_server_ids = list(dict.fromkeys(server_id.strip() for server_id in server_ids))
    lock = _COLLECTION_LOCKS.setdefault(str(destination), asyncio.Lock())
    async with lock:
        semaphore = asyncio.Semaphore(concurrency)

        async def run(server_id: str) -> Dict[str, Any]:
            async with semaphore:
                return await _export_hardware_inventory_single(
                    server_id,
                    destination,
                    refresh=refresh,
                    include_xml=include_xml,
                    poll_interval_seconds=poll_interval,
                    timeout_seconds=overall_timeout,
                )

        results = await asyncio.gather(*(run(server_id) for server_id in unique_server_ids))
        succeeded = sum(result.get("status") == "success" for result in results)
        unsupported = sum(result.get("unsupported") is True for result in results)
        failed = len(results) - succeeded
        status = "success" if failed == 0 else ("partial" if succeeded else "error")
        manifest_path = destination / "manifest.json"
        manifest = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "collection": collection,
            "directory": str(destination),
            "server_ids": unique_server_ids,
            "summary": {
                "requested": len(unique_server_ids),
                "succeeded": succeeded,
                "failed": failed,
                "unsupported": unsupported,
            },
            "results": [_manifest_result(result) for result in results],
        }
        try:
            manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=False) + "\n").encode("utf-8")
            _atomic_write(manifest_path, manifest_bytes)
        except OSError as exc:
            return {
                "status": "error",
                "message": f"XML files were processed but the manifest could not be written: {exc}",
                "directory": str(destination),
                "results": results,
            }

    return {
        "status": status,
        "collection": collection,
        "directory": str(destination),
        "manifest_path": str(manifest_path),
        "server_ids": unique_server_ids,
        "requested": len(unique_server_ids),
        "succeeded": succeeded,
        "failed": failed,
        "unsupported": unsupported,
        "duplicates_ignored": len(server_ids) - len(unique_server_ids),
        "results": results,
    }


@mcp.tool(
    description=(
        "Compatibility interface for Dell hardware inventory XML export. Returns raw XML in each "
        "successful result; prefer export_hardware_inventory_xml for fleet exports."
    )
)
async def dell_export_hardware_inventory(server_ids: List[str]) -> List[Dict]:
    """Compatibility wrapper retaining the historical list-with-raw-XML result."""
    result = await export_hardware_inventory_xml(server_ids, include_xml=True)
    if "results" in result:
        return result["results"]
    return [
        {"server_id": server_id, "status": "error", "message": result.get("message", "Export failed")}
        for server_id in server_ids
    ]


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
            "server-id", "https://firmware.example/iDRAC-firmware.EXE")
        => {"status": "success", "message": "Firmware update initiated", "job_id": "JID_123456789"}
    """
    try:
        handler = await _get_handler(server_id)

        # Use DMTF SimpleUpdate action with ImageURI for remote URL updates
        simple_update_path = "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"

        url_error = _validate_remote_url(url, label="Firmware URL")
        if url_error:
            return {"server_id": server_id, "status": "error", "message": url_error}
        if not isinstance(reboot, bool):
            return {"server_id": server_id, "status": "error", "message": "reboot must be a boolean"}

        payload = {"ImageURI": url, "@Redfish.OperationApplyTime": "Immediate"}

        result = await _redfish_call(server_id, "POST", simple_update_path, payload)

        if result.get("status") == "success":
            RESPONSE_CACHE.invalidate_prefix(f"{server_id}:firmware_inventory")
            # Extract job ID from Location header
            job_id = (_response_header(result, "Location") or "").split("/")[-1]
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
