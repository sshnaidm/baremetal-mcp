---
name: export-hpe-hardware-inventory
description: Export detailed HPE iLO hardware inventory JSON for one or more hosts, including DIMM and PCI slot occupancy, NIC ports, storage, and optional firmware, with identity-checked snapshots and a checksum manifest.
---

# Export HPE Hardware Inventory

Use this workflow for detailed, saved HPE hardware snapshots. For a quick system summary or firmware-only check, prefer `get_system_info` or filtered `get_firmware_inventory`.

## Select targets and export

Discover the configured baremetal-mcp tools first. Resolve the exact target set with `list_hosts`, `list_hosts_by_lab`, `list_hosts_by_tag`, or `get_hosts`, and identify HPE hosts. Do not silently drop non-HPE targets; report them as unsupported by this exporter.

Call `export_hpe_hardware_inventory` with the resolved `server_ids` in a batch. It accepts up to 64 input IDs and deduplicates them; split larger fleets into bounded batches with separate collections so each manifest covers its batch. Use default concurrency unless a lower BMC load is needed; an explicit `concurrency` must be between 1 and 12. Set `include_firmware=true` only when the requested snapshot needs firmware components; the default is false.

Use a named `collection` when requested or when a separate snapshot is needed to preserve an existing collection. Successful exports replace the corresponding host files and manifest in the selected collection; failed or partial host collections do not replace previous complete host snapshots. Do not archive or replace an existing inventory dataset with an incomplete batch.

Example arguments for a named collection (substitute the resolved host IDs and desired collection):

```json
{
  "server_ids": ["cnfdr19", "cnfdr20"],
  "collection": "hpe-hardware-snapshot",
  "concurrency": 2,
  "include_firmware": false
}
```

The MCP tool uses configured BMC credentials and only Redfish `GET` requests. Snapshots and `manifest.json` are written below `HARDWARE_INVENTORY_DIR`, or the `hardware_inventory_dir` setting. Do not read or expose credentials, reimplement the collector in a skill-local script, or change BMC configuration to obtain an export.

## Interpret coverage

The tool owns one-level collection expansion (`?$expand=.`), individual-member fallback with bounded concurrency, HPE identity validation, atomic JSON writes, and SHA-256 checksums. It checks the live system serial against a configured serial/service tag when present and checks chassis/system serial agreement. A required-resource failure produces a partial or error result without saving that host's new snapshot.

Saved documents contain identity, request statistics, summaries, warnings, and raw Redfish resources for the system, chassis, processors, memory, PCI slots, Ethernet interfaces, network adapters/ports/functions, PCIe devices/functions, storage/drives/volumes, power, thermal, BIOS, and chassis devices. Firmware resources are included only when requested.

Interpret the summaries conservatively:

- A DIMM socket is explicitly empty (`occupied: false`) only when Redfish reports `Status.State: Absent`.
- A PCI slot is explicitly empty (`occupancy: empty`) only when HPE reports `Status.OperationalStatus: Empty`. Chassis PCIe capability data and system HPE PCI slot occupancy are separate resources.
- Unknown occupancy and null NIC link states remain unknown. Array positions do not establish physical port numbers.
- A collection of populated drives does not establish coverage of empty drive bays; coverage depends on the iLO resources exposed by the host.

For Gen10 hosts with empty standard Storage or NetworkAdapters collections, the tool follows advertised HPE SmartStorage and BaseNetworkAdapters resources. These retain controller, physical/logical drive, enclosure, and embedded physical-port records. A failed standard Storage read becomes a warning only after all advertised SmartStorage resources are successfully collected. Review warnings even on successful exports.

## Verify and report

Review aggregate counts and every per-host result. Distinguish saved snapshots, unsupported vendors, identity conflicts, partial collections, and errors. Files left over from a previous complete export are not evidence that a failed host succeeded in the current run.

Report `directory`, `manifest_path`, requested/succeeded/failed counts, and each successful host's `file_path`, identity, SHA-256 checksum, summary counts, and relevant warnings. Include per-host errors and missing coverage for unsuccessful targets. If manifest writing fails after some host files were saved, report those files and the manifest failure separately; do not claim a complete export.
