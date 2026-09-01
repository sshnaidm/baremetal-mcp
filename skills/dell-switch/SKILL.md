---
name: dell-switch
description: Query Dell SmartFabric OS10 switches through the baremetal MCP when inspecting configuration, interfaces, VLANs, forwarding tables, neighbors, or operational state.
---

# Query Dell OS10 Switches

1. Identify the target switch by its `switch_id`. Switches are defined under the `switches:` section in `redfish_servers.yaml`. Use `list_switches` to find available switch IDs.

2. Use `dell_switch_run_command(switch_id, command)`. It connects via SSH, disables paging for that session, runs the command in EXEC mode, and returns the output.

3. The tool is read-only and accepts exactly one single-line Dell OS10 `show` command. It rejects configuration commands and embedded newlines.

4. Use the exact calls below for common queries:

   | Need | Tool call |
   |------|-----------|
   | OS and hardware version | `dell_switch_run_command(switch_id, "show version")` |
   | Running configuration | `dell_switch_run_command(switch_id, "show running-configuration")` |
   | Interface status | `dell_switch_run_command(switch_id, "show interface status")` |
   | Interface details | `dell_switch_run_command(switch_id, "show interface ethernet 1/1/1")` |
   | VLAN summary | `dell_switch_run_command(switch_id, "show vlan")` |
   | MAC address table | `dell_switch_run_command(switch_id, "show mac address-table")` |
   | LLDP neighbors | `dell_switch_run_command(switch_id, "show lldp neighbors")` |
   | ARP table | `dell_switch_run_command(switch_id, "show ip arp")` |
   | Routing table | `dell_switch_run_command(switch_id, "show ip route")` |
   | LACP/LAG status | `dell_switch_run_command(switch_id, "show port-channel summary")` |
   | Spanning tree | `dell_switch_run_command(switch_id, "show spanning-tree")` |

**Notes:**

- Credentials come from `redfish_secrets.yaml`; do not put them in the switch configuration.
- Each call opens a fresh SSH session.
- Switch entries use `hostname`; `vendor`, `model`, `tags`, and `port` are optional.
- The implementation targets Dell SmartFabric OS10. Other Dell switch operating systems have different CLI behavior and are not currently supported.
