# AGENTS.md

This file provides guidance to AI coding assistants working with this repository.

## What This Is

An MCP (Model Context Protocol) server that exposes Redfish BMC operations (Dell iDRAC, HPE iLO, Supermicro), BMC console capture and command paging, and Junos/Dell OS10 switch queries as tools for AI assistants.

## Running the Server

```bash
# Install dependencies
pip install -e '.[test]'

# Run via stdio (for Claude Code / Gemini CLI / MCP clients)
fastmcp run -t stdio main.py

# Run as HTTP server
fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http main.py
```

Run `pytest` for the unit and in-process MCP contract suite. Live-device checks are explicit, opt-in, and read-only unless the user separately authorizes a state change.

## Configuration

Four YAML files control behavior (paths set via env vars or defaults):

- `GLOBAL_CONFIG` → `global_config.yaml` — server settings (timeouts, retries, cache TTLs). Ships with defaults in the repo; override via env var to customize.
- `REDFISH_CONFIG` → `redfish_servers.yaml` — server/switch definitions (`bmc_ip`, vendor, lab, tags)
- `REDFISH_SECRETS` → `redfish_secrets.yaml` — per-server/switch credentials (username/password)
- `ISOS_FILE` → `isos.yaml` — firmware/ISO URL catalog (Dell firmware .EXE URLs keyed by model/target/version)

See `*.example.yaml` files for format. The config file supports `server_defaults`, `switch_defaults`, a top-level `servers:` key (for Redfish hosts, loaded into `CONFIG`), and an optional `switches:` key (for switches, loaded into `SWITCHES`). Connection ports are explicit YAML values: Redfish uses `redfish.port`, SOL/VSP uses `serial_console.port`, VNC uses `vnc.port`, and switches use `port`. Per-target mappings override their corresponding defaults. Switch entries use `hostname` for the management IP; `vendor`, `model`, and `tags` are optional.

## Architecture

**Entry point:** `main.py` — loads config, imports tool/resource modules, starts FastMCP server.

**Layer structure (top to bottom):**

1. **`tools/`** — MCP tool functions registered via `@mcp.tool()`. Each module groups related tools:
   - `hosts.py` — host listing/filtering (by lab, tag, id)
   - `server.py` — power state, firmware inventory, system info, hardware overview, boot control, cache management
   - `media.py` — virtual media mount/unmount/boot-from-ISO
   - `dell.py` — Dell-specific: firmware update, hardware inventory XML export, ISO catalog
   - `console.py` — read-only framebuffer capture plus explicitly input-capable command/pager tools returned as MCP image content
   - `vnc_capture_worker.py` — isolated one-shot vncdotool process for capture, command typing, and guarded pager keys
   - `serial_console.py` — guarded Dell SOL/HPE VSP execution, result markers, sent-state tracking, and bounded batches
   - `configuration.py` — sanitized host/capability preflight without exposing secret values
   - `network_collect.py` — short-command Linux network collection, parsing, and identity validation
   - `network_hardware.py` — vendor-neutral read-only Redfish NIC/adapter/port queries
   - `network_inventory.py` — persistent per-host YAML, validation, searches, and aggregate indexes
   - `operations.py` — in-memory background fleet operations and safe console retry selection
   - `dell_switch.py` — read-only Dell OS10 queries plus confirmation-gated unrestricted command sequences via SSH
   - `junos.py` — Junos switch queries via SSH (`junos_run_command`)
   - `redfish.py` — low-level `redfish_call` and `parallel_redfish_call` passthrough
2. **`resources.py`** — MCP resources (`hosts://all`, `hosts://id/{id}`, etc.) for read-only host config access
3. **`helpers.py`** — internal async logic: HTTP client management, vendor handler resolution, Redfish API calls with retry, virtual media path discovery, boot override
4. **`handlers.py`** — vendor-specific handler classes (`Dell`, `HPE`, `Supermicro`) inheriting `BaseVendorHandler`. Each defines Redfish paths (`SYSTEM_PATH`, `MANAGER_PATH`) and auth strategy
5. **`config.py`** — globals (`CONFIG`, `SWITCHES`, `SECRETS`, `ISOS`, `SETTINGS`), YAML loading, FastMCP instance, boot target normalization, logging setup, all configurable constants (timeouts, retries, TTLs)
6. **`cache.py`** — `TTLCache` class and `RESPONSE_CACHE` singleton

**Key patterns:**

