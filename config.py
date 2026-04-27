#!/usr/bin/env python3
"""
Configuration, globals, and constants for MCP Redfish Server.
"""

import logging
import os
import yaml
from logging.handlers import RotatingFileHandler
from typing import Dict

from fastmcp import FastMCP

# Suppress InsecureRequestWarning for unverified HTTPS requests
from urllib3.exceptions import InsecureRequestWarning
import urllib3

urllib3.disable_warnings(InsecureRequestWarning)

# Configure main logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configure request logger
request_logger = logging.getLogger("request_logger")
request_logger.setLevel(logging.INFO)
if not any(isinstance(h, RotatingFileHandler) for h in request_logger.handlers):
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_requests.log")
    handler = RotatingFileHandler(log_path, maxBytes=1000000, backupCount=5)
    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)
    request_logger.addHandler(handler)

# Initialize FastMCP server
mcp = FastMCP(name="baremetal-mcp")

# --- Configuration and Globals ---

CONFIG: Dict = {}
SWITCHES: Dict = {}
SECRETS: Dict = {}
ISOS: Dict = {}
SETTINGS: Dict = {}

# Defaults — overridden by global_config.yaml if present
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 3
BACKOFF_FACTOR = 0.5
TTL_FIRMWARE_INVENTORY = 7200
TTL_HARDWARE_OVERVIEW = 14400
TTL_SYSTEM_INFO = 1800
TTL_DISK_CACHE = 86400
SSH_TIMEOUT = 15
SSH_COMMAND_TIMEOUT = 60

CONFIG_FILE = os.getenv("REDFISH_CONFIG", "redfish_servers.yaml")
SECRETS_FILE = os.getenv("REDFISH_SECRETS", "redfish_secrets.yaml")
ISOS_FILE = os.getenv("ISOS_FILE", "isos.yaml")
SETTINGS_FILE = os.getenv("GLOBAL_CONFIG", "global_config.yaml")

# Cache for discovered virtual media paths per server
VIRTUAL_MEDIA_PATH_CACHE: Dict[str, str] = {}

# Common boot target aliases mapping to Redfish enums
BOOT_TARGET_ALIASES: Dict[str, str] = {
    "pxe": "Pxe",
    "network": "Pxe",
    "net": "Pxe",
    "cd": "Cd",
    "dvd": "Cd",
    "cdrom": "Cd",
    "iso": "Cd",
    "hdd": "Hdd",
    "disk": "Hdd",
    "localdisk": "Hdd",
    "usb": "Usb",
}


def _normalize_boot_target(target: str):
    """Normalize boot target string to Redfish enum."""
    if not target:
        return None
    t = target.strip().lower()
    return BOOT_TARGET_ALIASES.get(t) or (target if target[0].isupper() else None)


def _load_config():
    """Load server and secret configurations if not already loaded.

    Loads each mapping independently and tolerates a missing secrets file.
    """
    global DEFAULT_TIMEOUT, MAX_RETRIES, BACKOFF_FACTOR
    global TTL_FIRMWARE_INVENTORY, TTL_HARDWARE_OVERVIEW, TTL_SYSTEM_INFO, TTL_DISK_CACHE
    global SSH_TIMEOUT, SSH_COMMAND_TIMEOUT

    config_file = CONFIG_FILE
    secrets_file = SECRETS_FILE
    isos_file = ISOS_FILE
    settings_file = SETTINGS_FILE

    # Load SETTINGS if empty — overrides module-level defaults
    if not SETTINGS:
        if os.path.exists(settings_file):
            with open(settings_file, "r") as f:
                raw_settings = yaml.safe_load(f) or {}
                if isinstance(raw_settings, dict):
                    SETTINGS.update(raw_settings)
                    DEFAULT_TIMEOUT = SETTINGS.get("default_timeout", DEFAULT_TIMEOUT)
                    MAX_RETRIES = SETTINGS.get("max_retries", MAX_RETRIES)
                    BACKOFF_FACTOR = SETTINGS.get("backoff_factor", BACKOFF_FACTOR)
                    TTL_FIRMWARE_INVENTORY = SETTINGS.get("cache_ttl_firmware_inventory", TTL_FIRMWARE_INVENTORY)
                    TTL_HARDWARE_OVERVIEW = SETTINGS.get("cache_ttl_hardware_overview", TTL_HARDWARE_OVERVIEW)
                    TTL_SYSTEM_INFO = SETTINGS.get("cache_ttl_system_info", TTL_SYSTEM_INFO)
                    TTL_DISK_CACHE = SETTINGS.get("cache_ttl_disk_cache", TTL_DISK_CACHE)
                    SSH_TIMEOUT = SETTINGS.get("ssh_timeout", SSH_TIMEOUT)
                    SSH_COMMAND_TIMEOUT = SETTINGS.get("ssh_command_timeout", SSH_COMMAND_TIMEOUT)

    # Load CONFIG if empty
    if not CONFIG:
        if not os.path.exists(config_file):
            logger.warning(f"Configuration file not found: {config_file}; continuing with empty config")
        else:
            with open(config_file, "r") as f:
                raw_config = yaml.safe_load(f) or {}
                servers_section = raw_config.get("servers", raw_config)
                if isinstance(servers_section, dict):
                    CONFIG.update(servers_section)
                else:
                    logger.warning("Invalid format in %s: expected a mapping for servers", config_file)
                switches_section = raw_config.get("switches", {})
                if isinstance(switches_section, dict):
                    SWITCHES.update(switches_section)

    # Load SECRETS if empty
    if not SECRETS:
        if not os.path.exists(secrets_file):
            logger.warning("Secrets file not found: %s; continuing with empty secrets", secrets_file)
        else:
            with open(secrets_file, "r") as f:
                raw_secrets = yaml.safe_load(f) or {}
                if isinstance(raw_secrets, dict):
                    SECRETS.update(raw_secrets)
                else:
                    logger.warning("Invalid format in %s: expected a mapping for secrets", secrets_file)

    if not ISOS:
        if not os.path.exists(isos_file):
            logger.warning("ISOs file not found: %s; continuing without it", isos_file)
        else:
            with open(isos_file, "r") as f:
                raw_isos = yaml.safe_load(f) or {}
                if isinstance(raw_isos, dict):
                    ISOS.update(raw_isos)
                else:
                    logger.warning("Invalid format in %s: expected a mapping for isos", isos_file)


def _flatten_dict(data, prefix: str = "") -> Dict[str, str]:
    """Recursively flatten a nested dict/list structure into key-value pairs.

    Keys are formed by joining nested keys with underscores.
    Stops when a leaf value (string URL) is reached.
    """
    result = {}

    if isinstance(data, dict):
        for key, value in data.items():
            new_prefix = f"{prefix}_{key}" if prefix else str(key)
            result.update(_flatten_dict(value, new_prefix))
    elif isinstance(data, list):
        for item in data:
            result.update(_flatten_dict(item, prefix))
    elif isinstance(data, str):
        # Leaf value - this is the URL
        if prefix:
            result[prefix] = data

    return result
