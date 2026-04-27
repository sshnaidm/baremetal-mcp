# Update Dell Firmware

<name>update-dell-firmware</name>

<description>
Guides the process of updating firmware on Dell servers using the Redfish MCP. Use this skill when the user asks to update firmware on a Dell BMC/iDRAC.
</description>

<instructions>
1. Identify the target Dell server(s) by checking the vendor using `get_vendor`.
2. Find the required firmware URL, either by asking the user or using the `list_isos` or `dell_list_url` tools.
3. Use the `dell_update_firmware` tool with the target server ID and the firmware URL.
4. Set the `reboot` parameter to `true` if an immediate reboot is required for the update to apply, otherwise leave it as `false`.
5. Verify the update job status if needed, by querying the Redfish TaskService using `redfish_call`.
6. Note: `dell_update_firmware` automatically clears the firmware inventory cache for the server on success, so subsequent `get_firmware_inventory` calls will fetch fresh data. If results still look stale, call `clear_server_cache(server_ids)` explicitly.
</instructions>