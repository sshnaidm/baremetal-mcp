#!/usr/bin/env python3
"""
Baremetal MCP Server - bare-metal infrastructure management for AI assistants.

Provides MCP tools and resources for managing servers via Redfish API (Dell, HPE,
Supermicro) and Junos switches via SSH.
"""

from config import (
    mcp,
    CONFIG,
    SECRETS,
    ISOS,
    _load_config,
    _flatten_dict,
    _normalize_boot_target,
    logger,
    request_logger,
)

from handlers import (
    BaseVendorHandler,
    Dell,
    HPE,
    Supermicro,
    VENDOR_MAP,
)

from helpers import (
    _get_handler,
    _get_vendor_from_api,
    _redfish_call,
    _find_virtual_cd_path,
    _get_vm_path_and_state,
    _eject_virtual_media,
    _insert_virtual_media,
    _ensure_boot_once_single,
)

__all__ = [
    # MCP instance
    "mcp",
    # Config
    "CONFIG",
    "SECRETS",
    "ISOS",
    "_load_config",
    "_flatten_dict",
    "_normalize_boot_target",
    "logger",
    "request_logger",
    # Handlers
    "BaseVendorHandler",
    "Dell",
    "HPE",
    "Supermicro",
    "VENDOR_MAP",
    # Helpers
    "_get_handler",
    "_get_vendor_from_api",
    "_redfish_call",
    "_find_virtual_cd_path",
    "_get_vm_path_and_state",
    "_eject_virtual_media",
    "_insert_virtual_media",
    "_ensure_boot_once_single",
]
