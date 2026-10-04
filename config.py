"""
Configuration, globals, and constants for MCP Redfish Server.
"""

import copy
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

import urllib3
import yaml
from fastmcp import FastMCP

# Suppress InsecureRequestWarning for unverified HTTPS requests
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(InsecureRequestWarning)

# Configure main logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configure request logger
request_logger = logging.getLogger("request_logger")
request_logger.setLevel(logging.INFO)
DEFAULT_REQUEST_LOG_PATH = "/tmp/baremetal-mcp-requests.log"
REQUEST_LOG_PATH = os.getenv("REQUEST_LOG_PATH", DEFAULT_REQUEST_LOG_PATH)


def _create_request_log_handler(log_path: str) -> logging.Handler:
    """Create the rotating request log handler, falling back to stderr."""
    try:
        return RotatingFileHandler(log_path, maxBytes=1000000, backupCount=5)
    except OSError as exc:
        logger.warning("Unable to open request log %s; falling back to stderr: %s", log_path, exc)
        return logging.StreamHandler()


if not any(isinstance(h, RotatingFileHandler) for h in request_logger.handlers):
    handler = _create_request_log_handler(REQUEST_LOG_PATH)
    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)
    request_logger.addHandler(handler)

# Initialize FastMCP server
mcp = FastMCP(name="baremetal-mcp")

# --- Configuration and Globals ---

CONFIG: dict = {}
SWITCHES: dict = {}
SECRETS: dict = {}
ISOS: dict = {}
SETTINGS: dict = {}

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
VNC_CAPTURE_TIMEOUT = 30
NETWORK_INVENTORY_DIR = os.getenv("NETWORK_INVENTORY_DIR", "network_inventory")
SOL_CONNECT_TIMEOUT = 10
CONSOLE_COMMAND_TIMEOUT = 60
CONSOLE_BATCH_CONCURRENCY = 6
CONSOLE_OUTPUT_LIMIT = 65536
CONSOLE_SESSION_TTL = 300
OPERATION_TTL = 3600
NETWORK_COLLECTION_CONCURRENCY = 6
BATCH_CONCURRENCY = 6
HARDWARE_INVENTORY_DIR = os.getenv("HARDWARE_INVENTORY_DIR", "data/hardware_inventory")
HARDWARE_INVENTORY_TIMEOUT = 300
HARDWARE_INVENTORY_POLL_INTERVAL = 2.0
HARDWARE_INVENTORY_MAX_BYTES = 50 * 1024 * 1024

CONFIG_FILE = os.getenv("REDFISH_CONFIG", "redfish_servers.yaml")
SECRETS_FILE = os.getenv("REDFISH_SECRETS", "redfish_secrets.yaml")
ISOS_FILE = os.getenv("ISOS_FILE", "isos.yaml")
SETTINGS_FILE = os.getenv("GLOBAL_CONFIG", "global_config.yaml")

# Cache for discovered virtual media paths per server
VIRTUAL_MEDIA_PATH_CACHE: dict[str, str] = {}

# Common boot target aliases mapping to Redfish enums
BOOT_TARGET_ALIASES: dict[str, str] = {
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


def _bounded_setting(
    name: str,
    current: float,
    *,
    integer: bool = False,
    minimum: float,
    maximum: float,
) -> int | float:
    """Read one numeric setting, retaining a safe default when invalid."""
    value = SETTINGS.get(name, current)
    try:
        if isinstance(value, bool):
            raise ValueError
        parsed = int(value) if integer else float(value)
        if not minimum <= parsed <= maximum:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring invalid %s=%r; expected %s between %s and %s",
            name,
            value,
            "an integer" if integer else "a number",
            minimum,
            maximum,
        )
        return current


def _normalize_boot_target(target: str) -> str | None:
    """Normalize boot target string to Redfish enum."""
    if not target:
        return None
    t = target.strip().lower()
    return BOOT_TARGET_ALIASES.get(t) or (target if target[0].isupper() else None)


