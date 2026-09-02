---
name: boot-image-network-inventory
description: Boot an exact ISO through Redfish, watch the BMC console until its shell is ready, and collect a validated, searchable network inventory.
---

# Boot an Image and Record Network Inventory

Use this workflow for the exact hosts and image URL requested by the user.

## Boundaries

Mounting media, changing one-time boot state, and restarting a host are state-changing operations. The request must authorize those actions for every target. Do not infer permission for a forced reset, a second reboot, media ejection, network configuration, DHCP, package installation, or service changes.

Read credentials only from MCP configuration. Never put them in a console command, saved inventory, log, or response.

## Workflow

1. Resolve and confirm each `server_id`. Record the initial power state and capture the current console before changing boot state.
2. Call `boot_from_iso` with the exact image URL and an explicit authorized reset type. Prefer a graceful restart for a running host and `On` for a powered-off host; use a forced reset only when explicitly authorized.
3. Require confirmation that the requested media is mounted and the one-time CD boot override is set. On failure, report the failed stage and do not reset repeatedly.
4. Watch `capture_console_screen` for meaningful boot transitions. Confirm the requested live image and a focused shell rather than relying on stale scrollback. Use a 20-minute readiness limit unless the user requests another limit.
5. Call `collect_network_inventory` for the ready hosts with `transport="auto"` and `save=true`. The MCP tool owns the bounded serial probes, output markers, parsing, identity checks, and atomic save.
6. If a host returns `vnc_fallback_required=true`, call `capture_console_screen` and inspect its image. After visibly confirming the shell, pass that capture's one-use `input_confirmation_token` as `confirmation_token` to `run_console_command` for one short read-only observation, then use `console_pager_action` to inspect and close its pages. Obtain and inspect a fresh capture and token before every additional command. If pager tracking expires, inspect a new post-expiry capture and pass its token to `console_pager_action`; `get_console_session_status` alone never authorizes input. Normalize only legible facts and save them with `save_network_inventory` only after stable identity evidence verifies the logical host; do not fabricate structured data from an unreadable screen.
7. Verify each saved snapshot with `get_network_inventory`. Use `search_network_inventory(link_up=true, server_id=...)` and an observed MAC or IP to sanity-check connected interfaces and searchability.

Report boot evidence, collection transport and provenance, saved paths, interface/link/address totals, connected MACs, IPs and routes, and all partial or failed evidence sources. Do not claim a remote switch port unless LLDP or switch evidence identifies it.
