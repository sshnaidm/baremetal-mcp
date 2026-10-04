"""
Vendor-specific Redfish handlers for Dell, HPE, and Supermicro.
"""

import base64
from typing import Any


class BaseVendorHandler:
    """Base class for all vendor-specific Redfish handlers."""

    SYSTEM_PATH: str
    MANAGER_PATH: str
    UPDATE_SERVICE_PATH: str
    HW_INVENTORY_PATH: str

    def __init__(self, user: str | None, password: str | None) -> None:
        if not isinstance(user, str) or not user.strip() or not isinstance(password, str) or not password.strip():
            raise ValueError("BMC username and password are required")
        self.auth: tuple | None = None
        self.headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        self._configure_auth(user, password)

    def _configure_auth(self, user: str | None, password: str | None) -> None:
        """Set up authentication, override in subclasses if needed."""
        self.auth = (user, password)

    def get_request_args(self) -> dict[str, Any]:
        """Return common request arguments."""
        args = {"headers": self.headers}
        if self.auth:
            args["auth"] = self.auth
        return args


class Dell(BaseVendorHandler):
    """Handler for Dell iDRAC."""

    SYSTEM_PATH = "/redfish/v1/Systems/System.Embedded.1"
    MANAGER_PATH = "/redfish/v1/Managers/iDRAC.Embedded.1"
    UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
    HW_INVENTORY_PATH = (
        "redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService/Actions/DellLCService.ExportHWInventory"
    )

    def _configure_auth(self, user: str | None, password: str | None) -> None:
        self.auth = (user, password)


class HPE(BaseVendorHandler):
    """Handler for HPE iLO."""

    SYSTEM_PATH = "/redfish/v1/Systems/1"
    MANAGER_PATH = "/redfish/v1/Managers/1"
    UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
    HW_INVENTORY_PATH = ""

    def _configure_auth(self, user: str | None, password: str | None) -> None:
        auth_string = base64.b64encode(f"{user}:{password}".encode()).decode("utf-8")
        self.headers["Authorization"] = f"Basic {auth_string}"


class Supermicro(BaseVendorHandler):
    """Handler for Supermicro."""

    SYSTEM_PATH = "/redfish/v1/Systems/1"
    MANAGER_PATH = "/redfish/v1/Managers/1"
    UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
    HW_INVENTORY_PATH = ""

    def _configure_auth(self, user: str | None, password: str | None) -> None:
        self.auth = (user, password)


VENDOR_MAP: dict[str, type[BaseVendorHandler]] = {
    "dell": Dell,
    "hpe": HPE,
    "supermicro": Supermicro,
}
