"""Tests for helpers.py - Redfish API call logic, vendor detection, virtual media, boot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from conftest import (
    DELL_R750_ROOT,
    DELL_R750_SYSTEM,
    DELL_R750_VM_CD,
    DELL_R750_VM_COLLECTION,
    DELL_R750_VM_REMOVABLE,
    HPE_DL380_ROOT,
    HPE_DL380_VM_1,
    HPE_DL380_VM_2,
    HPE_DL380_VM_COLLECTION,
    SUPERMICRO_ROOT,
    make_mock_response,
)

from helpers import (
    _bmc_url_authority,
    _eject_virtual_media,
    _ensure_boot_once_single,
    _find_virtual_cd_path,
    _get_handler,
    _get_vendor_from_api,
    _get_vm_path_and_state,
    _insert_virtual_media,
    _redfish_call,
    _response_header,
)


class TestBmcUrlAuthority:
    @pytest.mark.parametrize(
        "value",
        [
            "https://10.0.0.1",
            "root@10.0.0.1",
            "10.0.0.1:443",
            "10.0.0.1/redfish/v1",
            "10.0.0.1?target=other",
            "10.0.0.1#fragment",
            "[2001:db8::1]",
            "fe80::1%eth0",
            "999.0.0.1",
            "2130706433",
            "0x7f000001",
            "0177.0.0.1",
            " bmc.example.test",
            "bmc.example.test\n",
        ],
    )
    def test_rejects_non_host_authorities(self, value: object) -> None:
        with pytest.raises(ValueError, match="bmc_ip"):
            _bmc_url_authority(value)

    def test_accepts_hostname_ipv4_and_bracketless_ipv6(self) -> None:
        assert _bmc_url_authority("bmc-1.example.test") == "bmc-1.example.test"
        assert _bmc_url_authority("192.0.2.10") == "192.0.2.10"
        assert _bmc_url_authority("2001:db8::10") == "[2001:db8::10]"


class TestGetVendorFromApi:
    async def test_dell_detected(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.1", 443)
        assert vendor == "dell"

    async def test_hpe_detected(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(200, HPE_DL380_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.100", 443)
        assert vendor == "hpe"

    async def test_supermicro_detected(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(200, SUPERMICRO_ROOT)})
        vendor = await _get_vendor_from_api("10.0.0.50", 443)
        assert vendor == "supermicro"

    async def test_unknown_vendor_raises(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(200, {"Oem": {"UnknownVendor": {}}})})
        with pytest.raises(ConnectionError, match="Could not auto-detect"):
            await _get_vendor_from_api("10.0.0.99", 443)

    async def test_http_error_raises(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(ConnectionError, match="Could not auto-detect"):
            await _get_vendor_from_api("10.0.0.99", 443)


class TestGetHandler:
    async def test_cached_handler_returned(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        handler1 = await _get_handler("host1")
        handler2 = await _get_handler("host1")
        assert handler1 is handler2

    async def test_dell_handler_created(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        from handlers import Dell

        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        handler = await _get_handler("host1")
        assert isinstance(handler, Dell)
        assert handler.auth == ("root", "test-pass-not-real")

    async def test_hpe_handler_created(
        self, setup_hpe_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        from handlers import HPE

        mock_redfish_client({"/redfish/v1": make_mock_response(200, HPE_DL380_ROOT)})
        handler = await _get_handler("host100")
        assert isinstance(handler, HPE)
        assert handler.auth is None
        assert "Authorization" in handler.headers

    async def test_supermicro_handler_created(
        self, setup_supermicro_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        from handlers import Supermicro

        mock_redfish_client({"/redfish/v1": make_mock_response(200, SUPERMICRO_ROOT)})
        handler = await _get_handler("host500")
        assert isinstance(handler, Supermicro)

    async def test_vendor_auto_detected(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        import config

        config.CONFIG["host99"] = {
            "bmc_ip": "10.0.0.99",
            "redfish": {"port": 443},
            "verify_ssl": False,
        }
        config.SECRETS["host99"] = {"username": "root", "password": "pass"}
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        await _get_handler("host99")
        assert config.CONFIG["host99"]["vendor"] == "dell"

    async def test_server_not_in_config_raises(self) -> None:
        with pytest.raises(ValueError, match="not found in configuration"):
            await _get_handler("nonexistent")

    async def test_unsupported_vendor_raises(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        import config

        config.CONFIG["host99"] = {"bmc_ip": "10.0.0.99", "vendor": "cisco"}
        config.CONFIG["host99"]["redfish"] = {"port": 443}
        config.CONFIG["host99"]["verify_ssl"] = False
        config.SECRETS["host99"] = {"username": "operator", "password": "placeholder"}
        with pytest.raises(ValueError, match="Unsupported vendor"):
            await _get_handler("host99")

    async def test_missing_credentials_fail_closed(
        self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        import config

        config.CONFIG["host99"] = {
            "bmc_ip": "10.0.0.99",
            "redfish": {"port": 443},
            "verify_ssl": False,
            "vendor": "dell",
        }
        mock_redfish_client({"/redfish/v1": make_mock_response(200, DELL_R750_ROOT)})
        with pytest.raises(ValueError, match="Missing BMC username or password"):
            await _get_handler("host99")

    async def test_credential_profile_is_used(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        import config

        config.CONFIG["profiled"] = {
            "bmc_ip": "10.0.0.55",
            "redfish": {"port": 8443},
            "verify_ssl": False,
            "vendor": "idrac",
            "credential_profile": "dell-special",
        }
        config.SECRETS["profiles"] = {"dell-special": {"username": "profile-user", "password": "profile-pass"}}
        mock_redfish_client({})

        handler = await _get_handler("profiled")

        assert handler.auth == ("profile-user", "profile-pass")
        assert config.CONFIG["profiled"]["vendor"] == "dell"


class TestRedfishCall:
    @pytest.mark.parametrize(
        "path",
        [
            "https://attacker.example/redfish/v1",
            "//attacker.example/redfish/v1",
        ],
    )
    async def test_rejects_absolute_and_network_paths_before_connecting(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        get_client = AsyncMock(side_effect=AssertionError("HTTP client must not be selected"))
        monkeypatch.setattr("helpers._get_http_client", get_client)

        result = await _redfish_call("host1", "GET", path)

        assert result["status"] == "error"
        assert "origin-relative" in result["message"]
        get_client.assert_not_awaited()

    @pytest.mark.parametrize(
        "path",
        [
            "/redfish/v1/../Managers/1",
            "/redfish/v1/./Systems/1",
            "/redfish/v1/%2e%2E/Managers/1",
            "/redfish/v1/%252e%252e/Managers/1",
        ],
    )
    async def test_rejects_dot_segment_paths_before_connecting(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        get_client = AsyncMock(side_effect=AssertionError("HTTP must not be attempted"))
        monkeypatch.setattr("helpers._get_http_client", get_client)

        result = await _redfish_call("host1", "GET", path)

        assert result["status"] == "error"
        assert "dot-segments" in result["message"]
        get_client.assert_not_awaited()

    @pytest.mark.parametrize(
        "bmc_ip",
        [
            "https://10.0.0.1",
            "root@10.0.0.1",
            "10.0.0.1:443",
            "10.0.0.1/redfish/v1",
            "//other.example.test",
        ],
    )
    async def test_rejects_ambiguous_configured_bmc_target_before_connecting(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch, bmc_ip: str
    ) -> None:
        import config

        config.CONFIG["host1"]["bmc_ip"] = bmc_ip
        get_client = AsyncMock(side_effect=AssertionError("HTTP must not be attempted"))
        monkeypatch.setattr("helpers._get_http_client", get_client)

        result = await _redfish_call("host1", "GET", "/redfish/v1")

        assert result["status"] == "error"
        assert "bmc_ip" in result["message"]
        get_client.assert_not_awaited()

    async def test_bracketless_ipv6_is_safely_bracketed_in_url(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        config.CONFIG["host1"]["bmc_ip"] = "2001:db8::10"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=make_mock_response(200, DELL_R750_SYSTEM))
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)

        result = await _redfish_call("host1", "GET", "/redfish/v1")

        assert result["status"] == "success"
        assert mock_client.request.await_args.args[1] == "https://[2001:db8::10]:443/redfish/v1"

    async def test_missing_redfish_port_fails_before_connecting(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        config.CONFIG["host1"].pop("redfish")
        get_client = AsyncMock(side_effect=AssertionError("HTTP must not be attempted"))
        monkeypatch.setattr("helpers._get_http_client", get_client)

        result = await _redfish_call("host1", "GET", "/redfish/v1")

        assert result["status"] == "error"
        assert "redfish" in result["message"]
        get_client.assert_not_awaited()

    async def test_accepts_relative_path_and_normalizes_leading_slash(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/redfish/v1/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})

        result = await _redfish_call("host1", "GET", "redfish/v1/Systems/System.Embedded.1")

        assert result["status"] == "success"

    async def test_successful_get_json(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client(
            {
                "/redfish/v1/Systems/System.Embedded.1": make_mock_response(
                    200,
                    DELL_R750_SYSTEM,
                    headers={"Location": "/redfish/v1/example"},
                )
            }
        )
        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert result["status_code"] == 200
        assert result["data"]["Manufacturer"] == "Dell Inc."
        assert result["headers"]["location"] == "/redfish/v1/example"
        assert _response_header(result, "LOCATION") == "/redfish/v1/example"

    async def test_successful_post_empty_content(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Actions/ComputerSystem.Reset": make_mock_response(204, content=b"")})
        result = await _redfish_call(
            "host1",
            "POST",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )
        assert result["status"] == "success"
        assert result["remote_request_sent"] is True
        assert result["retry_safe"] is False
        assert result["outcome_unknown"] is False
        assert "Operation successful" in result["data"]

    async def test_non_json_response(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/hw_inventory": make_mock_response(200, content=b"<xml>data</xml>")})
        result = await _redfish_call("host1", "GET", "/hw_inventory", json_response=False)
        assert result["status"] == "success"
        assert result["data"] == b"<xml>data</xml>"

    async def test_5xx_retry_then_success(self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = 0

        async def _mock_request(method: str, url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_mock_response(500, {"error": "Internal"})
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=_mock_request)
        monkeypatch.setattr("helpers._http_client", mock_client)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("config.BACKOFF_FACTOR", 0)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert call_count == 2

    async def test_mutating_5xx_is_not_retried(self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
        request = AsyncMock(
            side_effect=[
                make_mock_response(500, {"error": "possibly applied"}),
                make_mock_response(204, content=b""),
            ]
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = request
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("config.BACKOFF_FACTOR", 0)

        result = await _redfish_call(
            "host1",
            "POST",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )

        assert result["status"] == "error"
        assert result["status_code"] == 500
        assert result["remote_request_sent"] is True
        assert result["retry_safe"] is False
        assert result["outcome_unknown"] is True
        assert request.await_count == 1

    async def test_mutating_transport_error_is_not_retried(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = AsyncMock(side_effect=httpx.ConnectError("response outcome is unknown"))
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = request
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("config.BACKOFF_FACTOR", 0)

        result = await _redfish_call(
            "host1",
            "PATCH",
            "/redfish/v1/Systems/System.Embedded.1",
            {"Boot": {"BootSourceOverrideEnabled": "Once"}},
        )

        assert result["status"] == "error"
        assert result["remote_request_sent"] is None
        assert result["retry_safe"] is False
        assert result["outcome_unknown"] is True
        assert request.await_count == 1

    async def test_4xx_no_retry(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/redfish/v1/Systems/System.Embedded.1": make_mock_response(404, {"error": "Not Found"})})
        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "error"
        assert result["status_code"] == 404
        assert result["data"] == {"error": "Not Found"}

    async def test_connection_error_retry(self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = 0

        async def _mock_request(method: str, url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=_mock_request)
        monkeypatch.setattr("helpers._http_client", mock_client)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        monkeypatch.setattr("config.BACKOFF_FACTOR", 0)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert call_count == 2

    async def test_uses_runtime_config_values(self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        calls = []

        async def _mock_request(method: str, url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            calls.append(kwargs)
            if len(calls) == 1:
                return make_mock_response(500, {"error": "retry"})
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=_mock_request)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        config._load_config()
        monkeypatch.setattr(config, "DEFAULT_TIMEOUT", 17)
        monkeypatch.setattr(config, "MAX_RETRIES", 2)
        monkeypatch.setattr(config, "BACKOFF_FACTOR", 0)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")

        assert result["status"] == "success"
        assert len(calls) == 2
        assert all(call["timeout"] == 17 for call in calls)

    async def test_verify_ssl_selects_separate_verified_client(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        config.CONFIG["host1"]["verify_ssl"] = True
        selected = []
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=make_mock_response(200, DELL_R750_SYSTEM))

        def select_client(verify: bool = False) -> MagicMock:
            selected.append(verify)
            return mock_client

        monkeypatch.setattr("helpers._get_http_client", select_client)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")

        assert result["status"] == "success"
        assert selected == [True]

    async def test_invalid_verify_ssl_value_fails_closed(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        config.CONFIG["host1"]["verify_ssl"] = "false"
        get_client = AsyncMock(side_effect=AssertionError("HTTP must not be attempted"))
        monkeypatch.setattr("helpers._get_http_client", get_client)

        result = await _redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")

        assert result["status"] == "error"
        assert "verify_ssl must be a boolean" in result["message"]
        get_client.assert_not_awaited()

    async def test_handler_exception_returns_error(
        self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({})
        result = await _redfish_call("nonexistent", "GET", "/redfish/v1")
        assert result["status"] == "error"


class TestFindVirtualCdPath:
    async def test_cached_path_returned(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({})  # should not be called
        path = await _find_virtual_cd_path("host1")
        assert path == "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"

    async def test_dell_cd_found(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        def _route(method: str, url: str | httpx.URL, **kwargs: object) -> httpx.Response:
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

    async def test_hpe_skip_floppy_find_cd(
        self, setup_hpe_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        def _route(method: str, url: str | httpx.URL, **kwargs: object) -> httpx.Response:
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

    async def test_no_cd_drive_raises(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
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

    async def test_collection_fetch_fails_raises(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Managers/iDRAC.Embedded.1/VirtualMedia": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(ValueError, match="Could not retrieve"):
            await _find_virtual_cd_path("host1")


class TestGetVmPathAndState:
    async def test_normal_not_inserted(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD)})
        state = await _get_vm_path_and_state("host1")
        assert state["inserted"] is False
        assert state["image"] is None
        assert "vm_path" in state

    async def test_inserted_true(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image="http://iso/test.iso")
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, inserted_cd)})
        state = await _get_vm_path_and_state("host1")
        assert state["inserted"] is True
        assert state["image"] == "http://iso/test.iso"

    async def test_fetch_fails_raises(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(500, {"error": "fail"})})
        with pytest.raises(RuntimeError):
            await _get_vm_path_and_state("host1")


class TestEjectInsertVirtualMedia:
    async def test_eject(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b"")})
        result = await _eject_virtual_media("host1", "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD")
        assert result["status"] == "success"

    async def test_insert(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b"")})
        result = await _insert_virtual_media(
            "host1", "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD", "http://iso/test.iso"
        )
        assert result["status"] == "success"


class TestEnsureBootOnceSingle:
    async def test_already_set_idempotent(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
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
        assert result["power_state"] == DELL_R750_SYSTEM["PowerState"]

    async def test_needs_change(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Pxe")
        assert result["status"] == "success"
        assert "Boot override set" in result["message"]

    async def test_with_mode_uefi(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Cd", mode="uefi")
        assert result["status"] == "success"

    async def test_with_mode_legacy(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM)})
        result = await _ensure_boot_once_single("host1", "Cd", mode="legacy")
        assert result["status"] == "success"

    async def test_with_reboot(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client(
            {
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/Actions/ComputerSystem.Reset": make_mock_response(204, content=b""),
            }
        )
        result = await _ensure_boot_once_single("host1", "Pxe", reboot=True)
        assert result["status"] == "success"
        assert "GracefulRestart" in result["message"]

    async def test_system_fetch_fails(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(500, {"error": "fail"})})
        result = await _ensure_boot_once_single("host1", "Pxe")
        assert result["status"] == "error"

    async def test_exception_returns_error(self) -> None:
        result = await _ensure_boot_once_single("nonexistent", "Pxe")
        assert result["status"] == "error"


VIRTUAL_OPTICAL_PATH = "PciRoot(0x0)/Pci(0x14,0x0)/USB(0xD,0x0)/USB(0x3,0x0)/USB(0x1,0x0)"

DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY = {
    "Members": [
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/BootOptions/Boot0006",
            "DisplayName": "Unavailable: Fedora",
            "UefiDevicePath": f"{VIRTUAL_OPTICAL_PATH}/CDROM(0x1)/\\EFI\\fedora\\shimx64.efi",
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/BootOptions/Boot0003",
            "DisplayName": "RAID Controller in SL 3: redhat",
            "UefiDevicePath": "HD(2,GPT,E8B37EFE-232B-4244-9A7D-280034A1CB89)/\\EFI\\redhat\\shimx64.efi",
        },
        {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/BootOptions/Boot0001",
            "DisplayName": "Virtual Optical Drive",
            "UefiDevicePath": VIRTUAL_OPTICAL_PATH,
            "RelatedItem": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1/VirtualMedia"}],
        },
    ]
}


class TestEnsureBootOnceDellUefiTarget:
    async def test_default_keeps_cd_without_querying_boot_options(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        patches: list[Any] = []

        def boot_options(*_args: object, **_kwargs: object) -> httpx.Response:
            raise AssertionError("BootOptions must not be queried unless dell_uefi_target is enabled")

        def system(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            if method == "PATCH":
                patches.append(kwargs["json"])
                return make_mock_response(204, content=b"")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": boot_options,
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", mode="uefi")
        assert result["status"] == "success"
        assert result["boot_target"] == "Cd"
        assert result["uefi_target_path"] is None
        assert patches[0]["Boot"]["BootSourceOverrideTarget"] == "Cd"
        assert "UefiTargetBootSourceOverride" not in patches[0]["Boot"]

    async def test_cd_uses_virtual_optical_uefi_target_not_stale_entry(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        patches: list[Any] = []

        def system(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            if method == "PATCH":
                patches.append(kwargs["json"])
                return make_mock_response(204, content=b"")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": make_mock_response(
                    200, DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY
                ),
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", mode="uefi", dell_uefi_target=True)
        assert result["status"] == "success"
        assert result["boot_target"] == "UefiTarget"
        assert result["uefi_target_path"] == VIRTUAL_OPTICAL_PATH
        assert patches == [
            {
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": "UefiTarget",
                    "UefiTargetBootSourceOverride": VIRTUAL_OPTICAL_PATH,
                    "BootSourceOverrideMode": "UEFI",
                }
            }
        ]

    async def test_cd_resolves_members_without_expand(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        links = {"Members": [{"@odata.id": m["@odata.id"]} for m in DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY["Members"]]}
        routes: dict[str, Any] = {
            "/Systems/System.Embedded.1/BootOptions": make_mock_response(200, links),
            "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
        }
        for member in DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY["Members"]:
            routes[member["@odata.id"]] = make_mock_response(200, member)
        mock_redfish_client(routes)
        result = await _ensure_boot_once_single("host1", "Cd", dell_uefi_target=True)
        assert result["boot_target"] == "UefiTarget"
        assert result["uefi_target_path"] == VIRTUAL_OPTICAL_PATH

    async def test_legacy_mode_keeps_cd(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": make_mock_response(
                    200, DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY
                ),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", mode="legacy", dell_uefi_target=True)
        assert result["status"] == "success"
        assert result["boot_target"] == "Cd"
        assert result["uefi_target_path"] is None

    async def test_falls_back_to_cd_when_boot_options_unavailable(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        patches: list[Any] = []

        def system(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            if method == "PATCH":
                patches.append(kwargs["json"])
                return make_mock_response(204, content=b"")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": make_mock_response(404, {"error": "missing"}),
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", dell_uefi_target=True)
        assert result["status"] == "success"
        assert result["boot_target"] == "Cd"
        assert patches[0]["Boot"]["BootSourceOverrideTarget"] == "Cd"
        assert "UefiTargetBootSourceOverride" not in patches[0]["Boot"]

    async def test_pending_bios_uefi_path_is_idempotent(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        pending_system = dict(
            DELL_R750_SYSTEM,
            Boot={
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "UefiTarget",
                "BootSourceOverrideMode": "UEFI",
                "UefiTargetBootSourceOverride": None,
            },
        )
        patches: list[Any] = []

        def system(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            if method == "PATCH":
                patches.append(kwargs["json"])
                return make_mock_response(204, content=b"")
            return make_mock_response(200, pending_system)

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": make_mock_response(
                    200, DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY
                ),
                "/Systems/System.Embedded.1/Bios/Settings": make_mock_response(
                    200, {"Attributes": {"OneTimeUefiBootPath": VIRTUAL_OPTICAL_PATH}}
                ),
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", dell_uefi_target=True)
        assert result["status"] == "success"
        assert "already set" in result["message"]
        assert patches == []

    async def test_stale_uefi_target_is_repatched(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        stale_system = dict(
            DELL_R750_SYSTEM,
            Boot={
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "UefiTarget",
                "BootSourceOverrideMode": "UEFI",
                "UefiTargetBootSourceOverride": "HD(1,GPT,AAAA)/\\EFI\\BOOT\\BOOTX64.EFI",
            },
        )
        patches: list[Any] = []

        def system(method: str, _url: str | httpx.URL, **kwargs: object) -> httpx.Response:
            if method == "PATCH":
                patches.append(kwargs["json"])
                return make_mock_response(204, content=b"")
            return make_mock_response(200, stale_system)

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": make_mock_response(
                    200, DELL_BOOT_OPTIONS_WITH_STALE_CD_ENTRY
                ),
                "/Systems/System.Embedded.1/Bios/Settings": make_mock_response(200, {"Attributes": {}}),
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await _ensure_boot_once_single("host1", "Cd", dell_uefi_target=True)
        assert result["status"] == "success"
        assert "Boot override set" in result["message"]
        assert patches[0]["Boot"]["UefiTargetBootSourceOverride"] == VIRTUAL_OPTICAL_PATH

    async def test_non_cd_target_does_not_query_boot_options(
        self, setup_dell_config: None, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]
    ) -> None:
        def boot_options(*_args: object, **_kwargs: object) -> httpx.Response:
            raise AssertionError("BootOptions must not be queried for non-Cd targets")

        mock_redfish_client(
            {
                "/Systems/System.Embedded.1/BootOptions": boot_options,
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )
        result = await _ensure_boot_once_single("host1", "Pxe", dell_uefi_target=True)
        assert result["status"] == "success"
        assert result["boot_target"] == "Pxe"
