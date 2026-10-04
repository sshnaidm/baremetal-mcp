"""Focused tests for complete, bounded HPE hardware snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from handlers import HPE
from tools import hpe_hardware_export as hpe

SYSTEM = "/redfish/v1/Systems/1"
CHASSIS = "/redfish/v1/Chassis/1"


def _collection(path: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    return {"@odata.id": path, "Members": members, "Members@odata.count": len(members)}


def _routes() -> dict[str, Any]:
    root = {"ProtocolFeaturesSupported": {"ExpandQuery": {"MaxLevels": 1, "NoLinks": True}}}
    system = {
        "@odata.id": SYSTEM,
        "Manufacturer": "HPE",
        "Model": "ProLiant",
        "SerialNumber": "SER1",
        "Links": {"Chassis": [{"@odata.id": CHASSIS}]},
        "Processors": {"@odata.id": f"{SYSTEM}/Processors"},
        "Memory": {"@odata.id": f"{SYSTEM}/Memory"},
        "EthernetInterfaces": {"@odata.id": f"{SYSTEM}/EthernetInterfaces"},
        "NetworkInterfaces": {"@odata.id": f"{SYSTEM}/NetworkInterfaces"},
        "Storage": {"@odata.id": f"{SYSTEM}/Storage"},
    }
    chassis = {
        "@odata.id": CHASSIS,
        "SerialNumber": "SER1",
        "NetworkAdapters": {"@odata.id": f"{CHASSIS}/NetworkAdapters"},
        "PCIeDevices": {"@odata.id": f"{CHASSIS}/PCIeDevices"},
        "PCIeSlots": {"@odata.id": f"{CHASSIS}/PCIeSlots"},
        "Power": {"@odata.id": f"{CHASSIS}/Power"},
        "Thermal": {"@odata.id": f"{CHASSIS}/Thermal"},
    }
    adapter_path = f"{CHASSIS}/NetworkAdapters/A"
    routes = {
        "/redfish/v1/": root,
        SYSTEM: system,
        CHASSIS: chassis,
        f"{SYSTEM}/Processors?$expand=.": _collection(
            f"{SYSTEM}/Processors", [{"@odata.id": f"{SYSTEM}/Processors/1", "Id": "1"}]
        ),
        f"{SYSTEM}/Memory?$expand=.": _collection(
            f"{SYSTEM}/Memory",
            [
                {"@odata.id": f"{SYSTEM}/Memory/1", "Id": "1", "CapacityMiB": 16384, "Status": {"State": "Enabled"}},
                {"@odata.id": f"{SYSTEM}/Memory/2", "Id": "2", "CapacityMiB": 0, "Status": {"State": "Absent"}},
            ],
        ),
        f"{SYSTEM}/PCISlots?$expand=.": _collection(
            f"{SYSTEM}/PCISlots",
            [
                {
                    "@odata.id": f"{SYSTEM}/PCISlots/1",
                    "Id": "1",
                    "Status": {"OperationalStatus": [{"Status": "InUse"}]},
                },
                {
                    "@odata.id": f"{SYSTEM}/PCISlots/2",
                    "Id": "2",
                    "Status": {"OperationalStatus": [{"Status": "Empty"}]},
                },
            ],
        ),
        f"{SYSTEM}/EthernetInterfaces?$expand=.": _collection(f"{SYSTEM}/EthernetInterfaces", []),
        f"{SYSTEM}/NetworkInterfaces?$expand=.": _collection(f"{SYSTEM}/NetworkInterfaces", []),
        f"{SYSTEM}/Storage?$expand=.": _collection(f"{SYSTEM}/Storage", []),
        f"{CHASSIS}/NetworkAdapters?$expand=.": _collection(
            f"{CHASSIS}/NetworkAdapters",
            [
                {
                    "@odata.id": adapter_path,
                    "Id": "A",
                    "Model": "NIC",
                    "NetworkPorts": {"@odata.id": f"{adapter_path}/NetworkPorts"},
                    "NetworkDeviceFunctions": {"@odata.id": f"{adapter_path}/NetworkDeviceFunctions"},
                }
            ],
        ),
        f"{CHASSIS}/PCIeDevices?$expand=.": _collection(f"{CHASSIS}/PCIeDevices", []),
        f"{CHASSIS}/Devices?$expand=.": _collection(f"{CHASSIS}/Devices", []),
        f"{adapter_path}/NetworkPorts?$expand=.": _collection(
            f"{adapter_path}/NetworkPorts",
            [{"@odata.id": f"{adapter_path}/NetworkPorts/1", "Id": "1", "LinkStatus": "Up", "SignalDetected": True}],
        ),
        f"{adapter_path}/NetworkDeviceFunctions?$expand=.": _collection(f"{adapter_path}/NetworkDeviceFunctions", []),
        f"{CHASSIS}/Power": {},
        f"{CHASSIS}/Thermal": {},
        f"{CHASSIS}/PCIeSlots": {},
    }
    return routes


@pytest.fixture
def mocked_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, Any], list[tuple[str, str, str]], Path]:
    routes = _routes()
    calls = []

    async def redfish(server_id: str, method: str, path: str) -> dict[str, Any]:
        calls.append((server_id, method, path))
        if path not in routes:
            return {"status": "error", "message": "not found"}
        return {"status": "success", "data": routes[path]}

    async def handler(_server_id: str) -> HPE:
        return HPE("user", "password")

    monkeypatch.setattr(hpe, "_redfish_call", redfish)
    monkeypatch.setattr(hpe, "_get_handler", handler)
    monkeypatch.setattr(hpe, "_load_config", lambda: None)
    monkeypatch.setattr(hpe, "_collection_directory", lambda collection: tmp_path / (collection or "default"))
    monkeypatch.setitem(hpe.cfg.CONFIG, "host1", {"serial_number": "SER1"})
    return routes, calls, tmp_path


def test_export_expands_slots_and_follows_ports(
    mocked_export: tuple[dict[str, Any], list[tuple[str, str, str]], Path],
) -> None:
    _routes_dict, calls, tmp_path = mocked_export
    result = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="test"))
    assert result["status"] == "success"
    item = result["results"][0]
    payload = Path(item["file_path"]).read_bytes()
    document = json.loads(payload)
    assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    assert [slot["occupied"] for slot in document["summary"]["memory_slots"]] == [True, False]
    assert [slot["occupancy"] for slot in document["summary"]["pci_slots"]] == ["in_use", "empty"]
    assert document["summary"]["network_ports"][0]["link_status"] == "Up"
    assert all(method == "GET" for _, method, _ in calls)
    assert not any(path == f"{SYSTEM}/Memory/1" for _, _, path in calls)
    assert (tmp_path / "test" / "manifest.json").exists()


def test_identity_mismatch_refuses_snapshot(
    mocked_export: tuple[dict[str, Any], list[tuple[str, str, str]], Path],
) -> None:
    routes, _calls, tmp_path = mocked_export
    routes[SYSTEM]["SerialNumber"] = "OTHER"
    result = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="test"))
    assert result["status"] == "error"
    assert not list((tmp_path / "test").glob("hpe_*.json"))


def test_partial_read_does_not_replace_complete_snapshot(
    mocked_export: tuple[dict[str, Any], list[tuple[str, str, str]], Path],
) -> None:
    routes, _calls, _tmp_path = mocked_export
    success = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="test"))
    path = Path(success["results"][0]["file_path"])
    before = path.read_bytes()
    del routes[f"{SYSTEM}/Memory?$expand=."]
    partial = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="test"))
    assert partial["status"] == "error"
    assert partial["results"][0]["status"] == "partial"
    assert path.read_bytes() == before


def test_rejects_cross_origin_and_traversal_links() -> None:
    assert hpe._path("https://evil.example/redfish/v1/Systems/1") is None
    assert hpe._path("/redfish/v1/../outside") is None
    assert hpe._path("/redfish/v1/Systems/1") == "/redfish/v1/Systems/1"


def test_mcp_tool_is_registered() -> None:
    from fastmcp import Client

    async def names() -> set[str]:
        async with Client(hpe.mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    assert "export_hpe_hardware_inventory" in asyncio.run(names())


def _oem_routes(routes: dict[str, Any]) -> str:
    smart = f"{SYSTEM}/SmartStorage"
    base = f"{SYSTEM}/BaseNetworkAdapters"
    controller = f"{smart}/ArrayControllers/0"
    routes[SYSTEM]["Oem"] = {
        "Hpe": {"Links": {"SmartStorage": {"@odata.id": smart}, "NetworkAdapters": {"@odata.id": base}}}
    }
    routes[f"{CHASSIS}/NetworkAdapters?$expand=."] = _collection(f"{CHASSIS}/NetworkAdapters", [])
    routes[base + "?$expand=."] = _collection(
        base,
        [
            {
                "@odata.id": base + "/1",
                "Id": "1",
                "Name": "Gen10 NIC",
                "PhysicalPorts": [{"MacAddress": "aa:bb:cc:dd:ee:ff", "LinkStatus": None, "SpeedMbps": 0}],
            }
        ],
    )
    routes[smart] = {"@odata.id": smart, "Links": {"ArrayControllers": {"@odata.id": smart + "/ArrayControllers"}}}
    routes[smart + "/ArrayControllers?$expand=."] = _collection(
        smart + "/ArrayControllers",
        [{"@odata.id": controller, "Id": "0", "Links": {"PhysicalDrives": {"@odata.id": controller + "/DiskDrives"}}}],
    )
    routes[controller + "/DiskDrives?$expand=."] = _collection(
        controller + "/DiskDrives", [{"@odata.id": controller + "/DiskDrives/0", "Id": "0", "SerialNumber": "DISK1"}]
    )
    return controller


def test_oem_storage_and_nic_fallback_recovers_standard_storage_error(
    mocked_export: tuple[dict[str, Any], list[tuple[str, str, str]], Path],
) -> None:
    routes, _calls, _tmp = mocked_export
    _oem_routes(routes)
    del routes[f"{SYSTEM}/Storage?$expand=."]
    result = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="oem"))
    assert result["status"] == "success"
    document = json.loads(Path(result["results"][0]["file_path"]).read_text())
    controller = document["resources"]["oem_smart_storage"]["controllers"][0]
    assert controller["PhysicalDrives"][0]["SerialNumber"] == "DISK1"
    port = document["summary"]["network_ports"][0]
    assert port["addresses"] == ["aa:bb:cc:dd:ee:ff"]
    assert port["link_status"] is None
    assert port["speed_mbps"] == 0
    assert port["physical_port_number"] is None
    assert result["results"][0]["warnings"]


def test_incomplete_oem_storage_preserves_snapshot(
    mocked_export: tuple[dict[str, Any], list[tuple[str, str, str]], Path],
) -> None:
    routes, _calls, _tmp = mocked_export
    controller = _oem_routes(routes)
    result = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="oem"))
    path = Path(result["results"][0]["file_path"])
    before = path.read_bytes()
    del routes[f"{SYSTEM}/Storage?$expand=."]
    del routes[controller + "/DiskDrives?$expand=."]
    failed = asyncio.run(hpe.export_hpe_hardware_inventory(["host1"], collection="oem"))
    assert failed["status"] == "error"
    assert path.read_bytes() == before
