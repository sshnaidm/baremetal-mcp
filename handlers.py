#!/usr/bin/env python3
"""
Vendor-specific Redfish handlers for Dell, HPE, and Supermicro.
"""

import base64
from typing import Any, Dict, Optional, Type


class BaseVendorHandler:
    """Base class for all vendor-specific Redfish handlers."""

    SYSTEM_PATH: str
    MANAGER_PATH: str
    UPDATE_SERVICE_PATH: str
    HW_INVENTORY_PATH: str

    def __init__(self, user: Optional[str], password: Optional[str]):
        self.auth: Optional[tuple] = None
        self.headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        self._configure_auth(user, password)

    def _configure_auth(self, user: Optional[str], password: Optional[str]):
        """Set up authentication, override in subclasses if needed."""
        self.auth = (user, password)

    def get_request_args(self) -> Dict[str, Any]:
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

    def _configure_auth(self, user: Optional[str], password: Optional[str]):
        self.auth = (user or "root", password or "calvin")


class HPE(BaseVendorHandler):
    """Handler for HPE iLO."""

    SYSTEM_PATH = "/redfish/v1/Systems/1"
    MANAGER_PATH = "/redfish/v1/Managers/1"
    UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
    HW_INVENTORY_PATH = ""

    def _configure_auth(self, user: Optional[str], password: Optional[str]):
        user = user or "Administrator"
        password = password or "password"
        auth_string = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("utf-8")
        self.headers["Authorization"] = f"Basic {auth_string}"


class Supermicro(BaseVendorHandler):
    """Handler for Supermicro."""

    SYSTEM_PATH = "/redfish/v1/Systems/1"
    MANAGER_PATH = "/redfish/v1/Managers/1"
    UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
    HW_INVENTORY_PATH = ""

    def _configure_auth(self, user: Optional[str], password: Optional[str]):
        self.auth = (user or "ADMIN", password or "ADMIN")


VENDOR_MAP: Dict[str, Type[BaseVendorHandler]] = {
    "dell": Dell,
    "hpe": HPE,
    "supermicro": Supermicro,
}
