"""Tests for tools/server.py - power, firmware, system info, hardware, vendor, boot, cache."""

from conftest import (
    make_mock_response,
    DELL_R750_SYSTEM,
    DELL_R750_MANAGER,
    SUPERMICRO_SYSTEM,
    SUPERMICRO_MANAGER,
    SUPERMICRO_STORAGE_EMPTY,
    SUPERMICRO_SIMPLE_STORAGE,
    SUPERMICRO_SIMPLE_STORAGE_1,
)
from tools.server import (
    get_power_state,
    set_power_state,
    get_firmware_inventory,
    get_system_info,
    get_hardware_overview,
    get_vendor,
    ensure_boot_once,
    clear_server_cache,
)


class TestGetPowerState:
    async def test_dell_on(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await get_power_state(["host1"])
        assert len(result) == 1
        assert result[0]["status"] == "success"
        assert result[0]["power_state"] == "On"

    async def test_hpe_off(self, setup_hpe_config, hpe_routes, mock_redfish_client):
        mock_redfish_client(hpe_routes)
        result = await get_power_state(["host100"])
        assert result[0]["power_state"] == "Off"

    async def test_multiple_servers(self, setup_all_configs, dell_routes, hpe_routes, mock_redfish_client):
        routes = {**dell_routes, **hpe_routes}
        mock_redfish_client(routes)
        result = await get_power_state(["host1", "host100"])
        assert len(result) == 2

    async def test_error_returns_error_dict(self, mock_redfish_client):
        mock_redfish_client({})
        result = await get_power_state(["nonexistent"])
        assert result[0]["status"] == "error"


class TestSetPowerState:
    async def test_success(self, setup_dell_config, dell_routes, mock_redfish_client):
        routes = dict(dell_routes)
        routes["/Actions/ComputerSystem.Reset"] = make_mock_response(204, content=b"")
        mock_redfish_client(routes)
        result = await set_power_state(["host1"], "ForceRestart")
        assert result[0]["status"] == "success"

    async def test_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await set_power_state(["nonexistent"], "On")
        assert result[0]["status"] == "error"


class TestGetFirmwareInventory:
    async def test_dell_expand_path(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await get_firmware_inventory(["host1"])
        assert result[0]["status"] == "success"
        inv = result[0]["inventory"]
        assert any(i["name"] == "BIOS" for i in inv)
        assert any(i["name"] == "iDRAC" for i in inv)

    async def test_dell_with_name_filter(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await get_firmware_inventory(["host1"], name_filter=["NIC"])
        inv = result[0]["inventory"]
        assert all("NIC" in i["name"] or "nic" in i["name"].lower() for i in inv)

    async def test_hpe_member_by_member(self, setup_hpe_config, hpe_routes, mock_redfish_client):
        mock_redfish_client(hpe_routes)
        result = await get_firmware_inventory(["host100"])
        assert result[0]["status"] == "success"
        inv = result[0]["inventory"]
        assert len(inv) == 3
        assert any("iLO" in i["name"] for i in inv)

    async def test_hpe_with_name_filter(self, setup_hpe_config, hpe_routes, mock_redfish_client):
        mock_redfish_client(hpe_routes)
        result = await get_firmware_inventory(["host100"], name_filter=["NIC"])
        inv = result[0]["inventory"]
        assert len(inv) == 1
        assert "NIC" in inv[0]["name"]

    async def test_cached_inventory(self, setup_dell_config, dell_routes, mock_redfish_client):
        from cache import RESPONSE_CACHE

        cached_data = [{"name": "cached_fw", "version": "1.0"}]
        RESPONSE_CACHE.set("host1:firmware_inventory", cached_data, 300)
        mock_redfish_client(dell_routes)
        result = await get_firmware_inventory(["host1"])
        assert result[0]["inventory"][0]["name"] == "cached_fw"

    async def test_cached_with_filter(self, setup_dell_config, dell_routes, mock_redfish_client):
        from cache import RESPONSE_CACHE

        cached_data = [
            {"name": "NIC Firmware", "version": "1.0"},
            {"name": "BIOS", "version": "2.0"},
        ]
        RESPONSE_CACHE.set("host1:firmware_inventory", cached_data, 300)
        mock_redfish_client(dell_routes)
        result = await get_firmware_inventory(["host1"], name_filter=["NIC"])
        assert len(result[0]["inventory"]) == 1

    async def test_error_returns_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await get_firmware_inventory(["nonexistent"])
        assert result[0]["status"] == "error"


class TestGetSystemInfo:
    async def test_dell(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await get_system_info(["host1"])
        assert result[0]["status"] == "success"
        info = result[0]["info"]
        assert info["manufacturer"] == "Dell Inc."
        assert info["model"] == "PowerEdge R750"
        assert info["bios_version"] == "1.8.2"
        assert info["firmware_version"] == "7.20.70.50"

    async def test_hpe(self, setup_hpe_config, hpe_routes, mock_redfish_client):
        mock_redfish_client(hpe_routes)
        result = await get_system_info(["host100"])
        info = result[0]["info"]
        assert info["manufacturer"] == "HPE"
        assert info["firmware_version"] == "iLO 5 v2.78"

    async def test_cached(self, setup_dell_config, dell_routes, mock_redfish_client):
        from cache import RESPONSE_CACHE

        cached = {"server_id": "host1", "status": "success", "info": {"manufacturer": "Cached"}}
        RESPONSE_CACHE.set("host1:system_info", cached, 300)
        mock_redfish_client(dell_routes)
        result = await get_system_info(["host1"])
        assert result[0]["info"]["manufacturer"] == "Cached"

    async def test_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await get_system_info(["nonexistent"])
        assert result[0]["status"] == "error"


class TestGetHardwareOverview:
    async def test_dell_with_storage(self, setup_dell_config, mock_redfish_client):
        storage_collection = {
            "Members": [
                {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.SL.7-1"},
            ]
        }
        storage_member = {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.SL.7-1",
            "Drives": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.SL.7-1/Drives/Disk.Bay.4"}],
        }
        drive_data = {
            "Id": "Disk.Bay.4",
            "Name": "Solid State Disk 4",
            "Model": "KPM5XVUG960G",
            "SerialNumber": "Y9H0A011T3YE",
            "CapacityBytes": 960197124096,
            "MediaType": "SSD",
            "Protocol": "SAS",
            "Status": {"Health": "OK"},
        }
        proc_collection = {"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.1"}]}
        proc_data = {
            "Id": "CPU.Socket.1",
            "Name": "CPU 1",
            "Model": "Intel(R) Xeon(R) Gold 6330N",
            "TotalCores": 28,
            "TotalThreads": 56,
            "MaxSpeedMHz": 2200,
            "Manufacturer": "Intel",
            "Socket": "CPU.Socket.1",
            "Status": {"Health": "OK"},
        }
        mem_collection = {"Members": []}
        nic_collection = {"Members": []}
        chassis_collection = {"Members": [{"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"}]}
        network_adapters = {"Members": []}

        def _route(method, url, **kwargs):
            u = str(url)
            if "/Drives/Disk.Bay.4" in u:
                return make_mock_response(200, drive_data)
            if "/Volumes" in u:
                return make_mock_response(200, {"Members": []})
            if "/Storage/RAID.SL.7-1" in u:
                return make_mock_response(200, storage_member)
            if u.endswith("/Storage"):
                return make_mock_response(200, storage_collection)
            if "/CPU.Socket.1" in u:
                return make_mock_response(200, proc_data)
            if u.endswith("/Processors"):
                return make_mock_response(200, proc_collection)
            if u.endswith("/Memory"):
                return make_mock_response(200, mem_collection)
            if u.endswith("/EthernetInterfaces"):
                return make_mock_response(200, nic_collection)
            if "/NetworkAdapters" in u:
                return make_mock_response(200, network_adapters)
            if u.endswith("/Chassis"):
                return make_mock_response(200, chassis_collection)
            if u.endswith("/System.Embedded.1") and "/Systems/" in u:
                return make_mock_response(200, DELL_R750_SYSTEM)
            if "iDRAC.Embedded.1" in u:
                return make_mock_response(200, DELL_R750_MANAGER)
            return make_mock_response(200, {"Members": []})

        routes = {"//": _route}
        mock_redfish_client(routes)
        result = await get_hardware_overview(["host1"])
        assert result[0]["status"] == "success"
        inv = result[0]["inventory"]
        assert inv["system"]["manufacturer"] == "Dell Inc."
        assert len(inv["processors"]) == 1
        assert inv["processors"][0]["model"] == "Intel(R) Xeon(R) Gold 6330N"
        assert len(inv["storage"]["drives"]) >= 1

    async def test_supermicro_simple_storage_fallback(self, setup_supermicro_config, mock_redfish_client):
        proc_collection = {"Members": []}
        mem_collection = {"Members": []}
        nic_collection = {"Members": []}
        chassis_collection = {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]}
        network_adapters = {"Members": []}

        routes = {
            "/Systems/1/Storage": make_mock_response(200, SUPERMICRO_STORAGE_EMPTY),
            "/Systems/1/SimpleStorage/1": make_mock_response(200, SUPERMICRO_SIMPLE_STORAGE_1),
            "/Systems/1/SimpleStorage": make_mock_response(200, SUPERMICRO_SIMPLE_STORAGE),
            "/Systems/1/Processors": make_mock_response(200, proc_collection),
            "/Systems/1/Memory": make_mock_response(200, mem_collection),
            "/Systems/1/EthernetInterfaces": make_mock_response(200, nic_collection),
            "/redfish/v1/Chassis/1/NetworkAdapters": make_mock_response(200, network_adapters),
            "/redfish/v1/Chassis": make_mock_response(200, chassis_collection),
            "/Systems/1": make_mock_response(200, SUPERMICRO_SYSTEM),
            "/Managers/1": make_mock_response(200, SUPERMICRO_MANAGER),
        }
        mock_redfish_client(routes)
        result = await get_hardware_overview(["host500"])
        assert result[0]["status"] == "success"
        drives = result[0]["inventory"]["storage"]["drives"]
        assert len(drives) >= 1
        assert drives[0]["source"] == "SimpleStorage"

    async def test_cached(self, setup_dell_config, mock_redfish_client):
        from cache import RESPONSE_CACHE

        cached = {"server_id": "host1", "status": "success", "inventory": {"system": {"manufacturer": "Cached"}}}
        RESPONSE_CACHE.set("host1:hardware_overview", cached, 300)
        mock_redfish_client({})
        result = await get_hardware_overview(["host1"])
        assert result[0]["inventory"]["system"]["manufacturer"] == "Cached"

    async def test_system_fetch_fails(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(500, {"error": "fail"})})
        result = await get_hardware_overview(["host1"])
        assert result[0]["status"] == "error"

    async def test_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await get_hardware_overview(["nonexistent"])
        assert result[0]["status"] == "error"


class TestGetVendor:
    async def test_known_vendor(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await get_vendor(["host1"])
        assert result[0]["status"] == "success"
        assert result[0]["vendor"] == "dell"

    async def test_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await get_vendor(["nonexistent"])
        assert result[0]["status"] == "error"


class TestEnsureBootOnce:
    async def test_valid_alias(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await ensure_boot_once(["host1"], "pxe")
        assert result[0]["status"] == "success"

    async def test_invalid_target(self, setup_dell_config):
        result = await ensure_boot_once(["host1"], "badtarget")
        assert result[0]["status"] == "error"
        assert "Unsupported" in result[0]["message"]

    async def test_multiple_servers_invalid(self, setup_all_configs):
        result = await ensure_boot_once(["host1", "host100"], "badtarget")
        assert len(result) == 2
        assert all(r["status"] == "error" for r in result)


class TestClearServerCache:
    async def test_entries_cleared(self):
        from cache import RESPONSE_CACHE

        RESPONSE_CACHE.set("host1:fw", "a", 300)
        RESPONSE_CACHE.set("host1:sys", "b", 300)
        result = await clear_server_cache(["host1"])
        assert result[0]["cleared"] == 2

    async def test_no_entries(self):
        result = await clear_server_cache(["host1"])
        assert result[0]["cleared"] == 0
