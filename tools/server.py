#!/usr/bin/env python3
"""
High-level server management tools - power, firmware, system info, hardware inventory.
"""

import asyncio
from typing import Dict, List, Optional

from config import mcp, CONFIG, _normalize_boot_target
from helpers import _redfish_call, _get_handler, _ensure_boot_once_single
from cache import RESPONSE_CACHE, TTL_FIRMWARE_INVENTORY, TTL_HARDWARE_OVERVIEW, TTL_SYSTEM_INFO


@mcp.tool(description="Read current power state (On/Off) for servers, in parallel.")
async def get_power_state(server_ids: List[str]) -> List[Dict]:
    """Get current power state per server in parallel.

    Returns per-server
    {"server_id": "...", "status": "success", "power_state": "On|Off|Unknown"}
    """

    async def _get_single(server_id: str) -> Dict:
        try:
            handler = await _get_handler(server_id)
            result = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
            if result.get("status") == "success":
                power_state = result.get("data", {}).get("PowerState", "Unknown")
                return {"server_id": server_id, "status": "success", "power_state": power_state}
            return {"server_id": server_id, "status": "error", "message": result.get("message")}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_get_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Set power state via Redfish Reset action (Graceful/Force), in parallel.")
async def set_power_state(server_ids: List[str], state: str) -> List[Dict]:
    """Set server power state using Redfish ComputerSystem.Reset.

    Args
    - state: ResetType (e.g., On, ForceOff, GracefulShutdown, ForceRestart).
    """

    async def _set_single(server_id: str, power_state: str) -> Dict:
        try:
            handler = await _get_handler(server_id)
            path = f"{handler.SYSTEM_PATH}/Actions/ComputerSystem.Reset"
            payload = {"ResetType": power_state}
            result = await _redfish_call(server_id, "POST", path, payload)
            return {"server_id": server_id, **result}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_set_single(server_id, state) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(
    description=(
        "Retrieve granular firmware components like NICs, RAID controllers (PERC/Smart Array), "
        "Physical Drives/SSDs, Power Supplies, CPLD, and Backplanes. "
        "DO NOT USE THIS TOOL for basic BIOS or base BMC/iDRAC/iLO versions — use `get_system_info` instead,"
        "which is much faster. "
        "Uses a single optimized Redfish request for Dell ($expand) and concurrently fetches members for HPE. "
        "Always use `name_filter` (e.g., ['NIC', 'RAID', 'SSD']) to limit the results."
    )
)
async def get_firmware_inventory(server_ids: List[str], name_filter: Optional[List[str]] = None) -> List[Dict]:
    """Get firmware inventory per server in parallel.

    Args
    - server_ids: List of host ids.
    - name_filter: Optional list of substrings to match against component names (case-insensitive).
      A component is included if any filter substring appears in its name.
      e.g. ["NIC", "RAID"] returns only those firmware entries.
      Omit to return all firmware components.

    Returns per-server
    {"server_id": "...", "status": "success", "inventory": [{"name": "...", "version": "..."}]}
    """

    async def _get_single(server_id: str) -> Dict:
        try:
            cache_key = f"{server_id}:firmware_inventory"
            cached = RESPONSE_CACHE.get(cache_key)
            if cached is not None:
                inventory = (
                    cached
                    if not name_filter
                    else [e for e in cached if any(f.lower() in e["name"].lower() for f in name_filter)]
                )
                return {"server_id": server_id, "status": "success", "inventory": inventory}

            handler = await _get_handler(server_id)

            # Dell Optimization Path
            if handler.__class__.__name__ == "Dell":
                path = f"{handler.UPDATE_SERVICE_PATH}/FirmwareInventory?$expand=*($levels=1)"
                result = await _redfish_call(server_id, "GET", path)
                if result.get("status") == "success":
                    members = result.get("data", {}).get("Members", [])
                    full_inventory = [{"name": m.get("Name", ""), "version": m.get("Version", "")} for m in members]
                    RESPONSE_CACHE.set(cache_key, full_inventory, TTL_FIRMWARE_INVENTORY)
                    inventory = (
                        full_inventory
                        if not name_filter
                        else [e for e in full_inventory if any(f.lower() in e["name"].lower() for f in name_filter)]
                    )
                    return {"server_id": server_id, "status": "success", "inventory": inventory}
                return {"server_id": server_id, "status": "error", "message": result.get("message")}

            # HPE / Generic Fallback Path
            else:
                path = f"{handler.UPDATE_SERVICE_PATH}/FirmwareInventory"
                result = await _redfish_call(server_id, "GET", path)
                if result.get("status") != "success":
                    return {"server_id": server_id, "status": "error", "message": result.get("message")}

                member_links = [
                    m.get("@odata.id") for m in result.get("data", {}).get("Members", []) if m.get("@odata.id")
                ]

                async def _fetch_member(link: str) -> Optional[Dict]:
                    resp = await _redfish_call(server_id, "GET", link)
                    if resp.get("status") == "success":
                        data = resp.get("data", {})
                        return {"name": data.get("Name", ""), "version": data.get("Version", "")}
                    return None

                fetch_tasks = [_fetch_member(link) for link in member_links]
                fetch_results = await asyncio.gather(*fetch_tasks)

                full_inventory = [res for res in fetch_results if res is not None]
                RESPONSE_CACHE.set(cache_key, full_inventory, TTL_FIRMWARE_INVENTORY)
                inventory = (
                    full_inventory
                    if not name_filter
                    else [e for e in full_inventory if any(f.lower() in e["name"].lower() for f in name_filter)]
                )
                return {"server_id": server_id, "status": "success", "inventory": inventory}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_get_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(
    description=(
        "Basic system summary (vendor, model, serial, power, health, BIOS version, BMC firmware version),"
        " in parallel. Lightweight — only 2 Redfish requests per server. Use this instead of "
        "get_firmware_inventory when you only need BIOS and iDRAC/iLO versions. Works across all vendors"
        " (Dell, HPE, Supermicro) and all generations."
    )
)
async def get_system_info(server_ids: List[str]) -> List[Dict]:
    """Get high-level system info per server in parallel.
    Includes manufacturer, model, serial number, power state, health, BIOS version, firmware (iDRAC) version.

    Returns per-server
    {"server_id": "...", "status": "success", "info": {"manufacturer": "...", ...}}
    """

    async def _get_single(server_id: str) -> Dict:
        try:
            cache_key = f"{server_id}:system_info"
            cached = RESPONSE_CACHE.get(cache_key)
            if cached is not None:
                return cached

            handler = await _get_handler(server_id)
            # Fetch system and manager info in parallel
            result, result2 = await asyncio.gather(
                _redfish_call(server_id, "GET", handler.SYSTEM_PATH),
                _redfish_call(server_id, "GET", handler.MANAGER_PATH),
            )
            info = {}
            if result.get("status") == "success":
                data = result.get("data", {})
                info["manufacturer"] = data.get("Manufacturer")
                info["model"] = data.get("Model")
                info["serial_number"] = data.get("SerialNumber")
                info["power_state"] = data.get("PowerState")
                info["health"] = data.get("Status", {}).get("Health")
                info["bios_version"] = data.get("BiosVersion")
            if result2.get("status") == "success":
                data = result2.get("data", {})
                info["firmware_version"] = data.get("FirmwareVersion")
            if not info:
                return {"server_id": server_id, "status": "error", "message": "Failed to get system info"}
            out = {"server_id": server_id, "status": "success", "info": info}
            RESPONSE_CACHE.set(cache_key, out, TTL_SYSTEM_INFO)
            return out
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_get_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Unified hardware inventory: CPUs, memory, NICs, drives, volumes (Dell/HPE compatible).")
async def get_hardware_overview(server_ids: List[str]) -> List[Dict]:
    """Aggregates CPU, memory, NIC, and storage (drives/volumes) info using standard Redfish paths.

    Targets broad compatibility with Dell and HPE by using:
    - Systems/{id}
    - Systems/{id}/Processors
    - Systems/{id}/Memory
    - Systems/{id}/EthernetInterfaces
    - Systems/{id}/Storage (then Drives/Volumes)
    """

    async def _collect_single(server_id: str) -> Dict:
        try:
            cache_key = f"{server_id}:hardware_overview"
            cached = RESPONSE_CACHE.get(cache_key)
            if cached is not None:
                return cached

            handler = await _get_handler(server_id)

            # Base system info
            system_resp = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
            if system_resp.get("status") != "success":
                return {"server_id": server_id, **system_resp}
            system = system_resp.get("data", {}) or {}

            system_summary = {
                "manufacturer": system.get("Manufacturer"),
                "model": system.get("Model"),
                "serial_number": system.get("SerialNumber"),
                "power_state": system.get("PowerState"),
                "health": (system.get("Status") or {}).get("Health"),
                "processor_summary": system.get("ProcessorSummary"),
                "memory_summary": system.get("MemorySummary"),
            }

            # Helper to fetch collection members' details in parallel
            async def _fetch_members(collection_path: str) -> List[Dict]:
                coll = await _redfish_call(server_id, "GET", collection_path)
                if coll.get("status") != "success":
                    return []
                members = (coll.get("data", {}) or {}).get("Members", [])
                paths = [m.get("@odata.id") for m in members if m.get("@odata.id")]
                if not paths:
                    return []
                results = await asyncio.gather(*[_redfish_call(server_id, "GET", p) for p in paths])
                return [r.get("data", {}) for r in results if r.get("status") == "success"]

            # Fetch all four subsystems in parallel
            procs_path = f"{handler.SYSTEM_PATH}/Processors"
            mem_path = f"{handler.SYSTEM_PATH}/Memory"
            nics_path = f"{handler.SYSTEM_PATH}/EthernetInterfaces"
            storage_coll_path = f"{handler.SYSTEM_PATH}/Storage"

            proc_data, mem_data, nic_data, storage_members = await asyncio.gather(
                _fetch_members(procs_path),
                _fetch_members(mem_path),
                _fetch_members(nics_path),
                _fetch_members(storage_coll_path),
            )

            # Process processors
            processors = [
                {
                    "id": p.get("Id"),
                    "name": p.get("Name"),
                    "model": p.get("Model"),
                    "total_cores": p.get("TotalCores"),
                    "total_threads": p.get("TotalThreads"),
                    "max_speed_mhz": p.get("MaxSpeedMHz"),
                    "manufacturer": p.get("Manufacturer"),
                    "socket": p.get("Socket"),
                    "status": p.get("Status"),
                }
                for p in proc_data
            ]

            # Process memory modules
            memory_modules = [
                {
                    "id": m.get("Id"),
                    "name": m.get("Name"),
                    "capacity_mib": m.get("CapacityMiB"),
                    "device_type": m.get("MemoryDeviceType"),
                    "speed_mhz": m.get("OperatingSpeedMhz"),
                    "manufacturer": m.get("Manufacturer"),
                    "serial_number": m.get("SerialNumber"),
                    "part_number": m.get("PartNumber"),
                    "status": m.get("Status"),
                }
                for m in mem_data
            ]

            # Process NICs
            nics = [
                {
                    "id": n.get("Id"),
                    "name": n.get("Name"),
                    "mac": n.get("MACAddress"),
                    "link_speed_mbps": n.get("CurrentLinkSpeedMbps") or n.get("SpeedMbps"),
                    "ipv4": n.get("IPv4Addresses"),
                    "ipv6": n.get("IPv6Addresses"),
                    "status": n.get("Status"),
                }
                for n in nic_data
            ]

            # Process storage controllers: fetch drives and volumes per controller in parallel
            async def _process_storage_controller(storage_member: Dict) -> tuple:
                ctrl_drives: List[Dict] = []
                ctrl_volumes: List[Dict] = []
                storage_base = storage_member.get("@odata.id")

                # Drives: some vendors expose a Drives list of links directly
                drive_links = storage_member.get("Drives", [])
                if isinstance(drive_links, list) and drive_links:
                    dpaths = [link.get("@odata.id") for link in drive_links if link.get("@odata.id")]
                    if dpaths:
                        drive_results = await asyncio.gather(*[_redfish_call(server_id, "GET", dp) for dp in dpaths])
                        for d in drive_results:
                            if d.get("status") == "success":
                                data = d.get("data", {})
                                ctrl_drives.append(
                                    {
                                        "id": data.get("Id"),
                                        "name": data.get("Name"),
                                        "model": data.get("Model"),
                                        "serial_number": data.get("SerialNumber"),
                                        "capacity_bytes": data.get("CapacityBytes"),
                                        "media_type": data.get("MediaType"),
                                        "protocol": data.get("Protocol"),
                                        "status": data.get("Status"),
                                    }
                                )
                elif storage_base:
                    for ddata in await _fetch_members(f"{storage_base}/Drives"):
                        ctrl_drives.append(
                            {
                                "id": ddata.get("Id"),
                                "name": ddata.get("Name"),
                                "model": ddata.get("Model"),
                                "serial_number": ddata.get("SerialNumber"),
                                "capacity_bytes": ddata.get("CapacityBytes"),
                                "media_type": ddata.get("MediaType"),
                                "protocol": ddata.get("Protocol"),
                                "status": ddata.get("Status"),
                            }
                        )

                # Volumes
                if storage_base:
                    for v in await _fetch_members(f"{storage_base}/Volumes"):
                        ctrl_volumes.append(
                            {
                                "id": v.get("Id"),
                                "name": v.get("Name"),
                                "raid_type": v.get("RAIDType"),
                                "capacity_bytes": v.get("CapacityBytes"),
                                "status": v.get("Status"),
                            }
                        )

                return ctrl_drives, ctrl_volumes

            # Process all storage controllers in parallel
            drives: List[Dict] = []
            volumes: List[Dict] = []
            if storage_members:
                controller_results = await asyncio.gather(*[_process_storage_controller(sm) for sm in storage_members])
                for ctrl_drives, ctrl_volumes in controller_results:
                    drives.extend(ctrl_drives)
                    volumes.extend(ctrl_volumes)

            inventory = {
                "system": system_summary,
                "processors": processors,
                "memory": memory_modules,
                "nics": nics,
                "storage": {"drives": drives, "volumes": volumes},
            }
            result = {"server_id": server_id, "status": "success", "inventory": inventory}
            RESPONSE_CACHE.set(cache_key, result, TTL_HARDWARE_OVERVIEW)
            return result
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_collect_single(sid) for sid in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Detect and return vendor (dell/hpe/supermicro) for servers, in parallel.")
async def get_vendor(server_ids: List[str]) -> List[Dict]:
    """Gets the vendor for a list of servers in parallel."""

    async def _get_single(server_id: str) -> Dict:
        try:
            await _get_handler(server_id)
            vendor = CONFIG.get(server_id, {}).get("vendor", "Unknown")
            return {"server_id": server_id, "status": "success", "vendor": vendor}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    tasks = [_get_single(server_id) for server_id in server_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Declaratively set one-time boot target (pxe/cd/hdd/usb), optional reboot.")
async def ensure_boot_once(
    server_ids: List[str],
    target: str,
    mode: Optional[str] = None,
    reboot: bool = False,
    reboot_type: str = "GracefulRestart",
) -> List[Dict]:
    """Ensure next boot uses the given target once.

    - target: common names accepted (pxe, cd/dvd, hdd/disk, usb) or exact Redfish enum.
    - mode: optional "UEFI" or "Legacy"; if provided, will be set.
    - reboot: if True, triggers a reset using reboot_type.
    """

    desired_target = _normalize_boot_target(target)
    if not desired_target:
        return [
            {
                "server_id": sid,
                "status": "error",
                "message": f"Unsupported boot target '{target}'",
            }
            for sid in server_ids
        ]

    tasks = [
        _ensure_boot_once_single(sid, desired_target, mode=mode, reboot=reboot, reboot_type=reboot_type)
        for sid in server_ids
    ]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Manually clear the in-memory response cache for one or more servers.")
async def clear_server_cache(server_ids: List[str]) -> List[Dict]:
    """Force cache invalidation for the given servers.

    Use after out-of-band hardware changes or when cached data seems stale.
    Does not affect the Dell hardware inventory XML disk cache.

    Returns per-server
    {"server_id": "...", "status": "success", "cleared": <count of entries removed>}
    """
    results = []
    for server_id in server_ids:
        cleared = RESPONSE_CACHE.invalidate_prefix(f"{server_id}:")
        results.append({"server_id": server_id, "status": "success", "cleared": cleared})
    return results
