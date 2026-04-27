# AGENTS.md

This file provides guidance to AI coding assistants working with this repository.

## What This Is

An MCP (Model Context Protocol) server that exposes Redfish BMC operations (Dell iDRAC, HPE iLO, Supermicro) and Junos switch queries as tools for AI assistants. Built with FastMCP, it provides inventory, power management, virtual media, boot control, Dell firmware updates, and Junos switch CLI access.

## Running the Server

```bash
# Install dependencies
pip install fastmcp httpx PyYAML urllib3 paramiko

# Run via stdio (for Claude Code / Gemini CLI / MCP clients)
fastmcp run -t stdio main.py

# Run as HTTP server
fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http main.py
```

No test suite exists currently. Validation is done by running the server against real or stubbed BMC endpoints.

## Configuration

Four YAML files control behavior (paths set via env vars or defaults):

- `GLOBAL_CONFIG` → `global_config.yaml` — server settings (timeouts, retries, cache TTLs). Ships with defaults in the repo; override via env var to customize.
- `REDFISH_CONFIG` → `redfish_servers.yaml` — server/switch definitions (`bmc_ip`, vendor, lab, tags)
- `REDFISH_SECRETS` → `redfish_secrets.yaml` — per-server/switch credentials (username/password)
- `ISOS_FILE` → `isos.yaml` — firmware/ISO URL catalog (Dell firmware .EXE URLs keyed by model/target/version)

See `*.example.yaml` files for format. The config file supports a top-level `servers:` key (for Redfish hosts, loaded into `CONFIG`) and an optional `switches:` key (for switches, loaded into `SWITCHES`). Switch entries use `hostname` for the management IP; `vendor`, `model`, and `tags` are all optional.

## Architecture

**Entry point:** `main.py` — loads config, imports tool/resource modules, starts FastMCP server.

**Layer structure (top to bottom):**

1. **`tools/`** — MCP tool functions registered via `@mcp.tool()`. Each module groups related tools:
   - `hosts.py` — host listing/filtering (by lab, tag, id)
   - `server.py` — power state, firmware inventory, system info, hardware overview, boot control, cache management
   - `media.py` — virtual media mount/unmount/boot-from-ISO
   - `dell.py` — Dell-specific: firmware update, hardware inventory XML export, ISO catalog
   - `junos.py` — Junos switch queries via SSH (`junos_run_command`)
   - `redfish.py` — low-level `redfish_call` and `parallel_redfish_call` passthrough
2. **`resources.py`** — MCP resources (`hosts://all`, `hosts://id/{id}`, etc.) for read-only host config access
3. **`helpers.py`** — internal async logic: HTTP client management, vendor handler resolution, Redfish API calls with retry, virtual media path discovery, boot override
4. **`handlers.py`** — vendor-specific handler classes (`Dell`, `HPE`, `Supermicro`) inheriting `BaseVendorHandler`. Each defines Redfish paths (`SYSTEM_PATH`, `MANAGER_PATH`) and auth strategy
5. **`config.py`** — globals (`CONFIG`, `SWITCHES`, `SECRETS`, `ISOS`, `SETTINGS`), YAML loading, FastMCP instance, boot target normalization, logging setup, all configurable constants (timeouts, retries, TTLs)
6. **`cache.py`** — `TTLCache` class and `RESPONSE_CACHE` singleton

**Key patterns:**

- All multi-server tools accept `List[str]` of server_ids and run operations via `asyncio.gather` for parallelism
- Tool return format is always `{"status": "success"|"error", ...}` dicts
- Vendor detection is auto-discovered from Redfish `/redfish/v1` OEM data if not in config, then cached
- Handler instances and virtual media paths are cached in module-level dicts
- `helpers._redfish_call` retries on 5xx errors and connection failures (up to 3 attempts with backoff)
- HTTP client uses `httpx.AsyncClient` with `verify=False` (BMC self-signed certs)
- Tool registration happens at import time via `@mcp.tool()` decorators; `tools/__init__.py` imports all tool modules
- Slow/static responses are cached in memory with TTLs: `get_firmware_inventory`, `get_hardware_overview`, `get_system_info`; `dell_export_hardware_inventory` uses a disk cache. TTL values are configurable in `global_config.yaml` (defaults in `config.py`).

## Core Principles

