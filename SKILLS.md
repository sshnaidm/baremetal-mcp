# Skills

This document describes the available skills (guided workflows) for the Redfish MCP server.

## Update Dell Firmware

**Location:** `skills/update-dell-firmware/SKILL.md`

**When to use:** When a user asks to update firmware (iDRAC or BIOS) on a Dell server.

**Workflow:**

1. Identify the target Dell server(s) by checking the vendor using `get_vendor`.
2. Find the required firmware URL — either ask the user or look it up with `list_isos` or `dell_list_url` (provide model, target, and version).
3. Call `dell_update_firmware` with the server ID and firmware URL.
4. Set `reboot=true` if an immediate reboot is needed for the update to apply (required for BIOS updates, not for iDRAC).
5. Optionally verify the update job status by querying the Redfish TaskService via `redfish_call`.

**Notes:**

- Only works on Dell iDRAC servers. Other vendors will return an error.
- Uses the DMTF `SimpleUpdate` action with `ImageURI` for remote firmware files.
- Firmware URLs point to Dell Update Package `.EXE` files, typically served from a local HTTP mirror.
- The ISO/firmware catalog is loaded from the `ISOS_FILE` config (see `isos.example.yaml` for format).

## Check Firmware Versions

**When to use:** When a user asks to check, compare, or list firmware versions across servers.

**Choose the right tool:**

- **Just BIOS + BMC firmware versions (iDRAC/iLO)?** → Use `get_system_info`. Only 2 Redfish requests per server. Returns `bios_version` and `firmware_version` (iDRAC/iLO version) alongside model, serial, power state, and health. Works across all vendors and generations.
- **Other components (RAID, NIC, drives, etc.)?** → Use `get_firmware_inventory` with `name_filter`.
- **Full inventory of all components?** → Use `get_firmware_inventory` without `name_filter`.

### Using `get_system_info` (preferred for BIOS + iDRAC/iLO)

1. Identify the target servers — use `list_hosts`, `list_hosts_by_lab`, or `list_hosts_by_tag` to get server IDs.
2. Call `get_system_info(server_ids)` — returns `bios_version` and `firmware_version` (iDRAC/iLO) per server.
3. Present results in a table for easy comparison.

**Examples:**

- "What BIOS and iDRAC/iLO versions?" → `get_system_info(server_ids)`
- "Quick health check" → `get_system_info(server_ids)` (also includes power state, health, model)

### Using `get_firmware_inventory` (for specific components or full inventory)

1. Identify the target servers.
2. Call `get_firmware_inventory` with the server IDs.
   - **Always use `name_filter`** to request only the components you need. This avoids returning 30-40+ entries per server.
   - The filter is case-insensitive substring match.
   - **Dell filter examples:** `["PERC"]` for RAID, `["Ethernet"]` for NICs, `["Lifecycle"]`, `["CPLD"]`, `["Power Supply"]`, `["SSD"]`.
   - **HPE filter examples:** `["Smart Array"]` for RAID, `["Ethernet"]` for NICs, `["iLO"]` for iLO firmware, `["Intelligent Provisioning"]`, `["Power"]`.
   - **Dell iDRAC naming caveat:** The iDRAC firmware is named `"Integrated Dell Remote Access Controller"`, so use `"Remote Access"` not `"iDRAC"`. The string `"iDRAC"` only matches the iDRAC Service Module package.
3. Present results in a table grouped by model or firmware version for easy comparison.

**Examples:**

- "Check Dell RAID firmware" → `get_firmware_inventory(server_ids, name_filter=["PERC"])`
- "Check HPE RAID firmware" → `get_firmware_inventory(server_ids, name_filter=["Smart Array"])`
- "List NIC firmware" → `get_firmware_inventory(server_ids, name_filter=["Ethernet"])`
- "List all firmware" → `get_firmware_inventory(server_ids)` (no filter)

**Notes:**

- `get_system_info` uses 2 Redfish requests per server. `get_firmware_inventory` uses 1 request for Dell (via `$expand`) and N+1 concurrent requests for HPE (which doesn't support `$expand`).
- If a BMC returns 503 Service Unavailable, it may need a reset (Dell: `racadm racreset`, HPE: iLO reset via web UI) or AC power cycle.
- Component naming varies across vendors and generations. Dell iDRAC 10 (PowerEdge R7x25+) uses `"BMC"` instead of `"Remote Access"`, `"RAID.Slot"` instead of `"PERC"`, `"NIC.Slot"` instead of `"Ethernet"`. HPE uses names like `"iLO 5"`, `"System ROM"`, `"Smart Array"`. Use `get_system_info` for BIOS + BMC firmware versions to avoid vendor/generation-specific filter issues.
- Both `get_system_info` and `get_firmware_inventory` cache their results in memory (see `cache.py` for TTL values). If results look unexpected or don't reflect a recent change, call `clear_server_cache(server_ids)` to force a fresh fetch.

## Query Junos Switches

**Location:** `skills/junos-switch/SKILL.md`

**When to use:** When a user asks to query a Juniper (Junos) switch — configuration, interfaces, MAC table, VLANs, or any CLI command.

**Workflow:**

1. Identify the target switch by its `switch_id` using `list_switches`.
2. Use `junos_run_command(switch_id, command)` with the appropriate Junos CLI command.

**Common commands:**

| Need | Command |
| ------ | --------- |
| Running config | `show configuration` |
| MAC table | `show ethernet-switching table` |
| Interface status | `show interfaces terse` |
| Interface config | `show configuration interfaces` |
| VLANs | `show vlans` |
| LLDP neighbors | `show lldp neighbors` |

**Notes:**

- Connects via SSH with password auth. Credentials in `redfish_secrets.yaml`.
- Paging is automatically disabled before each command.
- Any valid Junos operational-mode command can be passed.