- Multi-server tools deduplicate IDs, apply batch-size and concurrency limits, preserve stable result order, and use parallelism where safe.
- Tools return structured `{"status": "success"|"partial"|"error", ...}` data. Console tools additionally return native MCP `ImageContent`.
- Vendor detection is auto-discovered from Redfish `/redfish/v1` OEM data if not in config, then cached
- Handler instances and virtual media paths are cached in module-level dicts
- `helpers._redfish_call` retries only GET/HEAD/OPTIONS on 5xx and connection failures. Mutating methods are attempted once because their outcome can be ambiguous.
- HTTP clients separate verified and unverified TLS pools. Configure the boolean `verify_ssl` explicitly on a host or in `server_defaults`; do not infer a TLS policy in connection code.
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
- `list_switches`: Get all switches from the `switches:` section.
- `get_host(server_id)` / `get_hosts(server_ids)`: Get configuration for specific servers.
- `list_hosts_by_lab(lab)` / `list_hosts_by_tag(tag)`: Filter servers for batch operations.

## Hardware & Firmware Inventory

- `get_system_info`: Quick summary (manufacturer, model, serial, power state, health, BIOS version, BMC firmware version). Only 2 Redfish requests per server — prefer this for BIOS + iDRAC/iLO versions.
- `get_hardware_overview`: Unified view of CPUs, memory, NICs, and storage (drives/volumes). Compatible with Dell, HPE, and Supermicro (includes SimpleStorage and Chassis-based drive fallbacks for older Supermicro BMCs).
- `get_firmware_inventory`: Lists firmware components and versions. Always use `name_filter` to limit results (e.g., `["PERC"]`, `["Ethernet"]`). Without a filter, returns 30-40+ entries per server.
- `export_hardware_inventory_xml`: Dell-only detailed OEM XML with task polling, identity checks, XML validation, atomic files, SHA-256 values, and a manifest. Metadata is returned by default.
- `dell_export_hardware_inventory`: compatibility wrapper that includes raw XML; prefer the metadata-first batch tool.

## Power & Boot Management

- `get_power_state`: Check if servers are `On` or `Off`.
- `set_power_state`: Execute reset actions (`On`, `ForceOff`, `GracefulRestart`, `PushPowerButton`).
- `ensure_boot_once`: Set next boot target (`pxe`, `cd`, `hdd`, `usb`) and optionally reboot.

## Virtual Media & ISOs

- `list_isos`: List available ISO images from `isos.yaml`.
- `boot_from_iso`: Mount a remote ISO, verify the exact media and CD/Once state, then reboot. Verification failure stops before reset.
- `inject_media` / `eject_media`: Idempotent tools to mount/unmount virtual media.

## Dell-Specific Operations

- `dell_update_firmware`: Initiate iDRAC or BIOS update using a remote URL (.EXE DUP).
- `dell_list_url`: Retrieve firmware URLs from ISO configuration by model and version.

## BMC Console Operations

- `run_console_command_batch`: Normal text path over Dell SOL/HPE VSP. It defaults to a no-connection dry run; execution requires `dry_run=false` and `confirm_command == command`. Trust success only with a confirmed exit marker. Never retry `command_sent=true` with an unknown result.
- `start_console_command_batch` / `get_operation`: Background form for fleet commands that may exceed client timeouts. `retry_console_operation_failures` includes only hosts proven not to have received the command.
- Completed background operations conservatively aggregate `remote_state`, `outcome_unknown`, and `retry_safe`; inspect per-host results whenever `remote_state` is `mixed` or `unknown`.
- `capture_console_screen(server_id)`: Capture VNC without input and return a short-lived `input_confirmation_token` with the image.
- `run_console_command(server_id, command, confirmation_token, wait_seconds)`: VNC fallback. Use only after visually inspecting the exact token-producing capture.
- `console_pager_action(server_id, session_id, action, wait_seconds, confirmation_token=None)`: Capture or navigate the active pager with `refresh`, `next_page`, `previous_page`, `first_page`, `last_page`, `quit`, or `interrupt`. If tracking expired, first make and inspect a fresh post-expiry capture, then pass its token to authorize one recovery action. `abandon` clears local tracking without input only after the fresh image visibly proves that no pager is active. Inspect each returned image and quit after `(END)`.
- Configure `vnc.port` and `vnc.key_delay`, plus optional `vnc.timeout`, on the server entry; store the separate password as `vnc_password` in the secrets entry.
- Command text is visible on the host console and may enter shell history. Never place credentials in it. Commands use `/dev/null` for stdin; interactive commands are unsupported. `less` and POSIX `sh` are required on the host.
- Direct VNC does not provide the iDRAC SSL tunnel. If VNC SSL encryption is enabled, require an external tunnel instead of changing the BMC configuration.

