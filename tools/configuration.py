#!/usr/bin/env python3
"""Sanitized configuration preflight tools for batch operations."""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional

import config as cfg
from config import mcp
from helpers import _configured_port

_CAPABILITIES = {"redfish", "serial", "vnc", "network_inventory", "hardware_xml"}


def _valid_host(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        return False
    candidate = value.strip()
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        labels = candidate.rstrip(".").split(".")
        return bool(labels) and all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(char.isalnum() or char == "-" for char in label)
            for label in labels
        )


def _credential_presence(server_id: str) -> Dict[str, bool]:
    credentials = cfg.get_server_credentials(server_id) or {}
    return {
        "username": bool(credentials.get("username")),
        "password": bool(credentials.get("password")),
        "vnc_password": bool(credentials.get("vnc_password")),
    }


def _validate_server(
    server_id: str,
    capabilities: List[str],
) -> Dict[str, Any]:
    server = cfg.CONFIG.get(server_id)
    if not isinstance(server, dict):
        return {
            "server_id": server_id,
            "status": "error",
            "errors": ["host is not defined in server configuration"],
            "warnings": [],
        }

    errors: List[str] = []
    warnings: List[str] = []
    bmc_address = server.get("bmc_ip")
    if not _valid_host(bmc_address):
        errors.append("bmc_ip is missing or invalid")

    vendor = cfg.normalize_vendor(server.get("vendor"))
    if not vendor:
        if any(capability in {"serial", "hardware_xml"} for capability in capabilities):
            errors.append("vendor is required for serial and vendor-specific operations")
        else:
            warnings.append("vendor is not configured and will require Redfish discovery")
    elif vendor not in {"dell", "hpe", "supermicro"}:
        errors.append(f"unsupported vendor: {vendor}")

    credentials = _credential_presence(server_id)
    if any(capability in {"redfish", "serial", "hardware_xml"} for capability in capabilities):
        if not credentials["username"]:
            errors.append("username is missing from secrets or credential profile")
        if not credentials["password"]:
            errors.append("password is missing from secrets or credential profile")

    if any(capability in {"redfish", "hardware_xml"} for capability in capabilities):
        redfish = server.get("redfish")
        if not isinstance(redfish, dict):
            errors.append("redfish configuration is missing")
        else:
            try:
                _configured_port(redfish.get("port"), "redfish.port")
            except ValueError as exc:
                errors.append(str(exc))
        if not isinstance(server.get("verify_ssl"), bool):
            errors.append("verify_ssl must be explicitly configured as true or false")

    if "serial" in capabilities:
        serial = server.get("serial_console")
        if not isinstance(serial, dict):
            errors.append("serial_console configuration is missing")
            serial = {}
        else:
            try:
                _configured_port(serial.get("port"), "serial_console.port")
            except ValueError as exc:
                errors.append(str(exc))

        requested_transport = str((serial or {}).get("transport", "")).strip().lower()
        dell_transports = {"sol", "dell", "idrac", "idrac-ssh-sol"}
        hpe_transports = {"vsp", "hpe", "hp", "ilo", "ilo-ssh-vsp"}
        supported_transports = {"auto", *dell_transports, *hpe_transports}
        if (serial or {}).get("attach_command"):
            errors.append("custom serial_console.attach_command is not supported")
        if requested_transport not in supported_transports:
            errors.append(f"unsupported serial console transport: {requested_transport}")
        elif vendor == "supermicro":
            errors.append("serial console supports Dell SOL and HPE VSP only")
        elif vendor == "dell" and requested_transport in hpe_transports:
            errors.append("configured serial console transport conflicts with Dell vendor")
        elif vendor == "hpe" and requested_transport in dell_transports:
            errors.append("configured serial console transport conflicts with HPE vendor")

    if "vnc" in capabilities:
        vnc_port = server.get("vnc_port")
        if not vnc_port:
            errors.append("vnc_port is missing")
        else:
            try:
                _configured_port(vnc_port, "vnc_port")
            except ValueError as exc:
                errors.append(str(exc))
        if not credentials["vnc_password"]:
            errors.append("vnc_password is missing from secrets or credential profile")

    expected_macs = []
    for key in ("expected_host_macs", "host_macs"):
        if isinstance(server.get(key), list):
            expected_macs.extend(server[key])
    for key in ("expected_host_mac", "host_mac", "mac"):
        if server.get(key):
            expected_macs.append(server[key])
    for value in expected_macs:
        compact = "".join(char for char in str(value) if char.isalnum())
        if len(compact) != 12 or any(char not in "0123456789abcdefABCDEF" for char in compact):
            errors.append(f"invalid configured host MAC: {value}")

    bmc_mac = server.get("bmc_mac") or server.get("bmc_mac_address")
    if bmc_mac:
        compact = "".join(char for char in str(bmc_mac) if char.isalnum())
        if len(compact) != 12 or any(char not in "0123456789abcdefABCDEF" for char in compact):
            errors.append(f"invalid configured BMC MAC: {bmc_mac}")

    if "hardware_xml" in capabilities and vendor and vendor != "dell":
        errors.append("Dell hardware inventory XML is unsupported for this vendor")

    return {
        "server_id": server_id,
        "status": "success" if not errors else "error",
        "bmc_address": bmc_address,
        "vendor": vendor or None,
        "capabilities": capabilities,
        "credential_fields_present": credentials,
        "errors": errors,
        "warnings": warnings,
    }


@mcp.tool(
    description=(
        "Preflight host definitions and credential presence for Redfish, serial, VNC, network "
        "inventory, or Dell XML operations. Secret values are never returned."
    )
)
async def validate_host_configuration(
    server_ids: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    cfg._load_config()
    if server_ids is None:
        selected = sorted(cfg.CONFIG)
    elif not isinstance(server_ids, list) or not server_ids:
        return {"status": "error", "message": "server_ids must be a non-empty list when provided"}
    else:
        selected = list(dict.fromkeys(str(server_id).strip() for server_id in server_ids))
        if any(not server_id for server_id in selected):
            return {"status": "error", "message": "Every server_id must be non-empty"}

    requested_capabilities = capabilities or ["redfish"]
    if not isinstance(requested_capabilities, list) or not requested_capabilities:
        return {"status": "error", "message": "capabilities must be a non-empty list"}
    normalized_capabilities = list(
        dict.fromkeys(str(capability).strip().lower() for capability in requested_capabilities)
    )
    unsupported = sorted(set(normalized_capabilities) - _CAPABILITIES)
    if unsupported:
        return {
            "status": "error",
            "message": f"Unsupported capabilities: {', '.join(unsupported)}",
            "supported": sorted(_CAPABILITIES),
        }

    results = [_validate_server(server_id, normalized_capabilities) for server_id in selected]
    ready = [result["server_id"] for result in results if result["status"] == "success"]
    failed = [result["server_id"] for result in results if result["status"] != "success"]
    return {
        "status": "success" if not failed else ("partial" if ready else "error"),
        "ready": ready,
        "failed": failed,
        "results": results,
    }
