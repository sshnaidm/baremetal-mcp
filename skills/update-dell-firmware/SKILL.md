---
name: update-dell-firmware
description: Update firmware on Dell servers through the baremetal Redfish MCP when the user requests an iDRAC, BIOS, or other Dell firmware update.
---

# Update Dell Firmware

1. Identify the target Dell server(s) by checking the vendor using `get_vendor`.
2. Find the required firmware URL, either by asking the user or using the `list_isos` or `dell_list_url` tools.
3. Before submitting the update, verify the target, firmware URL, and whether the user authorized an immediate reboot. Do not infer reboot permission.
4. Use `dell_update_firmware` with the target server ID and firmware URL. Set `reboot` to `true` only when an immediate reboot was explicitly requested; otherwise use `false`.
5. If job verification is needed, query the Redfish TaskService using `redfish_call` and report the actual state rather than treating submission as completion.
6. `dell_update_firmware` clears the server's firmware inventory cache after successful submission. If later inventory still appears stale, use `clear_server_cache(server_ids)` before checking again.
