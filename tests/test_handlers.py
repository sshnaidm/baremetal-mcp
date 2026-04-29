"""Tests for handlers.py - vendor-specific handler classes."""

import base64

from handlers import BaseVendorHandler, Dell, HPE, Supermicro, VENDOR_MAP


class TestDellHandler:
    def test_default_credentials(self):
        handler = Dell(None, None)
        assert handler.auth == ("root", "calvin")

    def test_custom_credentials(self):
        handler = Dell("admin", "secret")
        assert handler.auth == ("admin", "secret")

    def test_paths(self):
        assert Dell.SYSTEM_PATH == "/redfish/v1/Systems/System.Embedded.1"
        assert Dell.MANAGER_PATH == "/redfish/v1/Managers/iDRAC.Embedded.1"
        assert Dell.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert Dell.HW_INVENTORY_PATH != ""

    def test_get_request_args(self):
        handler = Dell("root", "calvin")
        args = handler.get_request_args()
        assert "headers" in args
        assert "auth" in args
        assert args["auth"] == ("root", "calvin")
        assert args["headers"]["Content-Type"] == "application/json"


class TestHPEHandler:
    def test_default_credentials(self):
        handler = HPE(None, None)
        assert handler.auth is None
        expected = base64.b64encode(b"Administrator:password").decode("utf-8")
        assert handler.headers["Authorization"] == f"Basic {expected}"

    def test_custom_credentials(self):
        handler = HPE("admin", "secret123")
        expected = base64.b64encode(b"admin:secret123").decode("utf-8")
        assert handler.headers["Authorization"] == f"Basic {expected}"

    def test_auth_is_none(self):
        handler = HPE("admin", "pass")
        assert handler.auth is None

    def test_paths(self):
        assert HPE.SYSTEM_PATH == "/redfish/v1/Systems/1"
        assert HPE.MANAGER_PATH == "/redfish/v1/Managers/1"
        assert HPE.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert HPE.HW_INVENTORY_PATH == ""

    def test_get_request_args_no_auth_key(self):
        handler = HPE("admin", "pass")
        args = handler.get_request_args()
        assert "headers" in args
        assert "auth" not in args
        assert "Authorization" in args["headers"]


class TestSupermicroHandler:
    def test_default_credentials(self):
        handler = Supermicro(None, None)
        assert handler.auth == ("ADMIN", "ADMIN")

    def test_custom_credentials(self):
        handler = Supermicro("user", "pw")
        assert handler.auth == ("user", "pw")

    def test_paths(self):
        assert Supermicro.SYSTEM_PATH == "/redfish/v1/Systems/1"
        assert Supermicro.MANAGER_PATH == "/redfish/v1/Managers/1"
        assert Supermicro.UPDATE_SERVICE_PATH == "/redfish/v1/UpdateService"
        assert Supermicro.HW_INVENTORY_PATH == ""

    def test_get_request_args(self):
        handler = Supermicro("ADMIN", "ADMIN")
        args = handler.get_request_args()
        assert args["auth"] == ("ADMIN", "ADMIN")


class TestVendorMap:
    def test_keys(self):
        assert set(VENDOR_MAP.keys()) == {"dell", "hpe", "supermicro"}

    def test_values(self):
        assert VENDOR_MAP["dell"] is Dell
        assert VENDOR_MAP["hpe"] is HPE
        assert VENDOR_MAP["supermicro"] is Supermicro

    def test_all_inherit_base(self):
        for cls in VENDOR_MAP.values():
            assert issubclass(cls, BaseVendorHandler)