- **Redfish First**: Always prioritize Redfish API calls for any hardware-related tasks. Vendor-specific CLIs like `racadm` are the absolute last resort.
- **Parallelism by Default**: Most high-level tools support multiple `server_ids` and execute in parallel. Always use these parallel versions when dealing with more than one server.
- **Declarative Operations**: Prefer declarative tools (e.g., `boot_from_iso`, `ensure_boot_once`, `inject_media`) which handle state checks and idempotency internally.
- **Vendor Awareness**: The system supports Dell, HPE, and Supermicro. Some tools are vendor-specific (prefixed with `dell_`). Use `get_vendor` or check host configuration if unsure.

## Discovery and Filtering

Hosts are defined in `redfish_servers.yaml` with metadata such as `lab`, `vendor`, and `tags`.

- `list_hosts`: Get the full mapping of all known servers.
- `get_host(server_id)` / `get_hosts(server_ids)`: Get configuration for specific servers.
- `list_hosts_by_lab(lab)` / `list_hosts_by_tag(tag)`: Filter servers for batch operations.

## Hardware & Firmware Inventory

- `get_system_info`: Quick summary (manufacturer, model, serial, power state, health, BIOS version, BMC firmware version). Only 2 Redfish requests per server — prefer this for BIOS + iDRAC/iLO versions.
- `get_hardware_overview`: Unified view of CPUs, memory, NICs, and storage (drives/volumes). Compatible with Dell and HPE.
- `get_firmware_inventory`: Lists firmware components and versions. Always use `name_filter` to limit results (e.g., `["PERC"]`, `["Ethernet"]`). Without a filter, returns 30-40+ entries per server.
- `dell_export_hardware_inventory`: Dell-only, extremely detailed OEM hardware XML. Use as last resort.

## Power & Boot Management

- `get_power_state`: Check if servers are `On` or `Off`.
- `set_power_state`: Execute reset actions (`On`, `ForceOff`, `GracefulRestart`, `PushPowerButton`).
- `ensure_boot_once`: Set next boot target (`pxe`, `cd`, `hdd`, `usb`) and optionally reboot.

## Virtual Media & ISOs

- `list_isos`: List available ISO images from `isos.yaml`.
- `boot_from_iso`: Mount a remote ISO, set boot to CD (Once), and reboot in a single call.
- `inject_media` / `eject_media`: Idempotent tools to mount/unmount virtual media.

## Dell-Specific Operations

- `dell_update_firmware`: Initiate iDRAC or BIOS update using a remote URL (.EXE DUP).
- `dell_list_url`: Retrieve firmware URLs from ISO configuration by model and version.

## Junos Switch Operations

- `list_switches`: List all switches from the `switches:` section of the configuration.
- `junos_run_command(switch_id, command)`: Run any CLI command on a switch via SSH. Paging is automatically disabled. Switches are defined under the `switches:` key in the config file. `hostname` is used for the management IP; `vendor`, `model`, and `tags` are all optional. Credentials come from `redfish_secrets.yaml`, optional `port` defaults to 22.

## Low-Level Access

- `redfish_call` / `parallel_redfish_call`: Custom Redfish requests not covered by high-level tools. Research standard Redfish paths for the vendor and use these.

## Operational Guidance

- If the built-in tools don't provide needed information, discover the correct Redfish path (search docs for the vendor — Dell, HPE, or Supermicro) and use `parallel_redfish_call`.
- Vendor-specific Redfish paths differ (Dell uses `System.Embedded.1`/`iDRAC.Embedded.1`, HPE/Supermicro use `1`). The handler layer abstracts this — use `_get_handler` to get correct paths.
- Hosts are organized by labs and tags in the config for filtering.
- `get_firmware_inventory`, `get_hardware_overview`, and `get_system_info` return cached results. If results look stale after a hardware change, call `clear_server_cache(server_ids)`. `dell_update_firmware` automatically clears the firmware cache on success.
- To change cache TTL values, timeouts, or retry settings, edit `global_config.yaml`. Defaults are defined in `config.py` and overridden by the YAML file at startup.
- All Redfish requests and payloads are logged to `log_requests.log`.

## Skills

`skills/update-dell-firmware/SKILL.md` — guided workflow for Dell firmware updates using the tool chain: `get_vendor` → `list_isos`/`dell_list_url` → `dell_update_firmware`.
`skills/junos-switch/SKILL.md` — guided workflow for querying Junos switches via `junos_run_command`.
`SKILLS.md` — check firmware versions workflow: when to use `get_system_info` vs `get_firmware_inventory`.