def _merge_connection_defaults(defaults: object, entry: object) -> object:
    """Deep-merge YAML connection defaults without sharing nested objects."""
    if not isinstance(defaults, dict):
        defaults = {}
    if not isinstance(entry, dict):
        return copy.deepcopy(entry)
    merged = copy.deepcopy(defaults)
    for key, value in entry.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_connection_defaults(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_config() -> None:
    """Load server and secret configurations if not already loaded.

    Loads each mapping independently and tolerates a missing secrets file.
    """
    global DEFAULT_TIMEOUT, MAX_RETRIES, BACKOFF_FACTOR
    global TTL_FIRMWARE_INVENTORY, TTL_HARDWARE_OVERVIEW, TTL_SYSTEM_INFO, TTL_DISK_CACHE
    global SSH_TIMEOUT, SSH_COMMAND_TIMEOUT, VNC_CAPTURE_TIMEOUT
    global SOL_CONNECT_TIMEOUT, CONSOLE_COMMAND_TIMEOUT, CONSOLE_BATCH_CONCURRENCY
    global CONSOLE_OUTPUT_LIMIT, CONSOLE_SESSION_TTL, NETWORK_COLLECTION_CONCURRENCY
    global BATCH_CONCURRENCY, OPERATION_TTL
    global HARDWARE_INVENTORY_DIR, HARDWARE_INVENTORY_TIMEOUT, HARDWARE_INVENTORY_POLL_INTERVAL
    global HARDWARE_INVENTORY_MAX_BYTES

    config_file = CONFIG_FILE
    secrets_file = SECRETS_FILE
    isos_file = ISOS_FILE
    settings_file = SETTINGS_FILE

    # Load SETTINGS if empty — overrides module-level defaults
    if not SETTINGS and os.path.exists(settings_file):
        with open(settings_file, "r") as f:
            raw_settings = yaml.safe_load(f) or {}
            if isinstance(raw_settings, dict):
                SETTINGS.update(raw_settings)
                DEFAULT_TIMEOUT = _bounded_setting("default_timeout", DEFAULT_TIMEOUT, minimum=1, maximum=3600)
                MAX_RETRIES = _bounded_setting("max_retries", MAX_RETRIES, integer=True, minimum=1, maximum=10)
                BACKOFF_FACTOR = _bounded_setting("backoff_factor", BACKOFF_FACTOR, minimum=0, maximum=60)
                TTL_FIRMWARE_INVENTORY = _bounded_setting(
                    "cache_ttl_firmware_inventory",
                    TTL_FIRMWARE_INVENTORY,
                    integer=True,
                    minimum=0,
                    maximum=31_536_000,
                )
                TTL_HARDWARE_OVERVIEW = _bounded_setting(
                    "cache_ttl_hardware_overview",
                    TTL_HARDWARE_OVERVIEW,
                    integer=True,
                    minimum=0,
                    maximum=31_536_000,
                )
                TTL_SYSTEM_INFO = _bounded_setting(
                    "cache_ttl_system_info",
                    TTL_SYSTEM_INFO,
                    integer=True,
                    minimum=0,
                    maximum=31_536_000,
                )
                TTL_DISK_CACHE = _bounded_setting(
                    "cache_ttl_disk_cache",
                    TTL_DISK_CACHE,
                    integer=True,
                    minimum=0,
                    maximum=31_536_000,
                )
                SSH_TIMEOUT = _bounded_setting("ssh_timeout", SSH_TIMEOUT, minimum=1, maximum=600)
                SSH_COMMAND_TIMEOUT = _bounded_setting(
                    "ssh_command_timeout", SSH_COMMAND_TIMEOUT, minimum=1, maximum=3600
                )
                VNC_CAPTURE_TIMEOUT = _bounded_setting(
                    "vnc_capture_timeout", VNC_CAPTURE_TIMEOUT, minimum=1, maximum=300
                )
                SOL_CONNECT_TIMEOUT = _bounded_setting(
                    "sol_connect_timeout", SOL_CONNECT_TIMEOUT, minimum=1, maximum=300
                )
                CONSOLE_COMMAND_TIMEOUT = _bounded_setting(
                    "console_command_timeout", CONSOLE_COMMAND_TIMEOUT, minimum=1, maximum=3600
                )
                CONSOLE_BATCH_CONCURRENCY = _bounded_setting(
                    "console_batch_concurrency",
                    CONSOLE_BATCH_CONCURRENCY,
                    integer=True,
                    minimum=1,
                    maximum=12,
                )
                CONSOLE_OUTPUT_LIMIT = _bounded_setting(
                    "console_output_limit",
                    CONSOLE_OUTPUT_LIMIT,
                    integer=True,
                    minimum=1024,
                    maximum=1_048_576,
                )
                CONSOLE_SESSION_TTL = _bounded_setting(
                    "console_session_ttl",
                    CONSOLE_SESSION_TTL,
                    integer=True,
                    minimum=10,
                    maximum=86_400,
                )
                OPERATION_TTL = _bounded_setting(
                    "operation_ttl",
                    OPERATION_TTL,
                    integer=True,
                    minimum=60,
                    maximum=86_400,
                )
                NETWORK_COLLECTION_CONCURRENCY = _bounded_setting(
                    "network_collection_concurrency",
                    NETWORK_COLLECTION_CONCURRENCY,
                    integer=True,
                    minimum=1,
                    maximum=12,
                )
                BATCH_CONCURRENCY = _bounded_setting(
                    "batch_concurrency",
                    BATCH_CONCURRENCY,
                    integer=True,
                    minimum=1,
                    maximum=12,
                )
                HARDWARE_INVENTORY_DIR = os.getenv("HARDWARE_INVENTORY_DIR") or SETTINGS.get(
                    "hardware_inventory_dir", HARDWARE_INVENTORY_DIR
                )
                HARDWARE_INVENTORY_TIMEOUT = _bounded_setting(
                    "hardware_inventory_timeout",
                    HARDWARE_INVENTORY_TIMEOUT,
                    minimum=1,
                    maximum=3600,
                )
                HARDWARE_INVENTORY_POLL_INTERVAL = _bounded_setting(
                    "hardware_inventory_poll_interval",
                    HARDWARE_INVENTORY_POLL_INTERVAL,
                    minimum=0.1,
                    maximum=30,
                )
                HARDWARE_INVENTORY_MAX_BYTES = _bounded_setting(
                    "hardware_inventory_max_bytes",
                    HARDWARE_INVENTORY_MAX_BYTES,
                    integer=True,
                    minimum=1024,
                    maximum=268_435_456,
                )

    # Load CONFIG if empty
    if not CONFIG:
        if not os.path.exists(config_file):
            logger.warning(f"Configuration file not found: {config_file}; continuing with empty config")
        else:
            with open(config_file, "r") as f:
                raw_config = yaml.safe_load(f) or {}
                server_defaults = raw_config.get("server_defaults", {})
                switch_defaults = raw_config.get("switch_defaults", {})
                if not isinstance(server_defaults, dict):
                    logger.warning("Invalid server_defaults: expected a mapping; ignoring it")
                    server_defaults = {}
                if not isinstance(switch_defaults, dict):
                    logger.warning("Invalid switch_defaults: expected a mapping; ignoring it")
                    switch_defaults = {}

                if "servers" in raw_config:
                    servers_section = raw_config.get("servers")
                else:
                    reserved = {"labs", "server_defaults", "switch_defaults", "switches"}
                    servers_section = {key: value for key, value in raw_config.items() if key not in reserved}
                if isinstance(servers_section, dict):
                    CONFIG.update(
                        {
                            server_id: _merge_connection_defaults(server_defaults, server)
                            for server_id, server in servers_section.items()
                        }
                    )
                    for server in CONFIG.values():
                        if isinstance(server, dict) and server.get("vendor"):
                            server["vendor"] = normalize_vendor(server["vendor"])
                else:
                    logger.warning("Invalid format in %s: expected a mapping for servers", config_file)
                switches_section = raw_config.get("switches", {})
                if isinstance(switches_section, dict):
                    SWITCHES.update(
                        {
                            switch_id: _merge_connection_defaults(switch_defaults, switch)
                            for switch_id, switch in switches_section.items()
                        }
                    )

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


def normalize_vendor(value: object) -> str:
    """Return the canonical vendor name used by handlers and console transports."""
    normalized = str(value or "").strip().lower()
    aliases = {
        "hp": "hpe",
        "hewlett packard enterprise": "hpe",
        "idrac": "dell",
        "ilo": "hpe",
        "super micro": "supermicro",
    }
    return aliases.get(normalized, normalized)


def get_server_credentials(server_id: str) -> dict[str, Any] | None:
    """Resolve credentials without exposing them in a tool result.

    A host may reference ``credential_profile`` in its non-secret configuration.
    Profiles live below ``profiles`` in the secrets file; a per-host secrets entry
    takes precedence and can override individual profile fields.
    """
    _load_config()
    server = CONFIG.get(server_id)
    if not isinstance(server, dict):
        return None
    resolved: dict[str, Any] = {}
    profile_name = server.get("credential_profile")
    profiles = SECRETS.get("profiles", {})
    if profile_name and isinstance(profiles, dict):
        profile = profiles.get(str(profile_name))
        if isinstance(profile, dict):
            resolved.update(profile)
    direct = SECRETS.get(server_id)
    if isinstance(direct, dict):
        resolved.update(direct)
    return resolved or None


def _flatten_dict(data: object, prefix: str = "") -> dict[str, str]:
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
    elif isinstance(data, str) and prefix:
        # Leaf value - this is the URL
        result[prefix] = data

    return result
