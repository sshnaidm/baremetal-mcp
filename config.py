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
    handler = RotatingFileHandler("log_requests.log", maxBytes=1000000, backupCount=5)
    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)
    request_logger.addHandler(handler)

# Initialize FastMCP server
mcp = FastMCP(name="baremetal-mcp")

# --- Configuration and Globals ---

CONFIG: Dict = {}
SECRETS: Dict = {}
ISOS: Dict = {}
DEFAULT_TIMEOUT = 60

CONFIG_FILE = os.getenv("REDFISH_CONFIG", "redfish_servers.yaml")
SECRETS_FILE = os.getenv("REDFISH_SECRETS", "redfish_secrets.yaml")
ISOS_FILE = os.getenv("ISOS_FILE", "isos.yaml")

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
    config_file = CONFIG_FILE
    secrets_file = SECRETS_FILE
    isos_file = ISOS_FILE

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
