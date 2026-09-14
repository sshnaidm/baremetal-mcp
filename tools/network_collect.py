#!/usr/bin/env python3
"""Collect normalized Linux network facts through guarded BMC serial consoles."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config as cfg
from config import mcp
from helpers import _get_handler, _redfish_call
from tools.network_inventory import save_network_inventory
from tools.serial_console import _run_serial_commands

_MAX_BATCH_SIZE = 64
_MAX_CONCURRENCY = 12
_MAX_INTERFACES = 48
_IFNAME_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
_PCI_RE = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$")

_BASE_PROBES: List[Tuple[str, str]] = [
    ("link", "ip -o link show"),
    (
        "sysfs",
        'for p in /sys/class/net/*; do n=${p##*/}; [ "$n" = lo ] && continue; '
        '[ -e "$p/device" ] || continue; printf \'SYSFS\\t%s\\t\' "$n"; '
        "cat \"$p/carrier\" 2>/dev/null | tr -d '\\n'; printf '\\t'; "
        "cat \"$p/operstate\" 2>/dev/null | tr -d '\\n'; printf '\\t'; "
        "cat \"$p/phys_port_name\" 2>/dev/null | tr -d '\\n'; printf '\\t'; "
        "cat \"$p/dev_port\" 2>/dev/null | tr -d '\\n'; printf '\\t'; "
        'basename "$(readlink -f "$p/device")"; done',
    ),
    (
        "pci",
        "if command -v lspci >/dev/null 2>&1; then "
        "lspci -Dnn | grep -Ei 'Ethernet controller|Network controller' || true; "
        "else printf 'LSPCI_UNAVAILABLE\\n'; fi",
    ),
    (
        "system",
        "printf 'HOSTNAME\\t'; hostname 2>/dev/null; printf 'SERIAL\\t'; "
        "cat /sys/class/dmi/id/product_serial 2>/dev/null; printf 'KERNEL\\t'; uname -srm",
    ),
    (
        "lldp",
        "if command -v lldpctl >/dev/null 2>&1; then lldpctl -f keyvalue 2>/dev/null; "
        "else printf 'LLDP_UNAVAILABLE\\n'; fi",
    ),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_ids(server_ids: List[str]) -> tuple[Optional[List[str]], Optional[str], int]:
    if not isinstance(server_ids, list) or not server_ids:
        return None, "server_ids must be a non-empty list", 0
    if len(server_ids) > _MAX_BATCH_SIZE:
        return None, f"At most {_MAX_BATCH_SIZE} hosts may be collected at once", 0
    ordered: List[str] = []
    seen: set[str] = set()
    for value in server_ids:
        if not isinstance(value, str) or not value.strip():
            return None, "Every server_id must be a non-empty string", 0
        server_id = value.strip()
        if len(server_id) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in server_id):
            return None, "Invalid server_id", 0
        if server_id not in seen:
            ordered.append(server_id)
            seen.add(server_id)
    return ordered, None, len(server_ids) - len(ordered)


def _parse_link(output: str) -> Dict[str, Dict[str, Any]]:
    interfaces: Dict[str, Dict[str, Any]] = {}
    pattern = re.compile(
        r"^\d+:\s+([^:@\s]+)(?:@[^:]+)?:\s+<([^>]*)>.*?\bstate\s+(\S+).*?" r"\blink/\S+\s+([0-9A-Fa-f:]{17})\b"
    )
    for raw_line in output.splitlines():
        match = pattern.search(raw_line.strip())
        if not match:
            continue
        name, flags_text, state, mac = match.groups()
        if name == "lo" or not _IFNAME_RE.fullmatch(name) or not _MAC_RE.fullmatch(mac):
            continue
        flags = {flag.strip().upper() for flag in flags_text.split(",") if flag.strip()}
        interfaces[name] = {
            "name": name,
            "mac_address": mac.lower(),
            "vendor": None,
            "model": None,
            "description": None,
            "pci_address": None,
            "physical_port": None,
            "driver": None,
            "firmware_version": None,
            "link": {
                "detected": "LOWER_UP" in flags,
                "detected_source": "ip-link-lower-up",
                "state": state.lower(),
                "speed_mbps": None,
                "duplex": None,
                "media": None,
            },
            "addresses": [],
        }
    return interfaces


def _parse_sysfs(output: str) -> Dict[str, Dict[str, Optional[str]]]:
    values: Dict[str, Dict[str, Optional[str]]] = {}
    for line in output.splitlines():
        fields = line.strip().split("\t")
        if len(fields) < 7 or fields[0] != "SYSFS" or not _IFNAME_RE.fullmatch(fields[1]):
            continue
        name, carrier, state, phys_port, dev_port, pci = fields[1:7]
        values[name] = {
            "carrier": carrier or None,
            "state": state or None,
            "physical_port": phys_port or dev_port or None,
            "pci_address": pci if _PCI_RE.fullmatch(pci) else None,
        }
    return values


def _split_vendor_model(description: str) -> tuple[Optional[str], Optional[str]]:
    value = description.strip()
    value = re.sub(r"\s+\[[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\](?:\s+\(rev [^)]+\))?$", "", value)
    vendors = (
        "Broadcom Inc. and subsidiaries",
        "Mellanox Technologies",
        "Intel Corporation",
        "NVIDIA Corporation",
        "Marvell Technology Group Ltd.",
        "QLogic Corp.",
        "Advanced Micro Devices, Inc.",
    )
    for vendor in vendors:
        if value.startswith(vendor):
            return vendor, value[len(vendor) :].strip() or None
    parts = value.split(None, 1)
    return (parts[0], parts[1] if len(parts) > 1 else None) if parts else (None, None)


def _parse_pci(output: str) -> Dict[str, Dict[str, Optional[str]]]:
    devices: Dict[str, Dict[str, Optional[str]]] = {}
    pattern = re.compile(
        r"^([0-9A-Fa-f:.]+)\s+(?:Ethernet|Network) controller(?:\s+\[[^]]+\])?:\s+(.+)$",
        re.IGNORECASE,
    )
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        pci, description = match.groups()
        if not _PCI_RE.fullmatch(pci):
            continue
        vendor, model = _split_vendor_model(description)
        devices[pci.lower()] = {
            "vendor": vendor,
            "model": model,
            "description": description.strip(),
        }
    return devices


def _parse_addresses(output: str, interfaces: Dict[str, Dict[str, Any]]) -> None:
    for line in output.splitlines():
        match = re.match(r"^\d+:\s+(\S+)\s+(inet6?)\s+(\S+).*?\bscope\s+(\S+)", line.strip())
        if not match:
            continue
        name, family, address, scope = match.groups()
        name = name.split("@", 1)[0]
        if name not in interfaces:
            continue
        try:
            address = str(ipaddress.ip_interface(address))
        except ValueError:
            continue
        interfaces[name]["addresses"].append(
            {
                "address": address,
                "family": "ipv6" if family == "inet6" else "ipv4",
                "scope": scope,
            }
        )


def _parse_speed(value: str) -> Optional[int]:
    match = re.search(r"(\d+)\s*([MGT])b/s", value, re.IGNORECASE)
    if not match:
        return None
    multiplier = {"M": 1, "G": 1000, "T": 1_000_000}[match.group(2).upper()]
    return int(match.group(1)) * multiplier


def _apply_details(interface: Dict[str, Any], output: str) -> None:
    fields: Dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(
            r"\s*(driver|firmware-version|bus-info|Speed|Duplex|Port|Link detected|PHYS_PORT|DEV_PORT):\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()
    interface["driver"] = fields.get("driver") or interface.get("driver")
    interface["firmware_version"] = fields.get("firmware-version") or None
    bus = fields.get("bus-info", "").lower()
    if _PCI_RE.fullmatch(bus):
        interface["pci_address"] = bus
    interface["physical_port"] = (
        fields.get("phys_port") or interface.get("physical_port") or fields.get("dev_port") or None
    )
    interface["link"]["speed_mbps"] = _parse_speed(fields.get("speed", ""))
    interface["link"]["duplex"] = fields.get("duplex", "").lower() or None
    interface["link"]["media"] = fields.get("port", "").lower() or None
    if fields.get("link detected", "").lower() in {"yes", "no"}:
        interface["link"]["detected"] = fields["link detected"].lower() == "yes"
        interface["link"]["detected_source"] = "ethtool"


def _parse_system(output: str) -> Dict[str, Optional[str]]:
    result: Dict[str, Optional[str]] = {"hostname": None, "serial_number": None, "kernel": None}
    for line in output.splitlines():
        for prefix, key in (("HOSTNAME\t", "hostname"), ("SERIAL\t", "serial_number"), ("KERNEL\t", "kernel")):
            if line.startswith(prefix):
                value = line[len(prefix) :].strip()
                result[key] = value or None
    return result


def _parse_lldp(output: str) -> Dict[str, Dict[str, str]]:
    neighbors: Dict[str, Dict[str, str]] = {}
    for line in output.splitlines():
        match = re.match(
            r"lldp\.([^.]+)\.([^.]+)\.(chassis\.name|port\.id|port\.descr)=(.*)$",
            line.strip(),
        )
        if not match:
            continue
        interface, _index, field, value = match.groups()
        if _IFNAME_RE.fullmatch(interface):
            neighbors.setdefault(interface, {})[field.replace(".", "_")] = value
    return neighbors


def _command_map(result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(command.get("label")): command
        for command in result.get("commands", [])
        if isinstance(command, dict) and command.get("label")
    }


def _required_success(commands: Dict[str, Dict[str, Any]], labels: List[str]) -> bool:
    return all(_probe_success(commands.get(label, {})) for label in labels)


def _probe_success(probe: Dict[str, Any]) -> bool:
    """Require an unambiguous, complete zero-exit result before trusting output."""
    exit_code = probe.get("exit_code")
    return (
        probe.get("status") == "success"
        and probe.get("result_confirmed") is True
        and isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code == 0
        and probe.get("truncated") is False
    )


def _session_input_uncertain(result: Dict[str, Any]) -> bool:
    """Return whether more console input would be unsafe after this session."""
    if result.get("sent_unconfirmed") is True or result.get("input_state") == "partial_text_possible":
        return True
    return any(
        isinstance(item, dict)
        and (
            (item.get("command_sent") is True and item.get("result_confirmed") is not True)
            or item.get("input_state") == "partial_text_possible"
        )
        for item in result.get("commands", [])
    )


def _detail_command(interface: str) -> str:
    quoted = shlex.quote(interface)
    return (
        f"if command -v ethtool >/dev/null 2>&1; then ethtool -i {quoted} 2>/dev/null; "
        f"ethtool {quoted} 2>/dev/null | grep -E 'Speed:|Duplex:|Port:|Link detected:' || true; "
        "else printf 'ETHTOOL_UNAVAILABLE\\n'; fi; "
        f"printf 'PHYS_PORT: '; cat /sys/class/net/{quoted}/phys_port_name 2>/dev/null || true; "
        f"printf 'DEV_PORT: '; cat /sys/class/net/{quoted}/dev_port 2>/dev/null || true"
    )


async def _redfish_system_identity(server_id: str) -> Dict[str, Any]:
    try:
        handler = await _get_handler(server_id)
        response = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
        if response.get("status") != "success" or not isinstance(response.get("data"), dict):
            return {"status": "unavailable", "message": response.get("message")}
        data = response["data"]
        return {
            "status": "success",
            "serial_number": data.get("SerialNumber"),
            "uuid": data.get("UUID"),
            "manufacturer": data.get("Manufacturer"),
            "model": data.get("Model"),
        }
    except Exception as exc:
        return {"status": "unavailable", "message": f"{type(exc).__name__}: {exc}"[-1000:]}


def _configured_expected_macs(server: Dict[str, Any]) -> set[str]:
    values: List[Any] = []
    for key in ("expected_host_macs", "host_macs"):
        if isinstance(server.get(key), list):
            values.extend(server[key])
    for key in ("expected_host_mac", "host_mac", "mac"):
        if server.get(key):
            values.append(server[key])
    normalized: set[str] = set()
    for value in values:
        match = _MAC_RE.fullmatch(str(value).strip())
        if not match:
            raise ValueError(f"Invalid configured host MAC: {value}")
        normalized.add(match.group(0).lower())
    return normalized


def _identity_result(
    server: Dict[str, Any],
    system: Dict[str, Optional[str]],
    redfish: Dict[str, Any],
    interfaces: List[Dict[str, Any]],
) -> Dict[str, Any]:
    observed_macs = {item["mac_address"] for item in interfaces if item.get("mac_address")}
    expected_macs = _configured_expected_macs(server)
    missing_macs = sorted(expected_macs - observed_macs)

    def clean_serial(value: Any) -> str:
        serial = str(value or "").strip()
        if serial.casefold() in {"", "none", "not specified", "unknown", "to be filled by o.e.m."}:
            return ""
        return serial

    os_serial = clean_serial(system.get("serial_number"))
    redfish_serial = clean_serial(redfish.get("serial_number"))
    configured_serial = clean_serial(server.get("serial_number") or server.get("service_tag"))

    def compare(left: str, right: str) -> Optional[bool]:
        return left.casefold() == right.casefold() if left and right else None

    serial_comparisons = {
        "configured_to_os": compare(configured_serial, os_serial),
        "configured_to_redfish": compare(configured_serial, redfish_serial),
        "os_to_redfish": compare(os_serial, redfish_serial),
    }

    reasons: List[str] = []
    if missing_macs:
        reasons.append("configured host MACs were not observed")
    comparison_messages = {
        "configured_to_os": "configured serial does not match the OS DMI serial",
        "configured_to_redfish": "configured serial does not match the Redfish system serial",
        "os_to_redfish": "OS DMI serial does not match the Redfish system serial",
    }
    reasons.extend(comparison_messages[name] for name, matches in serial_comparisons.items() if matches is False)
    verification_evidence: List[str] = []
    if expected_macs and not missing_macs:
        verification_evidence.append("configured_host_macs")
    verification_evidence.extend(name for name, matches in serial_comparisons.items() if matches is True)
    comparable_serials = [matches for matches in serial_comparisons.values() if matches is not None]
    serial_matches = all(comparable_serials) if comparable_serials else None
    if reasons:
        status = "mismatch"
    elif verification_evidence:
        status = "verified"
    else:
        status = "unverified"
    return {
        "status": status,
        "reasons": reasons,
        "expected_host_macs": sorted(expected_macs),
        "missing_host_macs": missing_macs,
        "observed_host_macs": sorted(observed_macs),
        "os_serial_number": os_serial or None,
        "redfish_serial_number": redfish_serial or None,
        "configured_serial_number": configured_serial or None,
        "expected_serial_number": configured_serial or redfish_serial or None,
        "serial_matches": serial_matches,
        "serial_comparisons": serial_comparisons,
        "verification_evidence": verification_evidence,
    }


def _serial_failure(server_id: str, result: Dict[str, Any], vnc_available: bool) -> Dict[str, Any]:
    return {
        "server_id": server_id,
        "status": "error",
        "phase": result.get("phase", "serial-console"),
        "transport": result.get("transport"),
        "command_sent": result.get("command_sent", False),
        "sent_unconfirmed": result.get("sent_unconfirmed", False),
        "retry_safe": result.get("retry_safe", True),
        "input_state": result.get("input_state"),
        "message": result.get("message", "Serial-console network probes failed"),
        "vnc_fallback_required": vnc_available,
        "vnc_fallback_note": (
            "Use capture_console_screen and visually guarded VNC tools; structured OCR is not fabricated"
            if vnc_available
            else None
        ),
        "probe_results": result.get("commands", []),
    }


async def _collect_one(
    server_id: str,
    *,
    transport: str,
    save: bool,
    timeout_seconds: Optional[float],
) -> Dict[str, Any]:
    server = cfg.CONFIG.get(server_id)
    if not isinstance(server, dict):
        return {
            "server_id": server_id,
            "status": "error",
            "phase": "config",
            "message": "Host is not defined in server configuration",
            "command_sent": False,
            "retry_safe": True,
        }
    vnc_available = bool(server.get("vnc_port"))
    if transport == "vnc":
        return {
            "server_id": server_id,
            "status": "error",
            "phase": "transport",
            "message": "VNC frames cannot be treated as authoritative structured text",
            "command_sent": False,
            "retry_safe": True,
            "vnc_fallback_required": vnc_available,
        }

    base_result = await _run_serial_commands(server_id, _BASE_PROBES, timeout_seconds)
    base = _command_map(base_result)
    if _session_input_uncertain(base_result):
        return {
            **_serial_failure(server_id, base_result, vnc_available),
            "phase": "base-probes-unknown",
            "message": (
                "A base probe may have been partially sent or lacks a confirmed result; "
                "no second console session was started"
            ),
        }
    if not _required_success(base, ["link", "sysfs"]):
        return _serial_failure(server_id, base_result, vnc_available)

    interfaces = _parse_link(base["link"].get("output", ""))
    sysfs = _parse_sysfs(base["sysfs"].get("output", ""))
    interfaces = {name: value for name, value in interfaces.items() if name in sysfs}
    for name, values in sysfs.items():
        if name not in interfaces:
            continue
        interface = interfaces[name]
        interface["pci_address"] = values.get("pci_address")
        interface["physical_port"] = values.get("physical_port")
        if values.get("state"):
            interface["link"]["state"] = values["state"].lower()
        if values.get("carrier") in {"0", "1"}:
            interface["link"]["detected"] = values["carrier"] == "1"
            interface["link"]["detected_source"] = "sysfs-carrier"
    if not interfaces:
        return {
            **_serial_failure(server_id, base_result, vnc_available),
            "phase": "parse-link",
            "message": "No physical network interfaces were parsed from ip-link and sysfs",
        }
    if len(interfaces) > _MAX_INTERFACES:
        return {
            **_serial_failure(server_id, base_result, vnc_available),
            "phase": "parse-link",
            "message": f"Refusing to probe more than {_MAX_INTERFACES} physical interfaces",
        }

    pci_probe = base.get("pci", {})
    pci = _parse_pci(pci_probe.get("output", "")) if _probe_success(pci_probe) else {}
    for interface in interfaces.values():
        description = pci.get(str(interface.get("pci_address") or "").lower())
        if description:
            interface.update(description)

    details: List[Tuple[str, str]] = [
        (f"detail_{index}", _detail_command(name)) for index, name in enumerate(sorted(interfaces))
    ]
    details.extend(
        [
            ("address_final", "ip -o address show"),
            ("route_final", "ip -o route show table all"),
        ]
    )
    detail_result = await _run_serial_commands(server_id, details, timeout_seconds)
    detail_map = _command_map(detail_result)
    if _session_input_uncertain(detail_result):
        return _serial_failure(server_id, detail_result, vnc_available)
    if not _required_success(detail_map, ["address_final", "route_final"]):
        return _serial_failure(server_id, detail_result, vnc_available)

    for index, name in enumerate(sorted(interfaces)):
        detail = detail_map.get(f"detail_{index}")
        if detail and _probe_success(detail):
            _apply_details(interfaces[name], detail.get("output", ""))
    _parse_addresses(detail_map["address_final"].get("output", ""), interfaces)
    routes = [
        {"raw": line.strip()} for line in detail_map["route_final"].get("output", "").splitlines() if line.strip()
    ]
    system_probe = base.get("system", {})
    system = _parse_system(system_probe.get("output", "")) if _probe_success(system_probe) else _parse_system("")
    lldp_probe = base.get("lldp", {})
    lldp = _parse_lldp(lldp_probe.get("output", "")) if _probe_success(lldp_probe) else {}
    for name, neighbor in lldp.items():
        if name in interfaces:
            interfaces[name]["neighbor"] = neighbor

    ordered_interfaces = [interfaces[name] for name in sorted(interfaces)]
    redfish_identity = await _redfish_system_identity(server_id)
    try:
        identity = _identity_result(server, system, redfish_identity, ordered_interfaces)
    except ValueError as exc:
        return {
            "server_id": server_id,
            "status": "error",
            "phase": "identity",
            "message": str(exc),
            "command_sent": True,
            "result_confirmed": True,
            "retry_safe": False,
        }

    optional_failures = [
        item.get("label")
        for item in [*base_result.get("commands", []), *detail_result.get("commands", [])]
        if item.get("label") not in {"link", "sysfs", "address_final", "route_final"} and not _probe_success(item)
    ]
    collection_status = "success" if not optional_failures else "partial"
    host_metadata = {
        "bmc_address": server.get("bmc_ip"),
        "bmc_hostname": server.get("bmc_hostname"),
        "bmc_mac_address": server.get("bmc_mac") or server.get("bmc_mac_address"),
        "vendor": cfg.normalize_vendor(server.get("vendor")) or None,
        "model": redfish_identity.get("model"),
        "serial_number": redfish_identity.get("serial_number"),
        "os_hostname": system.get("hostname"),
        "identity_status": identity["status"],
    }
    host_metadata = {key: value for key, value in host_metadata.items() if value is not None}
    source = {
        "collection_method": base_result.get("transport"),
        "collection_status": collection_status,
        "commands": "read-only Linux ip, sysfs, ethtool, PCI, DMI, and optional LLDP queries",
        "identity": identity,
        "redfish_identity": redfish_identity,
        "optional_probe_failures": optional_failures,
    }

    save_result: Optional[Dict[str, Any]] = None
    if save and identity["status"] == "verified":
        save_result = await save_network_inventory(
            server_id,
            ordered_interfaces,
            host=host_metadata,
            routes=routes,
            source=source,
            observed_at=_now(),
        )
    elif save:
        save_result = {
            "status": "error",
            "server_id": server_id,
            "message": f"identity {identity['status']}: collected data was not persisted",
        }

    if identity["status"] == "mismatch":
        status = "error"
    elif save and identity["status"] != "verified":
        status = "error"
    elif save_result and save_result.get("status") != "success":
        status = "error"
    elif identity["status"] != "verified":
        status = "partial"
    else:
        status = collection_status
    return {
        "server_id": server_id,
        "status": status,
        "phase": "complete" if status in {"success", "partial"} else "identity",
        "transport": base_result.get("transport"),
        "command_sent": True,
        "result_confirmed": True,
        "retry_safe": False,
        "identity": identity,
        "host": host_metadata,
        "interfaces": ordered_interfaces,
        "routes": routes,
        "source": source,
        "saved": save_result,
        "summary": {
            "interface_count": len(ordered_interfaces),
            "links_up": sum(item["link"]["detected"] for item in ordered_interfaces),
            "address_count": sum(len(item["addresses"]) for item in ordered_interfaces),
        },
    }


@mcp.tool(
    description=(
        "Collect, parse, identity-check, and optionally persist Linux network data for multiple "
        "hosts through Dell SOL or HPE VSP using short read-only probes. VNC fallback is reported "
        "for visual handling and is never silently OCR-derived."
    )
)
async def collect_network_inventory(
    server_ids: List[str],
    transport: str = "auto",
    save: bool = True,
    concurrency: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    cfg._load_config()
    normalized, error, duplicates_removed = _normalize_ids(server_ids)
    transport = str(transport).strip().lower()
    if transport not in {"auto", "serial", "sol", "vnc"}:
        error = "transport must be auto, serial, sol, or vnc"
    if not isinstance(save, bool):
        error = "save must be a boolean"
    if concurrency is None:
        concurrency = getattr(cfg, "NETWORK_COLLECTION_CONCURRENCY", 6)
    if isinstance(concurrency, bool):
        error = "concurrency must be an integer"
    try:
        concurrency = int(concurrency)
    except (TypeError, ValueError):
        error = "concurrency must be an integer"
        concurrency = 0
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        error = f"concurrency must be between 1 and {_MAX_CONCURRENCY}"
    if timeout_seconds is not None:
        try:
            timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError):
            error = "timeout_seconds must be numeric"
        else:
            if not 1 <= timeout_seconds <= 300:
                error = "timeout_seconds must be between 1 and 300"
    if error:
        return {
            "status": "error",
            "phase": "validation",
            "message": error,
            "requested": len(server_ids) if isinstance(server_ids, list) else 0,
            "results": [],
        }

    semaphore = asyncio.Semaphore(concurrency)

    async def run(server_id: str) -> Dict[str, Any]:
        async with semaphore:
            return await _collect_one(
                server_id,
                transport=transport,
                save=save,
                timeout_seconds=timeout_seconds,
            )

    results = await asyncio.gather(*(run(server_id) for server_id in normalized))
    collected = sum(result.get("status") in {"success", "partial"} for result in results)
    saved_count = sum((result.get("saved") or {}).get("status") == "success" for result in results)
    failed = [result["server_id"] for result in results if result.get("status") == "error"]
    partial = [result["server_id"] for result in results if result.get("status") == "partial"]
    status = "success" if not (failed or partial) else ("partial" if collected else "error")
    return {
        "status": status,
        "phase": "complete",
        "transport": transport,
        "requested": len(server_ids),
        "unique": len(normalized),
        "duplicates_removed": duplicates_removed,
        "collected": collected,
        "saved": saved_count,
        "failed": failed,
        "partial": partial,
        "results": results,
    }
