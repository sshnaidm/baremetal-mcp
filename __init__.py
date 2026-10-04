"""
Baremetal MCP Server - bare-metal infrastructure management for AI assistants.

Provides MCP tools and resources for managing servers via Redfish API (Dell, HPE,
Supermicro) and Junos switches via SSH.
"""

from config import (
    CONFIG,
    ISOS,
    SECRETS,
    SWITCHES,
    _flatten_dict,
    _load_config,
    _normalize_boot_target,
    logger,
    mcp,
    request_logger,
)
from handlers import (
    HPE,
    VENDOR_MAP,
    BaseVendorHandler,
    Dell,
    Supermicro,
)
from helpers import (
    _eject_virtual_media,
    _ensure_boot_once_single,
    _find_virtual_cd_path,
    _get_handler,
    _get_vendor_from_api,
    _get_vm_path_and_state,
    _insert_virtual_media,
    _redfish_call,
)

__all__ = [
    # Config
    "CONFIG",
    "HPE",
    "ISOS",
    "SECRETS",
    "SWITCHES",
    "VENDOR_MAP",
    # Handlers
    "BaseVendorHandler",
    "Dell",
    "Supermicro",
    "_eject_virtual_media",
    "_ensure_boot_once_single",
    "_find_virtual_cd_path",
    "_flatten_dict",
    # Helpers
    "_get_handler",
    "_get_vendor_from_api",
    "_get_vm_path_and_state",
    "_insert_virtual_media",
    "_load_config",
    "_normalize_boot_target",
    "_redfish_call",
    "logger",
    # MCP instance
    "mcp",
    "request_logger",
]
