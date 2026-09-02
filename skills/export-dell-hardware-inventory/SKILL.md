---
name: export-dell-hardware-inventory
description: Export full Dell iDRAC hardware inventory XML for one or more hosts, save validated files atomically, and report a searchable manifest without overwriting unrelated snapshots.
---

# Export Dell Hardware Inventory XML

Resolve the exact target set with `list_hosts`, `list_hosts_by_lab`, or `list_hosts_by_tag`, and confirm which hosts are Dell. Do not silently drop non-Dell targets.

Call `export_hardware_inventory_xml` once for the resolved `server_ids`; for a large fleet, use `start_hardware_inventory_export` and poll `get_operation`. Use the default collection and cache behavior unless the user explicitly requests a named collection or a fresh export. Set `include_xml=true` only when the raw XML is actually needed in the response; normally use the saved files and summaries.

The MCP tool owns Dell export submission, bounded task polling, response-header normalization, Dell CIM/root validation, live Redfish identity lookup, embedded XML service-tag matching for fresh and cached files, atomic writes, checksums, concurrency, and manifest creation. Do not reproduce that workflow in a temporary script.

Review the aggregate counts and every per-host result. Distinguish successful exports, valid cached files, unsupported vendors, failed jobs, timeouts, and identity conflicts. Do not automatically resubmit an export whose submission outcome is ambiguous.

Report `directory`, `manifest_path`, requested/succeeded/failed/unsupported counts, cache/refresh state, each saved `file_path`, XML root validation, and SHA-256 checksum. Preserve existing collections unless the user explicitly requests `refresh=true` or a documented atomic replacement.
