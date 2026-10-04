---
name: collect-network-inventory
description: Collect, refresh, validate, and search structured host network inventory from already running systems through SOL or guarded VNC fallback.
---

# Collect Network Inventory

Resolve the requested hosts with `list_hosts`, `list_hosts_by_lab`, or `list_hosts_by_tag`. Compare the target set with `list_network_inventories` when the request concerns missing or stale coverage.

Call `collect_network_inventory(server_ids, transport="auto", save=true)`. For a large fleet, call `start_network_inventory_collection` and poll `get_operation` instead. The tools own short marker-delimited SOL/VSP probes, concurrency, parsing, identity validation, and atomic persistence; do not recreate these mechanics in a temporary script.

Review the aggregate `requested`, `collected`, `saved`, and `failed` counts and every per-host result. Only `identity.status=verified` serial collections are persisted; mismatched and unverified results remain unsaved. If a result says `vnc_fallback_required=true`, the tool did not fabricate or OCR structured data. Before each VNC command, call `capture_console_screen`, inspect that new image, visibly confirm a focused shell, and pass its one-use `input_confirmation_token` as `confirmation_token` to `run_console_command`. Use `console_pager_action` for every page. If tracking expires, inspect a fresh post-expiry capture and pass its token to `console_pager_action`; status lookup alone does not authorize input. Obtain and inspect another capture before the next command. Normalize only legible facts and call `save_network_inventory` with VNC provenance only after stable serial or expected-MAC evidence independently verifies the logical host.

Collection is read-only on the host. Do not request DHCP, bring links up, restart services, install utilities, or change routes merely to fill a field.

When neither serial nor a visually confirmed VNC shell is available, use `get_network_hardware` for read-only Redfish adapter, port, function, and firmware-visible address evidence. Keep that evidence separate from Linux interface names and carrier state; do not save it as an OS-observed snapshot.

If a serial collection is retry-safe, use configured VNC as its fallback. For an obscured or scrolling console, follow the `bmc-console` recovery flow immediately: send only the shell no-op `:` plus Enter, close its pager, capture again, then run the normal collection command without waiting for logs to stop or a prompt to remain visible. Do not infer structured observations from the screen or treat firmware-visible link state as an OS observation.

## HPE iLO physical NIC slots

For HPE hosts, use the HPE OEM Redfish collection `GET /redfish/v1/Systems/1/BaseNetworkAdapters` after the normal hardware query when a physical NIC slot is needed. Read every member returned by the collection (typically `/redfish/v1/Systems/1/BaseNetworkAdapters/1`, `/2`, and so on). Each member provides:

- `Location`, such as `PCI-E Slot 7` or `OCP 3.0 Slot 10`;
- `StructuredName`, such as `NIC.Slot.7.1` or `OCP.Slot.10.1`;
- `PhysicalPorts[*].MacAddress` and `PhysicalPorts[*].Oem.Hpe.PortNumber`.

Join an OS-observed interface to this evidence only by normalized MAC address. Record `Location` as the NIC slot, `StructuredName` as the firmware slot identifier, and the OEM port number as the physical port. Do not infer a slot from Linux PCI address or adapter ordering when no MAC match is available. `LinkStatus` in this OEM resource is firmware-visible evidence; retain Linux carrier state as the authoritative OS link observation.

For every host, preserve and report:

- collection transport and evidence sources;
- confirmed, partial, unsupported, or failed state;
- `identity_status`, conflicts, and whether an existing snapshot was preserved;
- interfaces, normalized MACs, physical carrier, addresses, routes, vendors, drivers, and known physical ports.

Administrative `UP` does not prove a cable or peer is connected. Prefer carrier or equivalent link evidence. Keep observed and expected-but-unconfirmed metadata distinct. Never merge data across hosts when stable identity or MAC ownership conflicts.

Verify saved results with `get_network_inventory` and `list_network_inventories`. Use `search_network_inventory` for connected interfaces, MACs, IPs/subnets, vendors, PCI addresses, or host IDs requested by the user.
