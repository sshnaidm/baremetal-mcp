#!/usr/bin/env python3
"""
MCP Redfish Server - Entry point.

Run with:
    fastmcp run -t stdio ./main.py
    fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http ./main.py
"""

from config import mcp, _load_config, logger

# Import resources to register them
import resources  # noqa: F401

# Import all tools to register them with the MCP server
import tools  # noqa: F401


def main():
    """Start the MCP Redfish server."""
    logger.info("Starting Simplified Redfish MCP Server")
    logger.info(
        "Available tools: redfish_call, parallel_redfish_call, get_power_state, "
        "set_power_state, get_firmware_inventory, get_system_info, ensure_boot_once, "
        "get_vendor, inject_media, eject_media, boot_from_iso, dell_export_hardware_inventory, "
        "dell_update_firmware, list_isos, dell_list_url, junos_run_command, list_switches"
    )
    _load_config()
    mcp.run()


if __name__ == "__main__":
    main()