## Junos Switch Operations

- `list_switches`: List all switches from the `switches:` section of the configuration.
- `junos_run_command(switch_id, command)`: Run any CLI command on a switch via SSH. Paging is automatically disabled. Switches are defined under the `switches:` key in the config file. `hostname` is used for the management IP; `vendor`, `model`, and `tags` are optional. Credentials come from `redfish_secrets.yaml`; the SSH `port` must be present directly or through `switch_defaults`.

## Persistent Network Inventory

- `collect_network_inventory`: Run short read-only Linux probes over SOL/VSP, compare OS DMI identity with Redfish, configured serials, and expected host MACs, and persist only `identity.status=verified` observations. Both mismatched and unverified collections remain unsaved. It reports guarded VNC fallback instead of fabricating OCR output.
- `get_network_hardware`: Read firmware-visible Ethernet interfaces, adapters, ports, and device functions over Redfish; keep this provenance distinct from OS observations.
- `save_network_inventory`: Validate and save the latest host snapshot under `network_inventory/hosts` as schema-versioned YAML. Never include credentials.
- `save_network_inventories`: Batch save with duplicate-ID and stale-snapshot guards.
- `get_network_inventory`: Return one saved host document.
- `list_network_inventories`: Summarize all saved hosts with interface, active-link, and address counts.
- `search_network_inventory`: Paginated search by normalized MAC, interface-name substring, link state, exact IP or subnet, vendor substring, PCI address, and server ID.
- `validate_network_inventories`: Report coverage, freshness, malformed files, duplicate MAC ownership, connected links without MACs, and configured identity mismatches.
- `export_network_inventory`: Atomically produce one YAML/JSON file with connected, MAC, IP, and separate BMC-MAC indexes.
- Configure the directory with `NETWORK_INVENTORY_DIR` (highest precedence) or `network_inventory_dir` in `global_config.yaml`.

## Dell OS10 Switch Operations

- `dell_switch_run_command(switch_id, command)`: Run a read-only Dell OS10 `show` command via SSH. The tool rejects configuration commands and embedded newlines before connecting. Paging is disabled only for the SSH session with `terminal length 0`.
- `dell_switch_apply_commands(switch_ids, commands, dry_run, confirmation, stop_on_error)`: Run an ordered, unrestricted OS10 CLI sequence on one or more switches. It defaults to dry-run, binds exact confirmation to the complete plan, uses one SSH session per switch, and runs switches in parallel. Configuration and startup-save commands are allowed.

## Low-Level Access

- `redfish_call` / `parallel_redfish_call`: Custom same-BMC Redfish requests not covered by high-level tools. Paths cannot change origin. Reads execute normally; POST/PUT/PATCH/DELETE default to no-request dry runs and require `dry_run=false` plus `confirm_method_path` set to the exact returned `required_confirmation`. Prefer declarative high-level tools.

## Operational Guidance

- If the built-in tools don't provide needed information, discover the correct Redfish path (search docs for the vendor — Dell, HPE, or Supermicro) and use `parallel_redfish_call`.
- Vendor-specific Redfish paths differ (Dell uses `System.Embedded.1`/`iDRAC.Embedded.1`, HPE/Supermicro use `1`). The handler layer abstracts this — use `_get_handler` to get correct paths.
- Hosts are organized by labs and tags in the config for filtering.
- `get_firmware_inventory`, `get_hardware_overview`, and `get_system_info` return cached results. If results look stale after a hardware change, call `clear_server_cache(server_ids)`. `dell_update_firmware` automatically clears the firmware cache on success.
- To change cache TTL values, timeouts, or retry settings, edit `global_config.yaml`. Defaults are defined in `config.py` and overridden by the YAML file at startup.
- All Redfish requests and payloads are logged to `REQUEST_LOG_PATH`, which defaults to
  `/tmp/baremetal-mcp-requests.log`; filesystem errors fall back to stderr.

## Skills

All reusable workflows use the portable Agent Skills `SKILL.md` format with YAML frontmatter and model-neutral instructions. See `SKILLS.md`; do not add client-specific metadata or reimplement MCP behavior in skill-local scripts.
