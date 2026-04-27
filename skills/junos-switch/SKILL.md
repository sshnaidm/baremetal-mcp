# Query Junos Switches

<name>junos-switch</name>

<description>
Guides querying Juniper (Junos) switches via SSH. Use this skill when the user asks to check switch configuration, interfaces, MAC tables, or run any CLI command on a Junos switch.
</description>

<instructions>
1. Identify the target switch by its `switch_id`. Switches are defined under the `switches:` section in `redfish_servers.yaml`. Use `list_switches` to find available switch IDs.

2. All queries use a single tool: `junos_run_command(switch_id, command)`. It connects via SSH, disables paging automatically, runs the command in operational mode, and returns the output.

3. Use the exact calls below for common queries:

   | Need | Tool call |
   |------|-----------|
   | Full running config | `junos_run_command(switch_id, "show configuration")` |
   | MAC address table | `junos_run_command(switch_id, "show ethernet-switching table")` |
   | Interface operational status | `junos_run_command(switch_id, "show interfaces terse")` |
   | Interface configuration | `junos_run_command(switch_id, "show configuration interfaces")` |
   | VLAN summary | `junos_run_command(switch_id, "show vlans")` |
   | LLDP neighbors | `junos_run_command(switch_id, "show lldp neighbors")` |
   | ARP table | `junos_run_command(switch_id, "show arp")` |
   | Routing table | `junos_run_command(switch_id, "show route")` |
   | Hardware inventory | `junos_run_command(switch_id, "show chassis hardware")` |
   | Active alarms | `junos_run_command(switch_id, "show system alarms")` |
   | MAC table for a port | `junos_run_command(switch_id, "show ethernet-switching table interface ge-0/0/5")` |
   | Detailed interface stats | `junos_run_command(switch_id, "show interfaces ge-0/0/5 extensive")` |
   | LACP/LAG status | `junos_run_command(switch_id, "show lacp interfaces")` |
   | Spanning tree | `junos_run_command(switch_id, "show spanning-tree bridge")` |

**Notes:**
- Connects via SSH with password authentication. Credentials come from `redfish_secrets.yaml`.
- Each call opens a fresh SSH session (no persistent connections).
- Switch entries are under the `switches:` key in the config file. `hostname` (management IP) is the only required field. `vendor`, `model`, and `tags` are optional. Credentials in `redfish_secrets.yaml`, optional `port` (default 22).
- Any valid Junos operational-mode command can be passed — the table above is just a reference for common ones.
</instructions>
