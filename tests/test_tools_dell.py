"""Tests for tools/dell.py - Dell-specific operations."""

import os

from conftest import (
    make_mock_response,
    DELL_R750_MANAGER,
    DELL_R750_SYSTEM,
    MOCK_ISOS,
    MOCK_ISOS_FLAT,
)
from tools.dell import list_isos, dell_list_url, dell_export_hardware_inventory, dell_update_firmware


class TestListIsos:
    async def test_loaded(self):
        import config

        config.ISOS.update(MOCK_ISOS)
        result = await list_isos()
        assert result["status"] == "success"
        assert "dell_model_750_idrac_version_7" in result["data"]

    async def test_empty(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "ISOS_FILE", "/nonexistent/isos.yaml")
        monkeypatch.setattr(config, "CONFIG_FILE", "/nonexistent/servers.yaml")
        monkeypatch.setattr(config, "SECRETS_FILE", "/nonexistent/secrets.yaml")
        monkeypatch.setattr(config, "SETTINGS_FILE", "/nonexistent/settings.yaml")
        result = await list_isos()
        assert result["status"] == "success"
        assert result["data"] == {}


class TestDellListUrl:
    async def test_model_found_idrac(self):
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "idrac", "7")
        assert result["status"] == "success"
        assert result["data"] == "http://fw.local/idrac7.exe"

    async def test_model_found_bios(self):
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "bios", "1")
        assert result["status"] == "success"
        assert result["data"] == "http://fw.local/bios1.exe"

    async def test_model_not_found(self):
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_999", "idrac", "1")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    async def test_unknown_target(self):
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "raid", "1")
        assert result["status"] == "error"
        assert "Target" in result["message"]

    async def test_version_not_found(self):
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "idrac", "999")
        assert result["status"] == "success"
        assert result["data"] == {}


class TestDellExportHardwareInventory:
    async def test_no_hw_inventory_path(self, setup_hpe_config, hpe_routes, mock_redfish_client):
        mock_redfish_client(hpe_routes)
        result = await dell_export_hardware_inventory(["host100"])
        assert result[0]["status"] == "error"
        assert "No hardware inventory" in result[0]["message"]

    async def test_disk_cache_hit(self, setup_dell_config, dell_routes, mock_redfish_client, tmp_path, monkeypatch):
        cache_dir = tmp_path / "tmp"
        cache_dir.mkdir()
        cache_file = cache_dir / "host1"
        cache_file.write_bytes(b"<cached_xml>data</cached_xml>")

        mock_redfish_client(dell_routes)

        _real_join = os.path.join

        def patched_join(*args):
            if len(args) >= 2 and args[-1] == "host1":
                return str(cache_file)
            if len(args) >= 2 and args[-1] == "tmp":
                return str(cache_dir)
            return _real_join(*args)

        monkeypatch.setattr("tools.dell.os.path.join", patched_join)

        result = await dell_export_hardware_inventory(["host1"])
        assert result[0]["status"] == "success"
        assert result[0].get("cached") is True

    async def test_post_returns_no_location(self, setup_dell_config, mock_redfish_client):
        export_response = make_mock_response(200, json_data={"Message": "Export started"})
        routes = {
            "/DellLCService.ExportHWInventory": export_response,
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        }
        mock_redfish_client(routes)
        result = await dell_export_hardware_inventory(["host1"])
        assert result[0]["status"] == "error"
        assert "No hardware inventory path" in result[0]["message"]

    async def test_exception(self, mock_redfish_client):
        mock_redfish_client({})
        result = await dell_export_hardware_inventory(["nonexistent"])
        assert result[0]["status"] == "error"


class TestDellUpdateFirmware:
    async def test_success_without_reboot(self, setup_dell_config, mock_redfish_client):
        routes = {
            "/Actions/UpdateService.SimpleUpdate": make_mock_response(
                202,
                json_data={"Message": "ok"},
                headers={"Location": "/redfish/v1/TaskService/Tasks/JID_123"},
            ),
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        }
        mock_redfish_client(routes)
        result = await dell_update_firmware("host1", "http://fw.local/idrac.exe")
        assert result["status"] == "success"
        assert "message" in result

    async def test_success_with_reboot(self, setup_dell_config, mock_redfish_client):
        routes = {
            "/Actions/UpdateService.SimpleUpdate": make_mock_response(
                202,
                json_data={"Message": "ok"},
                headers={"location": "/redfish/v1/TaskService/Tasks/JID_456"},
            ),
            "/Actions/ComputerSystem.Reset": make_mock_response(204, content=b""),
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
            "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
        }
        mock_redfish_client(routes)
        result = await dell_update_firmware("host1", "http://fw.local/bios.exe", reboot=True)
        assert result["status"] == "success"
        assert "reboot initiated" in result["message"]

    async def test_invalid_url(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER)})
        result = await dell_update_firmware("host1", "ftp://fw.local/file.exe")
        assert result["status"] == "error"
        assert "http" in result["message"].lower()

    async def test_update_fails(self, setup_dell_config, mock_redfish_client):
        routes = {
            "/Actions/UpdateService.SimpleUpdate": make_mock_response(500, {"error": "fail"}),
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        }
        mock_redfish_client(routes)
        result = await dell_update_firmware("host1", "http://fw.local/idrac.exe")
        assert result["status"] == "error"

    async def test_cache_invalidation(self, setup_dell_config, mock_redfish_client):
        from cache import RESPONSE_CACHE

        RESPONSE_CACHE.set("host1:firmware_inventory", [{"name": "old"}], 300)
        routes = {
            "/Actions/UpdateService.SimpleUpdate": make_mock_response(
                202,
                json_data={"Message": "ok"},
                headers={"location": "/redfish/v1/TaskService/Tasks/JID_789"},
            ),
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        }
        mock_redfish_client(routes)
        result = await dell_update_firmware("host1", "http://fw.local/idrac.exe")
        assert result["status"] == "success"
        assert RESPONSE_CACHE.get("host1:firmware_inventory") is None

    async def test_exception(self, mock_redfish_client):
        mock_redfish_client({})
        result = await dell_update_firmware("nonexistent", "http://fw.local/file.exe")
        assert result["status"] == "error"
