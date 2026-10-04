"""One authoritative contract for the tools exposed by the MCP server."""

from fastmcp import Client

import config
import tools  # noqa: F401 - import-time tool registration is the contract under test

EXPECTED_TOOLS = {
    "boot_from_iso",
    "capture_console_screen",
    "clear_server_cache",
    "collect_network_inventory",
    "console_pager_action",
    "dell_export_hardware_inventory",
    "dell_list_url",
    "dell_switch_apply_commands",
    "dell_switch_run_command",
    "dell_update_firmware",
    "eject_media",
    "ensure_boot_once",
    "export_hardware_inventory_xml",
    "export_hpe_hardware_inventory",
    "export_network_inventory",
    "get_console_session_status",
    "get_firmware_inventory",
    "get_hardware_overview",
    "get_host",
    "get_hosts",
    "get_network_hardware",
    "get_network_inventory",
    "get_operation",
    "get_power_state",
    "get_system_info",
    "get_vendor",
    "inject_media",
    "junos_run_command",
    "list_hosts",
    "list_hosts_by_lab",
    "list_hosts_by_tag",
    "list_isos",
    "list_network_inventories",
    "list_switches",
    "parallel_redfish_call",
    "redfish_call",
    "retry_console_operation_failures",
    "run_console_command",
    "run_console_command_batch",
    "save_network_inventories",
    "save_network_inventory",
    "search_network_inventory",
    "set_power_state",
    "start_console_command_batch",
    "start_hardware_inventory_export",
    "start_network_inventory_collection",
    "validate_host_configuration",
    "validate_network_inventories",
}


async def test_registered_tool_contract_is_complete_and_current():
    async with Client(config.mcp) as client:
        registered = {tool.name for tool in await client.list_tools()}
    assert registered == EXPECTED_TOOLS
