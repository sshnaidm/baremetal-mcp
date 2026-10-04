"""Vendor-neutral, read-only Redfish NIC, adapter, port, and function inventory."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import config as cfg
from config import mcp
from helpers import _get_handler, _redfish_call

_MAX_BATCH_SIZE = 64
_MAX_CONCURRENCY = 12
_MAX_MEMBERS = 256
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


def _odata_path(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("/"):
        return value
    if isinstance(value, dict):
        path = value.get("@odata.id")
        if isinstance(path, str) and path.startswith("/"):
            return path
    return None


def _normalize_mac(value: object) -> str | None:
    text = str(value or "").strip()
    return text.lower() if _MAC_RE.fullmatch(text) else None


def _macs(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return sorted({mac for item in values if (mac := _normalize_mac(item))})


class _RedfishReader:
    def __init__(self, server_id: str, concurrency: int = 8) -> None:
        self.server_id = server_id
        self.semaphore = asyncio.Semaphore(concurrency)
        self.errors: list[dict[str, str]] = []

    async def get(self, path: str, *, optional: bool = False) -> dict[str, Any] | None:
        async with self.semaphore:
            result = await _redfish_call(self.server_id, "GET", path)
        if result.get("status") == "success" and isinstance(result.get("data"), dict):
            return result["data"]
        if not optional:
            self.errors.append({"path": path, "message": result.get("message", "Redfish GET failed")})
        return None

    async def collection(self, path: str, *, optional: bool = True) -> list[dict[str, Any]]:
        collection = await self.get(path, optional=optional)
        if not collection:
            return []
        members = collection.get("Members") or []
        if not isinstance(members, list):
            if not optional:
                self.errors.append({"path": path, "message": "Collection Members is not a list"})
            return []
        paths = [member_path for member in members if (member_path := _odata_path(member))]
        if len(paths) > _MAX_MEMBERS:
            self.errors.append({"path": path, "message": f"Collection exceeds {_MAX_MEMBERS} member safety limit"})
            paths = paths[:_MAX_MEMBERS]
        values = await asyncio.gather(*(self.get(member_path, optional=True) for member_path in paths))
        return [value for value in values if value]


def _ethernet_interface(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data.get("Id"),
        "name": data.get("Name"),
        "description": data.get("Description"),
        "mac_address": _normalize_mac(data.get("MACAddress")),
        "permanent_mac_address": _normalize_mac(data.get("PermanentMACAddress")),
        "interface_enabled": data.get("InterfaceEnabled"),
        "link_status": data.get("LinkStatus"),
        "speed_mbps": data.get("CurrentLinkSpeedMbps") or data.get("SpeedMbps"),
        "mtu": data.get("MTUSize"),
        "fqdn": data.get("FQDN"),
        "ipv4": data.get("IPv4Addresses") or [],
        "ipv6": data.get("IPv6Addresses") or [],
        "status": data.get("Status"),
        "source_path": data.get("@odata.id"),
    }


def _adapter(data: dict[str, Any]) -> dict[str, Any]:
    controllers = []
    for controller in data.get("Controllers") or []:
        if not isinstance(controller, dict):
            continue
        capabilities = controller.get("ControllerCapabilities") or {}
        location = controller.get("Location") or {}
        part_location = location.get("PartLocation") or {}
        controllers.append(
            {
                "location": part_location.get("ServiceLabel"),
                "network_port_count": capabilities.get("NetworkPortCount"),
                "network_device_function_count": capabilities.get("NetworkDeviceFunctionCount"),
                "firmware_package_version": controller.get("FirmwarePackageVersion"),
                "pcie_interface": controller.get("PCIeInterface"),
            }
        )
    return {
        "id": data.get("Id"),
        "name": data.get("Name"),
        "manufacturer": data.get("Manufacturer"),
        "model": data.get("Model"),
        "serial_number": data.get("SerialNumber"),
        "part_number": data.get("PartNumber"),
        "controllers": controllers,
        "status": data.get("Status"),
        "source_path": data.get("@odata.id"),
    }


def _port(data: dict[str, Any]) -> dict[str, Any]:
    ethernet = data.get("Ethernet") or {}
    addresses = (
        data.get("AssociatedNetworkAddresses")
        or ethernet.get("AssociatedMACAddresses")
        or data.get("AssociatedMACAddresses")
        or []
    )
    return {
        "id": data.get("Id"),
        "name": data.get("Name"),
        "physical_port_number": data.get("PhysicalPortNumber"),
        "link_status": data.get("LinkStatus"),
        "speed_mbps": data.get("CurrentLinkSpeedMbps"),
        "active_link_technology": data.get("ActiveLinkTechnology"),
        "supported_link_capabilities": data.get("SupportedLinkCapabilities") or [],
        "mac_addresses": _macs(addresses),
        "status": data.get("Status"),
        "source_path": data.get("@odata.id"),
    }


def _device_function(data: dict[str, Any]) -> dict[str, Any]:
    ethernet = data.get("Ethernet") or {}
    links = data.get("Links") or {}
    assignment = links.get("PhysicalPortAssignment")
    return {
        "id": data.get("Id"),
        "name": data.get("Name"),
        "device_enabled": data.get("DeviceEnabled"),
        "net_device_function_type": data.get("NetDevFuncType"),
        "mac_address": _normalize_mac(ethernet.get("MACAddress") or data.get("MACAddress")),
        "permanent_mac_address": _normalize_mac(ethernet.get("PermanentMACAddress") or data.get("PermanentMACAddress")),
        "boot_mode": ethernet.get("BootMode"),
        "physical_port_path": _odata_path(assignment),
        "status": data.get("Status"),
        "source_path": data.get("@odata.id"),
    }


async def _linked_collection(
    reader: _RedfishReader,
    owner: dict[str, Any],
    property_name: str,
    fallback_suffix: str,
) -> list[dict[str, Any]]:
    path = _odata_path(owner.get(property_name))
    if not path:
        links = owner.get("Links") or {}
        path = _odata_path(links.get(property_name))
    if not path:
        owner_path = owner.get("@odata.id")
        if isinstance(owner_path, str):
            path = f"{owner_path.rstrip('/')}/{fallback_suffix}"
    return await reader.collection(path, optional=True) if path else []


async def _collect_one(server_id: str) -> dict[str, Any]:
    server = cfg.CONFIG.get(server_id)
    if not isinstance(server, dict):
        return {"server_id": server_id, "status": "error", "message": "Unknown server"}
    reader = _RedfishReader(server_id)
    try:
        handler = await _get_handler(server_id)
        system = await reader.get(handler.SYSTEM_PATH, optional=False)
        if not system:
            return {
                "server_id": server_id,
                "status": "error",
                "message": "Could not read Redfish system identity",
                "errors": reader.errors,
            }
        observed_serial = str(system.get("SerialNumber") or "").strip()
        expected_serial = str(server.get("serial_number") or server.get("service_tag") or "").strip()
        identity = {
            "status": "observed",
            "expected_serial_number": expected_serial or None,
            "observed_serial_number": observed_serial or None,
            "model": system.get("Model"),
            "manufacturer": system.get("Manufacturer"),
            "uuid": system.get("UUID"),
        }
        if expected_serial and observed_serial:
            identity["status"] = "verified" if expected_serial.casefold() == observed_serial.casefold() else "mismatch"
        if identity["status"] == "mismatch":
            return {
                "server_id": server_id,
                "status": "error",
                "identity": identity,
                "message": "Configured host identity does not match the target BMC",
                "errors": reader.errors,
            }

        ethernet_data = await reader.collection(f"{handler.SYSTEM_PATH}/EthernetInterfaces")
        network_interfaces = await reader.collection(f"{handler.SYSTEM_PATH}/NetworkInterfaces")
        chassis = await reader.collection("/redfish/v1/Chassis")
        adapters: list[dict[str, Any]] = []
        ports_by_path: dict[str, dict[str, Any]] = {}
        functions_by_path: dict[str, dict[str, Any]] = {}

        adapter_groups = await asyncio.gather(
            *(
                reader.collection(f"{item.get('@odata.id')}/NetworkAdapters")
                for item in chassis
                if item.get("@odata.id")
            )
        )
        adapter_data = [adapter for group in adapter_groups for adapter in group]
        owners = [*adapter_data, *network_interfaces]
        child_groups = (
            await asyncio.gather(
                *(_linked_collection(reader, owner, "NetworkPorts", "NetworkPorts") for owner in owners),
                *(
                    _linked_collection(reader, owner, "NetworkDeviceFunctions", "NetworkDeviceFunctions")
                    for owner in owners
                ),
            )
            if owners
            else []
        )
        split = len(owners)
        port_groups = child_groups[:split]
        function_groups = child_groups[split:]
        for data in adapter_data:
            adapters.append(_adapter(data))
        for group in port_groups:
            for data in group:
                key = str(data.get("@odata.id") or data.get("Id"))
                ports_by_path[key] = _port(data)
        for group in function_groups:
            for data in group:
                key = str(data.get("@odata.id") or data.get("Id"))
                functions_by_path[key] = _device_function(data)

        status = "partial" if reader.errors else "success"
        return {
            "server_id": server_id,
            "status": status,
            "vendor": cfg.normalize_vendor(server.get("vendor")) or None,
            "bmc_address": server.get("bmc_ip"),
            "identity": identity,
            "source": "redfish",
            "limitations": (
                "Redfish names and addresses describe firmware-visible interfaces and may not match Linux names"
            ),
            "ethernet_interfaces": [_ethernet_interface(data) for data in ethernet_data],
            "network_adapters": adapters,
            "network_ports": list(ports_by_path.values()),
            "network_device_functions": list(functions_by_path.values()),
            "errors": reader.errors,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "status": "error",
            "message": f"{type(exc).__name__}: {exc}"[-1000:],
            "errors": reader.errors,
        }


@mcp.tool(
    description=(
        "Read normalized NICs, physical network adapters, ports, and device functions through "
        "standard Redfish paths. Results retain source paths and do not change host or BMC state."
    )
)
async def get_network_hardware(
    server_ids: list[str],
    concurrency: int | None = None,
) -> dict[str, Any]:
    cfg._load_config()
    if not isinstance(server_ids, list) or not server_ids:
        return {"status": "error", "message": "server_ids must be a non-empty list"}
    if len(server_ids) > _MAX_BATCH_SIZE:
        return {"status": "error", "message": f"At most {_MAX_BATCH_SIZE} hosts may be requested"}
    ordered: list[str] = []
    for value in server_ids:
        if not isinstance(value, str) or not value.strip():
            return {"status": "error", "message": "Every server_id must be non-empty"}
        if value.strip() not in ordered:
            ordered.append(value.strip())
    if concurrency is None:
        concurrency = getattr(cfg, "BATCH_CONCURRENCY", 6)
    try:
        concurrency = int(concurrency) if not isinstance(concurrency, bool) else 0
    except (TypeError, ValueError):
        concurrency = 0
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        return {"status": "error", "message": f"concurrency must be between 1 and {_MAX_CONCURRENCY}"}

    semaphore = asyncio.Semaphore(concurrency)

    async def run(server_id: str) -> dict[str, Any]:
        async with semaphore:
            return await _collect_one(server_id)

    results = await asyncio.gather(*(run(server_id) for server_id in ordered))
    successful = sum(result.get("status") == "success" for result in results)
    failed = sum(result.get("status") == "error" for result in results)
    status = (
        "success" if successful == len(results) else ("partial" if successful or failed < len(results) else "error")
    )
    return {
        "status": status,
        "requested": len(server_ids),
        "unique": len(ordered),
        "duplicates_removed": len(server_ids) - len(ordered),
        "successful": successful,
        "failed": failed,
        "results": results,
    }
