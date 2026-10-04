#!/usr/bin/env python3
"""Read-only, detailed HPE iLO Redfish hardware snapshot export."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import config as cfg
from config import mcp, _load_config
from handlers import HPE
from helpers import _get_handler, _redfish_call
from tools.dell import _atomic_write, _collection_directory, _utc_now

_MAX_HOSTS = 64
_MAX_MEMBERS = 256
_MAX_PAGES = 8
_COLLECTION_LOCKS: Dict[str, asyncio.Lock] = {}


def _path(value: Any) -> Optional[str]:
    """Accept only origin-relative Redfish resource paths from a BMC response."""
    if isinstance(value, dict):
        value = value.get("@odata.id")
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/redfish/v1/"):
        return None
    if ".." in parsed.path.split("/") or any(ord(char) < 32 for char in value):
        return None
    return value


def _link(owner: Dict[str, Any], key: str) -> Optional[str]:
    return _path(owner.get(key))


def _members_path(owner: Dict[str, Any], key: str) -> Optional[str]:
    return _link(owner, key) or _path((owner.get("Links") or {}).get(key))


def _is_expanded(member: Any) -> bool:
    return isinstance(member, dict) and bool(
        set(member) - {"@odata.id", "@odata.context", "@odata.type", "@odata.etag"}
    )


class _Reader:
    def __init__(self, server_id: str, concurrency: int = 4):
        self.server_id = server_id
        self.semaphore = asyncio.Semaphore(concurrency)
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []
        self.requests = 0
        self.expanded_collections = 0
        self.member_fallbacks = 0

    def issue(self, path: str, message: str, *, required: bool) -> None:
        (self.errors if required else self.warnings).append({"path": path, "message": message})

    async def get(
        self, path: Optional[str], *, required: bool = False, report: bool = True
    ) -> Optional[Dict[str, Any]]:
        safe_path = _path(path)
        if not safe_path:
            if report:
                self.issue(str(path), "Missing or unsafe Redfish path", required=required)
            return None
        if safe_path in self.cache:
            return self.cache[safe_path]
        async with self.semaphore:
            result = await _redfish_call(self.server_id, "GET", safe_path)
            self.requests += 1
        if result.get("status") != "success" or not isinstance(result.get("data"), dict):
            if report:
                self.issue(safe_path, str(result.get("message") or "Redfish GET failed"), required=required)
            return None
        data = result["data"]
        self.cache[safe_path] = data
        return data

    async def links(self, values: Any, *, required: bool = False) -> List[Dict[str, Any]]:
        if not isinstance(values, list):
            return []
        if len(values) > _MAX_MEMBERS:
            self.issue("Members", f"More than {_MAX_MEMBERS} linked resources", required=True)
            return []

        async def resolve(value: Any) -> Optional[Dict[str, Any]]:
            if _is_expanded(value):
                return value
            path = _path(value)
            if not path:
                self.issue(str(value), "Missing or unsafe member path", required=required)
                return None
            self.member_fallbacks += 1
            return await self.get(path, required=required)

        resolved = await asyncio.gather(*(resolve(value) for value in values))
        return [value for value in resolved if value is not None]

    async def collection(self, path: Optional[str], *, required: bool = False) -> List[Dict[str, Any]]:
        safe_path = _path(path)
        if not safe_path:
            self.issue(str(path), "Collection path not advertised", required=required)
            return []
        expanded_path = safe_path + ("&" if "?" in safe_path else "?") + "$expand=."
        page = await self.get(expanded_path, report=False)
        if page is None:
            page = await self.get(safe_path, required=required)
            if page is None:
                return []
            self.warnings.append({"path": safe_path, "message": "$expand=. unavailable; fetched members individually"})
        else:
            self.expanded_collections += 1

        members: List[Dict[str, Any]] = []
        seen = set()
        pages = 0
        while page is not None:
            pages += 1
            if pages > _MAX_PAGES:
                self.issue(safe_path, f"More than {_MAX_PAGES} collection pages", required=True)
                break
            values = page.get("Members")
            if not isinstance(values, list):
                self.issue(safe_path, "Collection Members is not a list", required=required)
                break
            if len(members) + len(values) > _MAX_MEMBERS:
                self.issue(safe_path, f"More than {_MAX_MEMBERS} collection members", required=True)
                break
            for value in await self.links(values, required=required):
                member_path = _path(value)
                if member_path and member_path in seen:
                    continue
                if member_path:
                    seen.add(member_path)
                    self.cache.setdefault(member_path, value)
                members.append(value)
            next_path = _path(page.get("Members@odata.nextLink") or page.get("@odata.nextLink"))
            page = await self.get(next_path, required=required) if next_path else None
        # The first page declares total count; inspect the cached first response.
        first_page = self.cache.get(expanded_path) or self.cache.get(safe_path) or {}
        declared = first_page.get("Members@odata.count")
        if isinstance(declared, int) and len(members) != declared:
            self.issue(safe_path, f"Expected {declared} members, received {len(members)}", required=required)
        return members


def _memory_summary(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    slots = []
    for memory in members:
        state = (memory.get("Status") or {}).get("State")
        capacity = memory.get("CapacityMiB")
        occupied = False if state == "Absent" else True if isinstance(capacity, int) and capacity > 0 else None
        slots.append(
            {
                "id": memory.get("Id"),
                "locator": memory.get("DeviceLocator"),
                "occupied": occupied,
                "capacity_mib": capacity,
                "state": state,
                "health": (memory.get("Status") or {}).get("Health"),
                "manufacturer": memory.get("Manufacturer"),
                "part_number": memory.get("PartNumber"),
                "serial_number": memory.get("SerialNumber"),
                "source_path": _path(memory),
            }
        )
    return slots


def _pci_summary(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    slots = []
    for slot in members:
        operational = (slot.get("Status") or {}).get("OperationalStatus") or []
        state = operational[0].get("Status") if operational and isinstance(operational[0], dict) else None
        slots.append(
            {
                "id": slot.get("Id"),
                "name": slot.get("Name"),
                "occupancy": "empty" if state == "Empty" else "in_use" if state == "InUse" else "unknown",
                "operational_status": state,
                "link_lanes": slot.get("LinkLanes"),
                "technology": slot.get("Technology"),
                "source_path": _path(slot),
            }
        )
    return slots


def _network_summary(adapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ports = []
    for item in adapters:
        adapter = item["adapter"]
        for port in item["ports"]:
            ports.append(
                {
                    "adapter_id": adapter.get("Id"),
                    "adapter_model": adapter.get("Model"),
                    "id": port.get("Id"),
                    "physical_port_number": port.get("PhysicalPortNumber"),
                    "link_status": port.get("LinkStatus"),
                    "signal_detected": port.get("SignalDetected"),
                    "speed_mbps": port.get("CurrentLinkSpeedMbps"),
                    "addresses": port.get("AssociatedNetworkAddresses") or [],
                    "state": (port.get("Status") or {}).get("State"),
                    "source_path": _path(port),
                }
            )
    return ports


def _base_network_summary(adapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ports = []
    for adapter in adapters:
        for index, port in enumerate(adapter.get("PhysicalPorts") or []):
            ports.append(
                {
                    "adapter_id": adapter.get("Id"),
                    "adapter_model": adapter.get("Name"),
                    "id": port.get("Id"),
                    "physical_port_number": port.get("PhysicalPortNumber"),
                    "array_index": index,
                    "link_status": port.get("LinkStatus"),
                    "signal_detected": port.get("SignalDetected"),
                    "speed_mbps": port.get("SpeedMbps"),
                    "addresses": [port["MacAddress"]] if port.get("MacAddress") else [],
                    "state": (port.get("Status") or {}).get("State"),
                    "source_path": f"{_path(adapter)}#/PhysicalPorts/{index}",
                }
            )
    return ports


async def _smart_storage(reader: _Reader, path: str) -> Optional[Dict[str, Any]]:
    """Read the advertised HPE controller tree when standard Storage is empty."""
    root = await reader.get(path, required=True)
    if root is None:
        return None
    controller_paths = [
        _members_path(root, key) for key in ("ArrayControllers", "HostBusAdapters") if _members_path(root, key)
    ]
    if not controller_paths:
        reader.issue(path, "SmartStorage has no controller collection links", required=True)
        return None
    groups = await asyncio.gather(*(reader.collection(link, required=True) for link in controller_paths))

    async def details(controller: Dict[str, Any]) -> Dict[str, Any]:
        names = [
            key
            for key in ("PhysicalDrives", "LogicalDrives", "StorageEnclosures", "UnconfiguredDrives")
            if _members_path(controller, key)
        ]
        values = await asyncio.gather(
            *(reader.collection(_members_path(controller, key), required=True) for key in names)
        )
        return {"controller": controller, **dict(zip(names, values))}

    controllers = await asyncio.gather(*(details(controller) for group in groups for controller in group))
    return {"root": root, "controllers": controllers}


async def _collect(server_id: str, *, include_firmware: bool = False) -> Dict[str, Any]:
    reader = _Reader(server_id)
    handler = await _get_handler(server_id)
    if not isinstance(handler, HPE):
        return {"server_id": server_id, "status": "error", "unsupported": True, "message": "HPE iLO is required"}
    root, system = await asyncio.gather(
        reader.get("/redfish/v1/", required=True),
        reader.get(handler.SYSTEM_PATH, required=True),
    )
    if root is None or system is None:
        return {"server_id": server_id, "status": "error", "errors": reader.errors}
    serial = str(system.get("SerialNumber") or "").strip()
    if not serial or "HPE" not in str(system.get("Manufacturer") or "").upper():
        return {"server_id": server_id, "status": "error", "message": "HPE system identity is unavailable"}
    expected = cfg.CONFIG.get(server_id, {}).get("serial_number") or cfg.CONFIG.get(server_id, {}).get("service_tag")
    if expected and str(expected).casefold() != serial.casefold():
        return {
            "server_id": server_id,
            "status": "error",
            "message": "Configured serial does not match live HPE system",
        }
    chassis_links = (system.get("Links") or {}).get("Chassis") or []
    chassis_path = _path(chassis_links[0]) if chassis_links else None
    if not chassis_path:
        reader.issue(handler.SYSTEM_PATH, "System has no Chassis link", required=True)
        return {"server_id": server_id, "status": "error", "errors": reader.errors}
    chassis = await reader.get(chassis_path, required=True)
    if chassis is None:
        return {"server_id": server_id, "status": "error", "errors": reader.errors}
    chassis_serial = str(chassis.get("SerialNumber") or "").strip()
    if chassis_serial and chassis_serial.casefold() != serial.casefold():
        return {"server_id": server_id, "status": "error", "message": "HPE chassis and system serials disagree"}

    sys_path = _path(system) or handler.SYSTEM_PATH
    tasks = {
        "processors": reader.collection(_members_path(system, "Processors"), required=True),
        "memory": reader.collection(_members_path(system, "Memory"), required=True),
        "pci_slots": reader.collection(f"{sys_path.rstrip('/')}/PCISlots", required=True),
        "ethernet_interfaces": reader.collection(_members_path(system, "EthernetInterfaces"), required=True),
        "network_interfaces": reader.collection(_members_path(system, "NetworkInterfaces")),
        "storage": reader.collection(_members_path(system, "Storage"), required=True),
        "network_adapters": reader.collection(_members_path(chassis, "NetworkAdapters"), required=True),
        "pcie_devices": reader.collection(_members_path(chassis, "PCIeDevices"), required=True),
        "chassis_devices": reader.collection(f"{chassis_path.rstrip('/')}/Devices"),
    }
    names = list(tasks)
    values = await asyncio.gather(*tasks.values())
    sections = dict(zip(names, values))

    async def adapter_details(adapter: Dict[str, Any]) -> Dict[str, Any]:
        ports_path = _members_path(adapter, "NetworkPorts") or _members_path(adapter, "Ports")
        functions_path = _members_path(adapter, "NetworkDeviceFunctions")
        ports, functions = await asyncio.gather(
            reader.collection(ports_path, required=True),
            reader.collection(functions_path, required=bool(functions_path)),
        )
        return {"adapter": adapter, "ports": ports, "device_functions": functions}

    async def pcie_details(device: Dict[str, Any]) -> Dict[str, Any]:
        functions_path = _members_path(device, "PCIeFunctions")
        functions = await reader.collection(functions_path, required=bool(functions_path))
        return {"device": device, "functions": functions}

    async def storage_details(storage: Dict[str, Any]) -> Dict[str, Any]:
        drives, volumes = await asyncio.gather(
            reader.links(storage.get("Drives") or [], required=True),
            reader.collection(_members_path(storage, "Volumes"), required=bool(_members_path(storage, "Volumes"))),
        )
        return {"storage": storage, "drives": drives, "volumes": volumes}

    adapters, devices, storage = await asyncio.gather(
        asyncio.gather(*(adapter_details(value) for value in sections.pop("network_adapters"))),
        asyncio.gather(*(pcie_details(value) for value in sections.pop("pcie_devices"))),
        asyncio.gather(*(storage_details(value) for value in sections.pop("storage"))),
    )
    oem = (system.get("Oem") or {}).get("Hpe") or {}
    base_adapters = []
    base_path = _members_path(oem, "NetworkAdapters")
    if not adapters and base_path:
        base_adapters = await reader.collection(base_path, required=True)
    smart_storage = None
    smart_path = _members_path(oem, "SmartStorage")
    if not storage and smart_path:
        errors_before = len(reader.errors)
        smart_storage = await _smart_storage(reader, smart_path)
        if smart_storage is not None and len(reader.errors) == errors_before:
            storage_path = _members_path(system, "Storage")
            recovered = [error for error in reader.errors if error["path"].split("?")[0] == storage_path]
            for error in recovered:
                reader.errors.remove(error)
                reader.warnings.append({**error, "message": error["message"] + "; used complete HPE SmartStorage"})
    pci_slots = sections.get("pci_slots") or []
    memory = sections.get("memory") or []
    power, thermal, chassis_pcie_slots, bios = await asyncio.gather(
        reader.get(_members_path(chassis, "Power")),
        reader.get(_members_path(chassis, "Thermal")),
        reader.get(_members_path(chassis, "PCIeSlots")),
        reader.get(_members_path(system, "Bios")),
    )
    firmware = []
    if include_firmware:
        firmware = await reader.collection("/redfish/v1/UpdateService/FirmwareInventory")

    document = {
        "schema_version": 1,
        "server_id": server_id,
        "generated_at": _utc_now(),
        "identity": {
            "serial_number": serial,
            "manufacturer": system.get("Manufacturer"),
            "model": system.get("Model"),
            "uuid": system.get("UUID"),
        },
        "capabilities": {"expand_query": (root.get("ProtocolFeaturesSupported") or {}).get("ExpandQuery")},
        "request_stats": {
            "get_requests": reader.requests,
            "expanded_collections": reader.expanded_collections,
            "member_fallback_gets": reader.member_fallbacks,
        },
        "summary": {
            "memory_slots": _memory_summary(memory),
            "pci_slots": _pci_summary(pci_slots),
            "network_ports": _network_summary(adapters) + _base_network_summary(base_adapters),
        },
        "resources": {
            "system": system,
            "chassis": chassis,
            **sections,
            "network_adapters": adapters,
            "pcie_devices": devices,
            "storage": storage,
            "oem_smart_storage": smart_storage,
            "oem_base_network_adapters": base_adapters,
            "chassis_pcie_slots": chassis_pcie_slots,
            "power": power,
            "thermal": thermal,
            "bios": bios,
            "firmware_inventory": firmware if include_firmware else None,
        },
        "warnings": reader.warnings,
    }
    return {
        "server_id": server_id,
        "status": "success" if not reader.errors else "partial",
        "document": document,
        "errors": reader.errors,
    }


@mcp.tool(
    description=(
        "Export detailed HPE iLO hardware JSON for multiple servers. Uses one-level Redfish collection expansion "
        "to include empty DIMMs and PCI slots, then follows NIC, PCIe, and storage links. Writes atomic snapshots "
        "and a checksum manifest; never replaces a complete snapshot with partial data."
    )
)
async def export_hpe_hardware_inventory(
    server_ids: List[str],
    collection: Optional[str] = None,
    concurrency: Optional[int] = None,
    include_firmware: bool = False,
) -> Dict[str, Any]:
    """Persist identity-checked HPE hardware snapshots below HARDWARE_INVENTORY_DIR."""
    _load_config()
    if not isinstance(server_ids, list) or not server_ids or len(server_ids) > _MAX_HOSTS:
        return {"status": "error", "message": f"server_ids must contain 1-{_MAX_HOSTS} hosts"}
    if any(not isinstance(server_id, str) or not server_id.strip() for server_id in server_ids):
        return {"status": "error", "message": "Every server_id must be a non-empty string"}
    if not isinstance(include_firmware, bool):
        return {"status": "error", "message": "include_firmware must be a boolean"}
    if concurrency is None:
        concurrency = int(getattr(cfg, "BATCH_CONCURRENCY", 4))
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 12:
        return {"status": "error", "message": "concurrency must be an integer between 1 and 12"}
    try:
        destination = _collection_directory(collection)
        destination.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    unique_ids = list(dict.fromkeys(server_id.strip() for server_id in server_ids))
    lock = _COLLECTION_LOCKS.setdefault(str(destination), asyncio.Lock())
    async with lock:
        semaphore = asyncio.Semaphore(concurrency)

        async def run(server_id: str) -> Dict[str, Any]:
            async with semaphore:
                try:
                    result = await _collect(server_id, include_firmware=include_firmware)
                    document = result.pop("document", None)
                    if result.get("status") != "success" or document is None:
                        return result
                    payload = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
                    path = destination / f"hpe_{quote(server_id, safe='-_.')}.json"
                    _atomic_write(path, payload)
                    result.update(
                        file_path=str(path),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        bytes=len(payload),
                        identity=document["identity"],
                        request_stats=document["request_stats"],
                        summary_counts={
                            "memory_slots": len(document["summary"]["memory_slots"]),
                            "empty_memory_slots": sum(
                                item["occupied"] is False for item in document["summary"]["memory_slots"]
                            ),
                            "pci_slots": len(document["summary"]["pci_slots"]),
                            "empty_pci_slots": sum(
                                item["occupancy"] == "empty" for item in document["summary"]["pci_slots"]
                            ),
                            "network_ports": len(document["summary"]["network_ports"]),
                        },
                        warnings=document["warnings"],
                    )
                    return result
                except Exception as exc:
                    return {"server_id": server_id, "status": "error", "message": str(exc)}

        results = await asyncio.gather(*(run(server_id) for server_id in unique_ids))
        succeeded = sum(result.get("status") == "success" for result in results)
        status = "success" if succeeded == len(results) else "partial" if succeeded else "error"
        manifest = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "collection": collection,
            "directory": str(destination),
            "server_ids": unique_ids,
            "summary": {"requested": len(unique_ids), "succeeded": succeeded, "failed": len(unique_ids) - succeeded},
            "results": results,
        }
        manifest_path = destination / "manifest.json"
        try:
            _atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        except OSError as exc:
            return {"status": "error", "message": f"Could not write manifest: {exc}", "results": results}
        return {"status": status, "manifest_path": str(manifest_path), **manifest}
