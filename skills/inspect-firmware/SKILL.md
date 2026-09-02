---
name: inspect-firmware
description: Check or compare BIOS, BMC, RAID, NIC, drive, and other firmware versions across Dell, HPE, or Supermicro hosts using the least expensive Redfish inventory tool.
---

# Inspect Firmware Versions

Resolve all target `server_id` values first and use the multi-host tools for batch work.

- For BIOS and BMC firmware, or a quick model/health comparison, use `get_system_info`.
- For a named component family, use `get_firmware_inventory` with a narrow, case-insensitive `name_filter` such as `PERC`, `Smart Array`, or `Ethernet`.
- Fetch the full firmware inventory only when the user requests all components or when a narrow discovery query cannot identify the vendor's component name.

Component names vary by vendor and generation. A zero-match filter does not prove that hardware is absent; report the filter and broaden it carefully when needed. Supermicro firmware inventory may expose only BIOS and BMC versions.

Present comparable fields together and retain per-host errors. If a recent hardware or firmware change makes cached data suspect, call `clear_server_cache` for only the affected hosts and query again. A failed inventory request does not authorize a BMC reset or power operation.
