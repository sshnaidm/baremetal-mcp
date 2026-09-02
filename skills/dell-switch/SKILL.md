---
name: dell-switch
description: Query Dell SmartFabric OS10 switches and run confirmed generic CLI command sequences through the baremetal MCP.
---

# Query Dell OS10 Switches

1. Identify the target switch by its `switch_id`. Switches are defined under the `switches:` section in `redfish_servers.yaml`. Use `list_switches` to find available switch IDs.

2. Use `dell_switch_run_command(switch_id, command)`. It connects via SSH, disables paging for that session, runs the command in EXEC mode, and returns the output.

3. The tool is read-only and accepts exactly one single-line Dell OS10 `show` command. It rejects configuration commands and embedded newlines.

4. Use `dell_switch_apply_commands` for any CLI sequence that is not a single read-only `show` command. It accepts `switch_ids` and an ordered `commands` list. Commands are unrestricted: configuration, reload, firmware, delete, and startup-configuration save commands are all passed to OS10. Each command must be one list item without embedded newlines.

5. Always call `dell_switch_apply_commands` with its default `dry_run=true` first. Review the switch IDs and every command, then call it with `dry_run=false` and `confirmation` exactly equal to the returned `confirmation_required` value. The confirmation is bound to the complete plan; any switch, command, order, or `stop_on_error` change requires a new dry run.

6. Each switch gets one SSH session and the switches run in parallel. Commands run exactly in the supplied order. The tool stops after an OS10 CLI error by default and returns ordered command output. It does not enter configuration mode or save configuration unless those commands are explicitly present.

For example, a breakout and explicit startup save can use this ordered command list:

```yaml
switch_ids:
  - cnfdr-sw02
commands:
  - configure terminal
  - interface breakout 1/1/3 map 25g-4x
  - interface ethernet 1/1/3:1
  - switchport mode access
  - switchport access vlan 307
  - no shutdown
  - end
  - copy running-configuration startup-configuration
```

7. Use the exact calls below for common queries:

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
- Never place passwords or other secrets in a command list because MCP arguments and command results may be logged.
- Do not use the unrestricted tool for configuration unless the user explicitly authorizes the change. Preserve any user constraint such as running-config-only by omitting save commands from the reviewed plan.
- Switch entries use `hostname` and require an SSH `port` directly or through `switch_defaults`; `vendor`, `model`, and `tags` are optional. Credentials belong in `redfish_secrets.yaml`.
- The implementation targets Dell SmartFabric OS10. Other Dell switch operating systems have different CLI behavior and are not currently supported.
