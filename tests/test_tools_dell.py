"""Tests for tools/dell.py - Dell-specific operations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from conftest import (
    DELL_R750_MANAGER,
    DELL_R750_SYSTEM,
    MOCK_ISOS,
    MOCK_ISOS_FLAT,
    make_mock_response,
)
from fastmcp import Client

from tools.dell import (
    _download_exported_xml,
    _validate_xml_identity,
    _xml_details,
    dell_export_hardware_inventory,
    dell_list_url,
    dell_update_firmware,
    export_hardware_inventory_xml,
    list_isos,
)


def _inventory_xml(service_tag: str = "B5TXMH3") -> bytes:
    return (
        '<CIM><INSTANCE CLASSNAME="DCIM_SystemView">'
        f'<PROPERTY NAME="ServiceTag"><VALUE>{service_tag}</VALUE></PROPERTY>'
        "</INSTANCE><Device/></CIM>"
    ).encode()


class TestListIsos:
    async def test_loaded(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS)
        result = await list_isos()
        assert result["status"] == "success"
        assert "dell_model_750_idrac_version_7" in result["data"]

    async def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        monkeypatch.setattr(config, "ISOS_FILE", "/nonexistent/isos.yaml")
        monkeypatch.setattr(config, "CONFIG_FILE", "/nonexistent/servers.yaml")
        monkeypatch.setattr(config, "SECRETS_FILE", "/nonexistent/secrets.yaml")
        monkeypatch.setattr(config, "SETTINGS_FILE", "/nonexistent/settings.yaml")
        result = await list_isos()
        assert result["status"] == "success"
        assert result["data"] == {}


class TestDellListUrl:
    async def test_model_found_idrac(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "idrac", "7")
        assert result["status"] == "success"
        assert result["data"] == "http://fw.local/idrac7.exe"

    async def test_model_found_bios(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "bios", "1")
        assert result["status"] == "success"
        assert result["data"] == "http://fw.local/bios1.exe"

    async def test_model_not_found(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_999", "idrac", "1")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    async def test_unknown_target(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "raid", "1")
        assert result["status"] == "error"
        assert "Target" in result["message"]

    async def test_version_not_found(self) -> None:
        import config

        config.ISOS.update(MOCK_ISOS_FLAT)
        result = await dell_list_url("model_750", "idrac", "999")
        assert result["status"] == "success"
        assert result["data"] == {}


class TestDellExportHardwareInventory:
    async def test_new_and_compatibility_tools_are_registered(self) -> None:
        import config

        async with Client(config.mcp) as client:
            names = {tool.name for tool in await client.list_tools()}

        assert "export_hardware_inventory_xml" in names
        assert "dell_export_hardware_inventory" in names

    async def test_no_hw_inventory_path(
        self,
        setup_hpe_config: None,
        hpe_routes: dict[str, httpx.Response],
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client(hpe_routes)
        result = await dell_export_hardware_inventory(["host100"])
        assert result[0]["status"] == "error"
        assert result[0]["unsupported"] is True
        assert "not supported" in result[0]["message"]

    async def test_disk_cache_hit(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        cache_file = tmp_path / "dell_host1.xml"
        cache_file.write_bytes(_inventory_xml())
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})

        result = await dell_export_hardware_inventory(["host1"])
        assert result[0]["status"] == "success"
        assert result[0].get("cached") is True
        assert result[0]["data"] == _inventory_xml().decode()
        assert result[0]["xml_identity"]["status"] == "verified"

    async def test_post_returns_no_location(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        export_response = make_mock_response(200, json_data={"Message": "Export started"})
        routes = {
            "/DellLCService.ExportHWInventory": export_response,
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
            "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
        }
        mock_redfish_client(routes)
        result = await dell_export_hardware_inventory(["host1"])
        assert result[0]["status"] == "error"
        assert "No hardware inventory path" in result[0]["message"]
        assert result[0]["retry_safe"] is False
        assert result[0]["outcome_unknown"] is True

    async def test_exception(
        self,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client({})
        result = await dell_export_hardware_inventory(["nonexistent"])
        assert result[0]["status"] == "error"

    async def test_polls_task_validates_xml_and_writes_manifest(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        get_count = 0

        def task_route(method: str, _url: str | httpx.URL, **_kwargs: object) -> httpx.Response:
            nonlocal get_count
            get_count += 1
            if get_count == 1:
                return make_mock_response(
                    202,
                    {"TaskState": "Running"},
                    headers={"Retry-After": "1"},
                )
            return make_mock_response(200, content=_inventory_xml())

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("tools.dell.asyncio.sleep", no_sleep)
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": make_mock_response(
                    202,
                    {"Message": "started"},
                    headers={"Location": "/redfish/v1/TaskService/TaskMonitors/JID_1"},
                ),
                "/TaskService/TaskMonitors/JID_1": task_route,
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )

        result = await export_hardware_inventory_xml(
            ["host1"], collection="cnfdr-2026-09-02", poll_interval_seconds=0.1
        )

        assert result["status"] == "success"
        assert result["succeeded"] == 1
        assert get_count == 2
        item = result["results"][0]
        assert item["root_tag"] == "CIM"
        assert item["cached"] is False
        assert "data" not in item
        xml_path = Path(item["file_path"])
        assert xml_path.read_bytes() == _inventory_xml()
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        assert manifest["summary"] == {"requested": 1, "succeeded": 1, "failed": 0, "unsupported": 0}
        assert "data" not in manifest["results"][0]

    async def test_task_failure_is_not_cached(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": make_mock_response(
                    202, {"Message": "started"}, headers={"location": "/task/JID_2"}
                ),
                "/task/JID_2": make_mock_response(200, {"TaskState": "Exception"}),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )

        result = await export_hardware_inventory_xml(["host1"], poll_interval_seconds=0.1)

        assert result["status"] == "error"
        assert "exception" in result["results"][0]["message"]
        assert result["results"][0]["retry_safe"] is False
        assert not (tmp_path / "dell_host1.xml").exists()

    async def test_completed_task_location_is_followed(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": make_mock_response(
                    202, {"Message": "started"}, headers={"location": "/task/JID_3"}
                ),
                "/task/JID_3": make_mock_response(
                    200,
                    {"TaskState": "Completed"},
                    headers={"Location": "/download/JID_3"},
                ),
                "/download/JID_3": make_mock_response(200, content=_inventory_xml()),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )

        result = await export_hardware_inventory_xml(["host1"], poll_interval_seconds=0.1)

        assert result["status"] == "success"
        assert Path(result["results"][0]["file_path"]).read_bytes() == _inventory_xml()

    def test_malformed_or_oversized_xml_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        with pytest.raises(ValueError, match="not valid XML"):
            _xml_details(b"not xml")

        monkeypatch.setattr(config, "HARDWARE_INVENTORY_MAX_BYTES", 1024)
        with pytest.raises(ValueError, match="exceeds"):
            _xml_details(b"<Inventory>" + (b"x" * 1024) + b"</Inventory>")

        with pytest.raises(ValueError, match="unexpected XML root"):
            _xml_details(b"<html><body>error</body></html>")

    async def test_collection_path_is_constrained(
        self, setup_dell_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))

        result = await export_hardware_inventory_xml(["host1"], collection="../escape")

        assert result["status"] == "error"
        assert "safe path component" in result["message"]

    async def test_configured_identity_mismatch_stops_before_export(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config

        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        config.CONFIG["host1"]["service_tag"] = "EXPECTED"
        called = False

        def export_route(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal called
            called = True
            return make_mock_response(500, {"error": "must not run"})

        observed = dict(DELL_R750_SYSTEM, SerialNumber="OTHER")
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": export_route,
                "/Systems/System.Embedded.1": make_mock_response(200, observed),
            }
        )

        result = await export_hardware_inventory_xml(["host1"])
        assert result["status"] == "error"
        assert result["results"][0]["identity_mismatch"] is True
        assert called is False

    async def test_cross_host_task_location_is_rejected(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": make_mock_response(
                    202,
                    {"Message": "started"},
                    headers={"location": "https://other.example/redfish/v1/task/1"},
                ),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )

        result = await export_hardware_inventory_xml(["host1"])

        assert result["status"] == "error"
        assert "outside the configured BMC" in result["results"][0]["message"]

    async def test_reports_partial_and_unsupported(
        self,
        setup_all_configs: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        mock_redfish_client(
            {
                "/DellLCService.ExportHWInventory": make_mock_response(
                    202, {"Message": "started"}, headers={"location": "/inventory/JID_4"}
                ),
                "/inventory/JID_4": make_mock_response(200, content=_inventory_xml()),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )

        result = await export_hardware_inventory_xml(["host1", "host100"])

        assert result["status"] == "partial"
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert result["unsupported"] == 1

    async def test_cached_xml_must_match_live_bmc_identity(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        (tmp_path / "dell_host1.xml").write_bytes(_inventory_xml("OTHER"))
        export_called = False

        def export_route(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal export_called
            export_called = True
            return make_mock_response(500, {"error": "refresh attempted"})

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/DellLCService.ExportHWInventory": export_route,
            }
        )

        result = await export_hardware_inventory_xml(["host1"])

        assert result["status"] == "error"
        assert export_called is True
        assert "HTTP 500" in result["results"][0]["message"]

    async def test_unavailable_live_identity_stops_before_export(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        called = False

        def export_route(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal called
            called = True
            return make_mock_response(202, {"Message": "started"})

        mock_redfish_client({"/DellLCService.ExportHWInventory": export_route})

        result = await export_hardware_inventory_xml(["host1"])

        assert result["status"] == "error"
        assert "identity" in result["results"][0]["message"].lower()
        assert called is False

    def test_realistic_cim_system_service_tag_is_accepted(self) -> None:
        payload = (
            b'<CIM><INSTANCE CLASSNAME="DCIM_SystemView">'
            b'<PROPERTY NAME="ServiceTag"><VALUE>B5TXMH3</VALUE></PROPERTY>'
            b"</INSTANCE></CIM>"
        )

        xml_bytes, _text, root_tag = _xml_details(payload)

        assert xml_bytes == payload
        assert root_tag == "CIM"

    def test_identity_uses_only_system_view_service_tag(self) -> None:
        payload = b"""<CIM>
        <INSTANCE CLASSNAME="DCIM_SystemView">
          <PROPERTY NAME="ServiceTag"><VALUE>WRONG-SYSTEM</VALUE></PROPERTY>
          <PROPERTY NAME="ChassisServiceTag"><VALUE>B5TXMH3</VALUE></PROPERTY>
        </INSTANCE>
        <INSTANCE CLASSNAME="DCIM_ChassisView">
          <PROPERTY NAME="ServiceTag"><VALUE>B5TXMH3</VALUE></PROPERTY>
        </INSTANCE>
        </CIM>"""
        identity = {
            "expected_serial_number": "B5TXMH3",
            "observed_serial_number": "B5TXMH3",
        }

        with pytest.raises(ValueError, match="does not match"):
            _validate_xml_identity(payload, identity)

    def test_conflicting_system_view_service_tags_are_rejected(self) -> None:
        payload = b"""<CIM>
        <INSTANCE CLASSNAME="DCIM_SystemView">
          <PROPERTY NAME="ServiceTag"><VALUE>B5TXMH3</VALUE></PROPERTY>
        </INSTANCE>
        <INSTANCE CLASSNAME="DCIM_SystemView">
          <PROPERTY NAME="ServiceTag"><VALUE>OTHER</VALUE></PROPERTY>
        </INSTANCE>
        </CIM>"""
        identity = {"observed_serial_number": "B5TXMH3"}

        with pytest.raises(ValueError, match="conflicting DCIM_SystemView"):
            _validate_xml_identity(payload, identity)

    @pytest.mark.parametrize(
        "payload",
        [
            b"not JSON or XML",
            b'<CIM><INSTANCE CLASSNAME="DCIM_SystemView">\x01</INSTANCE></CIM>',
        ],
    )
    async def test_malformed_terminal_payload_fails_without_polling(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch, payload: object
    ) -> None:
        calls = 0

        async def malformed_response(*_args: object, **_kwargs: object) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {
                "status": "success",
                "status_code": 200,
                "headers": {},
                "data": payload,
            }

        async def must_not_sleep(_delay: float) -> None:
            raise AssertionError("Malformed terminal payload must not be polled again")

        monkeypatch.setattr("tools.dell._redfish_call", malformed_response)
        monkeypatch.setattr("tools.dell.asyncio.sleep", must_not_sleep)

        with pytest.raises(ValueError, match="neither valid Dell XML nor a JSON task document"):
            await _download_exported_xml(
                "host1",
                "/redfish/v1/TaskService/TaskMonitors/JID_bad",
                poll_interval_seconds=0.1,
                timeout_seconds=30,
            )

        assert calls == 1

    async def test_bounded_concurrency_and_duplicate_suppression(
        self, setup_all_configs: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        active = 0
        maximum = 0

        async def fake_export(server_id: str, destination: str, **_kwargs: object) -> dict[str, Any]:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            return {"server_id": server_id, "status": "success"}

        monkeypatch.setattr("tools.dell._export_hardware_inventory_single", fake_export)
        result = await export_hardware_inventory_xml(["host1", "host200", "host500", "host1"], concurrency=2)

        assert result["status"] == "success"
        assert result["requested"] == 3
        assert result["duplicates_ignored"] == 1
        assert maximum == 2


class TestDellUpdateFirmware:
    async def test_success_without_reboot(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
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
        assert result["job_id"] == "JID_123"

    async def test_success_with_reboot(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
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
        assert result["job_id"] == "JID_456"

    async def test_invalid_url(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER)})
        for url in (
            "ftp://fw.local/file.exe",
            "https://root:secret@fw.local/file.exe",
            "https://fw.local/bad file.exe",
        ):
            result = await dell_update_firmware("host1", url)
            assert result["status"] == "error"

    async def test_reboot_must_be_boolean(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({})

        result = await dell_update_firmware("host1", "https://fw.local/file.exe", reboot="yes")

        assert result["status"] == "error"
        assert result["message"] == "reboot must be a boolean"

    async def test_update_fails(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        routes = {
            "/Actions/UpdateService.SimpleUpdate": make_mock_response(500, {"error": "fail"}),
            "/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        }
        mock_redfish_client(routes)
        result = await dell_update_firmware("host1", "http://fw.local/idrac.exe")
        assert result["status"] == "error"

    async def test_cache_invalidation(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
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

    async def test_exception(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({})
        result = await dell_update_firmware("nonexistent", "http://fw.local/file.exe")
        assert result["status"] == "error"


class TestDellExportIdentityCompatibility:
    @pytest.mark.parametrize("field", ["oem", "sku"])
    async def test_chassis_serial_is_separate_from_service_tag(
        self,
        field: str,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        system = dict(DELL_R750_SYSTEM, SerialNumber="CN-CHASSIS-SERIAL")
        if field == "oem":
            system["Oem"] = {"Dell": {"DellSystem": {"ChassisServiceTag": "B5TXMH3"}}}
        else:
            system["SKU"] = "B5TXMH3"
        mock_redfish_client(
            {
                "/Systems/System.Embedded.1": make_mock_response(200, system),
                "/DellLCService.ExportHWInventory": make_mock_response(202, {}, headers={"Location": "/inventory/tag"}),
                "/inventory/tag": make_mock_response(200, content=_inventory_xml()),
            }
        )
        result = await export_hardware_inventory_xml(["host1"], refresh=True)
        assert result["status"] == "success"
        identity = result["results"][0]["identity"]
        assert identity["observed_serial_number"] == "CN-CHASSIS-SERIAL"
        assert identity["observed_service_tag"] == "B5TXMH3"
        assert result["results"][0]["xml_identity"]["service_tags"] == ["B5TXMH3"]

    async def test_disagreeing_service_tag_fields_stop_before_post(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        system = dict(DELL_R750_SYSTEM, SKU="OTHER01", Oem={"Dell": {"DellSystem": {"ChassisServiceTag": "B5TXMH3"}}})
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, system)})
        result = await export_hardware_inventory_xml(["host1"], refresh=True)
        assert result["status"] == "error"
        assert result["results"][0]["identity_mismatch"]

    async def test_new_export_action_is_discovered_only_after_404(
        self,
        setup_dell_config: None,
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HARDWARE_INVENTORY_DIR", str(tmp_path))
        path = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
        target = path + "/Actions/DellLCService.ExportHWInventory"

        def action_response(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            assert method == "POST"
            assert kwargs["json"] == {"ShareType": "Local", "XMLSchema": "CIM-XML"}
            return make_mock_response(202, {}, headers={"Location": "/inventory/new"})

        mock_redfish_client(
            {
                "/redfish/v1/Dell/Managers/": make_mock_response(404, {}),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/Managers/iDRAC.Embedded.1": make_mock_response(
                    200, {"Links": {"Oem": {"Dell": {"DellLCService": {"@odata.id": path}}}}}
                ),
                path: make_mock_response(
                    200,
                    {
                        "Actions": {
                            "#DellLCService.ExportHWInventory": {
                                "target": target,
                                "XMLSchema@Redfish.AllowableValues": ["CIM-XML"],
                            }
                        }
                    },
                ),
                target: action_response,
                "/inventory/new": make_mock_response(200, content=_inventory_xml()),
            }
        )
        result = await export_hardware_inventory_xml(["host1"], refresh=True)
        assert result["status"] == "success"
