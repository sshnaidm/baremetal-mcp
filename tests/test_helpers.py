"""Tests for helpers.py - Redfish API call logic, vendor detection, virtual media, boot."""

import pytest
import httpx
from unittest.mock import AsyncMock

from conftest import (
    make_mock_response,
    DELL_R750_ROOT,
    DELL_R750_SYSTEM,
    DELL_R750_VM_COLLECTION,
    DELL_R750_VM_CD,
    DELL_R750_VM_REMOVABLE,
    HPE_DL380_ROOT,
    HPE_DL380_VM_COLLECTION,
    HPE_DL380_VM_1,
    HPE_DL380_VM_2,
    SUPERMICRO_ROOT,
)
from helpers import (
    _get_vendor_from_api,
    _get_handler,
    _redfish_call,
    _find_virtual_cd_path,
    _get_vm_path_and_state,
    _eject_virtual_media,
    _insert_virtual_media,
    _ensure_boot_once_single,
)


class TestGetVendorFromApi:
    async def test_dell_detected(self, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.1")
        assert vendor == "dell"

    async def test_hpe_detected(self, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(200, HPE_DL380_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.100")
        assert vendor == "hpe"

    async def test_supermicro_detected(self, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(200, SUPERMICRO_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.500")
        assert vendor == "supermicro"

    async def test_unknown_vendor_raises(self, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(200, {"Oem": {"UnknownVendor": {}}})})
        with pytest.raises(ConnectionError, match="Could not auto-detect"):
            await _get_vendor_from_api("10.0.0.99")

    async def test_http_error_raises(self, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(ConnectionError, match="Could not auto-detect"):
            await _get_vendor_from_api("10.0.0.99")


class TestGetHandler:
    async def test_cached_handler_returned(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        handler1 = await _get_handler("host1")
        handler2 = await _get_handler("host1")
        assert handler1 is handler2

    async def test_dell_handler_created(self, setup_dell_config, mock_redfish_client):
        from handlers import Dell

        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        handler = await _get_handler("host1")
        assert isinstance(handler, Dell)
        assert handler.auth == ("root", "test-pass-not-real")

    async def test_hpe_handler_created(self, setup_hpe_config, mock_redfish_client):
        from handlers import HPE

        mock_redfish_client({"/redfish/v1": make_mock_response(200, HPE_DL380_ROOT)})
        handler = await _get_handler("host100")
        assert isinstance(handler, HPE)
        assert handler.auth is None
        assert "Authorization" in handler.headers

    async def test_supermicro_handler_created(self, setup_supermicro_config, mock_redfish_client):
        from handlers import Supermicro

        mock_redfish_client({"/redfish/v1": make_mock_response(200, SUPERMICRO_ROOT)})
        handler = await _get_handler("host500")
        assert isinstance(handler, Supermicro)

    async def test_vendor_auto_detected(self, mock_redfish_client):
        import config

        config.CONFIG["host99"] = {"bmc_ip": "10.0.0.99"}
        config.SECRETS["host99"] = {"username": "root", "password": "pass"}
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        await _get_handler("host99")
        assert config.CONFIG["host99"]["vendor"] == "dell"

    async def test_server_not_in_config_raises(self):
        with pytest.raises(ValueError, match="not found in configuration"):
            await _get_handler("nonexistent")

    async def test_unsupported_vendor_raises(self, mock_redfish_client):
        import config

        config.CONFIG["host99"] = {"bmc_ip": "10.0.0.99", "vendor": "cisco"}
        config.SECRETS["host99"] = {}
        with pytest.raises(ValueError, match="Unsupported vendor"):
            await _get_handler("host99")

    async def test_default_credentials_when_no_secrets(self, mock_redfish_client):
        import config
        from handlers import Dell

        config.CONFIG["host99"] = {"bmc_ip": "10.0.0.99", "vendor": "dell"}
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        handler = await _get_handler("host99")
        assert isinstance(handler, Dell)
        assert handler.auth == ("root", "calvin")


class TestRedfishCall:
    async def test_successful_get_json(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/redfish/v1/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert result["data"]["Manufacturer"] == "Dell Inc."

    async def test_successful_post_empty_content(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Actions/ComputerSystem.Reset": make_mock_response(204, content=b"")})
        result = await _redfish_call(
            "host1",
            "POST",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )
        assert result["status"] == "success"
        assert "Operation successful" in result["data"]

    async def test_non_json_response(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/hw_inventory": make_mock_response(200, content=b"<xml>data</xml>")})
        result = await _redfish_call("host1", "GET", "/hw_inventory", json_response=False)
        assert result["status"] == "success"
        assert result["data"] == b"<xml>data</xml>"

    async def test_5xx_retry_then_success(self, setup_dell_config, monkeypatch):
        call_count = 0

        async def _mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_mock_response(500, {"error": "Internal"})
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=_mock_request)
        monkeypatch.setattr("helpers._http_client", mock_client)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("helpers.BACKOFF_FACTOR", 0)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert call_count == 2

    async def test_4xx_no_retry(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/redfish/v1/Systems/System.Embedded.1": make_mock_response(404, {"error": "Not Found"})})
        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "error"

    async def test_connection_error_retry(self, setup_dell_config, monkeypatch):
        call_count = 0

        async def _mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=_mock_request)
        monkeypatch.setattr("helpers._http_client", mock_client)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("helpers.BACKOFF_FACTOR", 0)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert call_count == 2

    async def test_handler_exception_returns_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await _redfish_call("nonexistent", "GET", "/redfish/v1")
        assert result["status"] == "error"


class TestFindVirtualCdPath:
    async def test_cached_path_returned(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({})  # should not be called
        path = await _find_virtual_cd_path("host1")
        assert path == "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"

    async def test_dell_cd_found(self, setup_dell_config, mock_redfish_client):
        def _route(method, url, **kwargs):
            url_str = str(url)
            if url_str.endswith("/VirtualMedia/CD"):
                return make_mock_response(200, DELL_R750_VM_CD)
            if url_str.endswith("/VirtualMedia/RemovableDisk"):
                return make_mock_response(200, DELL_R750_VM_REMOVABLE)
            if url_str.endswith("/VirtualMedia"):
                return make_mock_response(200, DELL_R750_VM_COLLECTION)
            return make_mock_response(404)

        mock_redfish_client({"//": _route})
        path = await _find_virtual_cd_path("host1")
        assert "VirtualMedia/CD" in path

    async def test_hpe_skip_floppy_find_cd(self, setup_hpe_config, mock_redfish_client):
        def _route(method, url, **kwargs):
            url_str = str(url)
            if url_str.endswith("/VirtualMedia/1"):
                return make_mock_response(200, HPE_DL380_VM_1)
            if url_str.endswith("/VirtualMedia/2"):
                return make_mock_response(200, HPE_DL380_VM_2)
            if url_str.endswith("/VirtualMedia"):
                return make_mock_response(200, HPE_DL380_VM_COLLECTION)
            return make_mock_response(404)

        mock_redfish_client({"//": _route})
        path = await _find_virtual_cd_path("host100")
        assert "VirtualMedia/2" in path

    async def test_no_cd_drive_raises(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client(
            {
                "/VirtualMedia/RemovableDisk": make_mock_response(200, {"MediaTypes": ["USBStick"], "Inserted": False}),
                "/Managers/iDRAC.Embedded.1/VirtualMedia": make_mock_response(
                    200,
                    {"Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/RemovableDisk"}]},
                ),
            }
        )
        with pytest.raises(ValueError, match="No suitable virtual CD"):
            await _find_virtual_cd_path("host1")

    async def test_collection_fetch_fails_raises(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Managers/iDRAC.Embedded.1/VirtualMedia": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(ValueError, match="Could not retrieve"):
            await _find_virtual_cd_path("host1")


class TestGetVmPathAndState:
    async def test_normal_not_inserted(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD)})
        state = await _get_vm_path_and_state("host1")
        assert state["inserted"] is False
        assert state["image"] is None
        assert "vm_path" in state

    async def test_inserted_true(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image="http://iso/test.iso")
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, inserted_cd)})
        state = await _get_vm_path_and_state("host1")
        assert state["inserted"] is True
        assert state["image"] == "http://iso/test.iso"

    async def test_fetch_fails_raises(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(RuntimeError):
            await _get_vm_path_and_state("host1")


class TestEjectInsertVirtualMedia:
    async def test_eject(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b"")})
        result = await _eject_virtual_media("host1", "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD")
        assert result["status"] == "success"

    async def test_insert(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b"")})
        result = await _insert_virtual_media(
            "host1", "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD", "http://iso/test.iso"
        )
        assert result["status"] == "success"


class TestEnsureBootOnceSingle:
    async def test_already_set_idempotent(self, setup_dell_config, mock_redfish_client):
        system_already_set = dict(
            DELL_R750_SYSTEM,
            Boot={
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Pxe",
                "BootSourceOverrideMode": "UEFI",
            },
        )
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, system_already_set)})
        result = await _ensure_boot_once_single("host1", "Pxe")
        assert result["status"] == "success"
        assert "already set" in result["message"]

    async def test_needs_change(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Pxe")
        assert result["status"] == "success"
        assert "Boot override set" in result["message"]

    async def test_with_mode_uefi(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Cd", mode="uefi")
        assert result["status"] == "success"

    async def test_with_mode_legacy(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Cd", mode="legacy")
        assert result["status"] == "success"

    async def test_with_reboot(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client(
            {
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/Actions/ComputerSystem.Reset": make_mock_response(204, content=b""),
            }
        )
        result = await _ensure_boot_once_single("host1", "Pxe", reboot=True)
        assert result["status"] == "success"
        assert "GracefulRestart" in result["message"]

    async def test_system_fetch_fails(self, setup_dell_config, mock_redfish_client):
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(500, {"error": "fail"})})
        result = await _ensure_boot_once_single("host1", "Pxe")
        assert result["status"] == "error"

    async def test_exception_returns_error(self):
        result = await _ensure_boot_once_single("nonexistent", "Pxe")
        assert result["status"] == "error"
