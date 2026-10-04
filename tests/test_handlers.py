"""Tests for handlers.py - vendor-specific handler classes."""

from __future__ import annotations

import base64

import pytest

from handlers import HPE, VENDOR_MAP, BaseVendorHandler, Dell, Supermicro


class TestDellHandler:
    def test_missing_credentials_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="username and password"):
            Dell(None, None)

    def test_custom_credentials(self) -> None:
        handler = Dell("example-user", "example-password")
        assert handler.auth == ("example-user", "example-password")

    def test_paths(self) -> None:
        assert Dell.SYSTEM_PATH == "/redfish/v1/Systems/System.Embedded.1"
        assert Dell.MANAGER_PATH == "/redfish/v1/Managers/iDRAC.Embedded.1"
        assert Dell.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert Dell.HW_INVENTORY_PATH != ""

    def test_get_request_args(self) -> None:
        handler = Dell("example-dell-user", "example-dell-password")
        args = handler.get_request_args()
        assert "headers" in args
        assert "auth" in args
        assert args["auth"] == ("example-dell-user", "example-dell-password")
        assert args["headers"]["Content-Type"] == "application/json"


class TestHPEHandler:
    def test_missing_credentials_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="username and password"):
            HPE(None, None)

    def test_custom_credentials(self) -> None:
        handler = HPE("example-user", "example-password")
        expected = base64.b64encode(b"example-user:example-password").decode("utf-8")
        assert handler.headers["Authorization"] == f"Basic {expected}"

    def test_auth_is_none(self) -> None:
        handler = HPE("example-user", "example-password")
        assert handler.auth is None

    def test_paths(self) -> None:
        assert HPE.SYSTEM_PATH == "/redfish/v1/Systems/1"
        assert HPE.MANAGER_PATH == "/redfish/v1/Managers/1"
        assert HPE.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert HPE.HW_INVENTORY_PATH == ""

    def test_get_request_args_no_auth_key(self) -> None:
        handler = HPE("example-user", "example-password")
        args = handler.get_request_args()
        assert "headers" in args
        assert "auth" not in args
        assert "Authorization" in args["headers"]


class TestSupermicroHandler:
    def test_missing_credentials_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="username and password"):
            Supermicro(None, None)

    def test_custom_credentials(self) -> None:
        handler = Supermicro("example-user", "example-password")
        assert handler.auth == ("example-user", "example-password")

    def test_paths(self) -> None:
        assert Supermicro.SYSTEM_PATH == "/redfish/v1/Systems/1"
        assert Supermicro.MANAGER_PATH == "/redfish/v1/Managers/1"
        assert Supermicro.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert Supermicro.HW_INVENTORY_PATH == ""

    def test_get_request_args(self) -> None:
        handler = Supermicro("example-supermicro-user", "example-supermicro-password")
        args = handler.get_request_args()
        assert args["auth"] == ("example-supermicro-user", "example-supermicro-password")


class TestVendorMap:
    def test_keys(self) -> None:
        assert set(VENDOR_MAP.keys()) == {"dell", "hpe", "supermicro"}

    def test_values(self) -> None:
        assert VENDOR_MAP["dell"] is Dell
        assert VENDOR_MAP["hpe"] is HPE
        assert VENDOR_MAP["supermicro"] is Supermicro

    def test_all_inherit_base(self) -> None:
        for cls in VENDOR_MAP.values():
            assert issubclass(cls, BaseVendorHandler)
