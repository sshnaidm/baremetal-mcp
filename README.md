# Baremetal MCP Server

An MCP (Model Context Protocol) server for managing bare-metal infrastructure. It exposes Redfish BMC operations (Dell iDRAC, HPE iLO, Supermicro), BMC console capture and command paging, and Junos/Dell OS10 switch queries as tools for AI assistants.

Built with [FastMCP](https://github.com/jlowin/fastmcp), it works with any MCP-compatible client including **Claude Code**, **Gemini CLI**, and others.

## Table of Contents

- [Features](#features)
- [Portable Agent Skills](#portable-agent-skills)
- [Requirements](#requirements)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Claude Code](#claude-code)
- [Gemini CLI](#gemini-cli)
- [Cursor](#cursor)
- [Other MCP Clients](#other-mcp-clients)
- [Configuration](#configuration)
- [Usage](#usage)

## Features

- **Inventory:** Detailed hardware overview (CPUs, Memory, NICs, Storage).
- **Power Control:** On, Off, Graceful Shutdown, and Reboots.
- **Boot Management:** Set one-time boot targets (PXE, CD/ISO, HDD, USB).
- **Virtual Media:** Mount and eject ISO images remotely.
- **Dell-Specific:** Firmware updates and detailed XML inventory exports.
- **BMC Console Tools:** Run guarded Dell SOL/HPE VSP commands in batches, or capture and page through a visually confirmed VNC console.
- **Network Inventory:** Collect Linux network facts, query firmware-visible Redfish NIC ports, save per-host YAML, validate identity and duplicates, and export searchable MAC/IP indexes.
- **Junos Switches:** Query Juniper switch configuration, interfaces, MAC tables, and run arbitrary CLI commands via SSH.
- **Dell OS10 Switches:** Run read-only `show` queries or dry-run/confirm unrestricted CLI sequences across switches via SSH.
- **Parallelism:** Perform actions on multiple servers simultaneously.
- **Caching:** Slow inventory calls (`get_firmware_inventory`, `get_hardware_overview`, `get_system_info`) are cached in memory with TTLs to avoid redundant BMC requests.

## Portable Agent Skills

Model-neutral workflows are available under [`skills/`](skills/) using the standard [Agent Skills](https://agentskills.io/specification) `SKILL.md` format. They use YAML frontmatter plus Markdown instructions and do not require model-specific metadata. See [`SKILLS.md`](SKILLS.md) for the catalog.

## Requirements

Ensure you have Python installed and install the necessary dependencies:

```bash
pip install -e .
```

## Prerequisites

Before using the MCP server with any AI agent, you **must** configure two mandatory environment variables pointing to your server and credentials files:

```bash
# Required — must be set before running Claude Code or Gemini CLI
export REDFISH_CONFIG="/path/to/redfish_servers.yaml"
export REDFISH_SECRETS="/path/to/redfish_secrets.yaml"
```

```bash
# Optional — only if you need firmware ISOs or custom settings
export ISOS_FILE="/path/to/isos.yaml"
export GLOBAL_CONFIG="/path/to/global_config.yaml"
export NETWORK_INVENTORY_DIR="/path/to/network_inventory"
export HARDWARE_INVENTORY_DIR="/path/to/hardware_inventory"
```

Add these to your `~/.bashrc` or `~/.zshrc` to make them permanent. See [Configuration](#configuration) for file format details.

## Installation

### Running manually

```bash
# stdio transport (for MCP clients)
fastmcp run -t stdio main.py

# HTTP transport (for network access)
fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http main.py
```

## Claude Code

### Install as a plugin from GitHub (recommended)

Make sure the [prerequisite env vars](#prerequisites) are exported in your shell, then:

```bash
claude plugin marketplace add sshnaidm/baremetal-mcp
claude plugin install baremetal-mcp@baremetal-mcp-marketplace
```

Verify it works:

```bash
claude mcp list                  # see configured servers
```

Or within a Claude Code session, run `/mcp` to see active tools and server status.

### Alternative: clone and auto-detect

Clone the repository and open it with Claude Code. The included `.mcp.json` is detected automatically — you'll be prompted to approve the server on first use.

```bash
git clone https://github.com/sshnaidm/baremetal-mcp.git
cd baremetal-mcp
claude
```

### Alternative: manual setup with env vars

Register the server directly and pass config paths as env vars:

```bash
claude mcp add --transport stdio baremetal-mcp \
  --env REDFISH_CONFIG=/path/to/redfish_servers.yaml \
  --env REDFISH_SECRETS=/path/to/redfish_secrets.yaml \
  -- fastmcp run -t stdio /path/to/baremetal-mcp/main.py
```

## Gemini CLI

Make sure the [prerequisite env vars](#prerequisites) are exported in your shell, then install directly from the repository:

```bash
gemini extensions install https://github.com/sshnaidm/baremetal-mcp.git
```

Verify it works:

- `/extensions list` - See installed extensions.
- `/mcp` - See active tools and server status.

## Cursor

Add the server to your Cursor MCP configuration file (`.cursor/mcp.json` in your project or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "baremetal-mcp": {
      "command": "fastmcp",
      "args": ["run", "-t", "stdio", "/path/to/baremetal-mcp/main.py"],
      "env": {
        "REDFISH_CONFIG": "/path/to/redfish_servers.yaml",
        "REDFISH_SECRETS": "/path/to/redfish_secrets.yaml"
      }
    }
  }
}
```

## Other MCP Clients

Any MCP-compatible client can use this server via stdio transport. Add it to your client's MCP configuration:

```json
{
  "mcpServers": {
    "baremetal-mcp": {
      "command": "fastmcp",
      "args": ["run", "-t", "stdio", "/path/to/baremetal-mcp/main.py"],
      "env": {
        "REDFISH_CONFIG": "/path/to/redfish_servers.yaml",
        "REDFISH_SECRETS": "/path/to/redfish_secrets.yaml"
      }
    }
  }
}
```

Optionally add `GLOBAL_CONFIG` and `ISOS_FILE` env vars if you need custom settings or firmware ISOs.

For HTTP transport instead of stdio:

```json
{
  "mcpServers": {
    "baremetal-mcp": {
      "url": "http://127.0.0.1:5004/mcp"
    }
  }
}
```

Start the server separately with: `fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http main.py`

## Configuration

The server uses up to four YAML configuration files controlled by environment variables.

| Env var | Default filename | Required | Description |
| --------- | ----------------- | ---------- | ------------- |
| `REDFISH_CONFIG` | `redfish_servers.yaml` | **Yes** | Server/switch definitions (BMC IPs, vendor, tags) |
| `REDFISH_SECRETS` | `redfish_secrets.yaml` | **Yes** | Per-server credentials (username/password) |
| `ISOS_FILE` | `isos.yaml` | No | Firmware/ISO URL catalog |
| `GLOBAL_CONFIG` | `global_config.yaml` | No | Settings overrides (timeouts, retries, cache TTLs) |
| `NETWORK_INVENTORY_DIR` | `network_inventory` | No | Per-host YAML network snapshots; overrides the global setting |
| `HARDWARE_INVENTORY_DIR` | `data/hardware_inventory` | No | Validated Dell XML files and manifests; overrides the global setting |

### Servers Configuration (`redfish_servers.yaml`)

Each entry requires `bmc_ip` (the BMC management address). Connection ports are never inferred by the MCP server: define shared values in `server_defaults` and `switch_defaults`, then override them per target when necessary. `vendor` remains optional and is auto-detected through the configured Redfish endpoint.

```yaml
server_defaults:
  redfish:
    port: 443
  serial_console:
    transport: auto
    port: 22
  verify_ssl: false

switch_defaults:
  port: 22

servers:
  srv-dell-01:
    bmc_ip: "10.10.1.5"          # required
    vendor: "dell"                # optional, auto-detected if omitted
    lab: "lab-a"
    tags: ["compute", "gpu"]
    serial_number: "ABC123"       # optional identity guard
    expected_host_macs:
      - "aa:bb:cc:dd:ee:01"
    credential_profile: "dell-lab"
    vnc:                           # optional; all connection values explicit
      port: 5901
      timeout: 30
      key_delay: 0.01
  srv-hpe-02:
    bmc_ip: "10.10.1.6"
    vendor: "hpe"
    lab: "lab-b"
```

### Switches Configuration

Switches are defined under a separate `switches:` section in the same file. Each needs a `hostname` and an SSH `port`, either directly or through `switch_defaults`.

```yaml
switches:
  lab1-switch:
    hostname: "192.168.1.200"
    vendor: "Juniper"          # optional
    model: "Juniper QFX5120"   # optional
    tags: ["switch", "lab1"]   # optional
  lab1-dell-switch:
    hostname: "192.168.1.201"
    vendor: "Dell"
    model: "S5232F-ON"
    tags: ["switch", "lab1"]
```

### Secrets Configuration (`redfish_secrets.yaml`)

Define the credentials for each server ID.

```yaml
profiles:
  dell-lab:
    username: "example-dell-user"
    password: "example-dell-password"
    vnc_password: "example-vnc-password"
srv-dell-01:
  # Per-host values override a referenced profile.
  password: "example-override-password"
srv-hpe-02:
  username: "example-hpe-user"
  password: "example-hpe-password"
```

See `*.example.yaml` files for complete format examples.

`boot_from_iso` validates HTTP(S) image URLs, bounds fleet concurrency, verifies that the exact image is inserted, and confirms `Cd`/`Once` before it sends a reset. If verification fails, it does not reboot. A powered-off verified host is started with `On` instead of an invalid restart request. Results retain each accepted, rejected, or ambiguous media/boot mutation, so a late failure cannot make a partially changed host look safe to retry; an accepted reset request is never described as a verified power transition.

`export_hardware_inventory_xml` replaces external Dell export wrappers. It handles case-insensitive `Location` headers, polls task monitors with deadlines and `Retry-After`, rejects cross-BMC redirects or malformed/oversized/non-inventory XML, and requires the XML service tag to match the live Redfish identity before using either a fresh download or cache. It writes XML atomically and creates a checksum manifest. It returns metadata by default; use `include_xml=true` only when the raw payload is needed. For large exports, use `start_hardware_inventory_export` and `get_operation`.

Use `redfish_call` and `parallel_redfish_call` only when no high-level tool covers a standard Redfish resource. They accept same-BMC origin-relative paths only. GET/HEAD/OPTIONS run normally; POST/PUT/PATCH/DELETE make no request by default and require `dry_run=false` plus `confirm_method_path` exactly matching the returned `required_confirmation`. Mutating requests are never automatically replayed after an ambiguous failure, and missing credentials fail closed instead of falling back to vendor defaults.

### BMC Console Commands

`run_console_command_batch` is the normal text interface. It attaches with Dell iDRAC SSH SOL (`console com2`) or HPE iLO VSP, proves that a shell interprets a random nonce, sends one single-line command, and captures its output plus exit code. It defaults to `dry_run=true`, which checks configuration without connecting. Execution requires both `dry_run=false` and `confirm_command` exactly equal to `command`.

Every host result reports `command_sent`, `result_confirmed`, `retry_safe`, transport, phase, exit code, bounded output, and truncation. Never repeat a `command_sent=true` result whose completion is unknown. `retry_console_operation_failures` selects only hosts proven not to have received the previous command. Use `start_console_command_batch` and poll `get_operation` for fleet commands that could exceed an MCP client timeout. Completed operation records aggregate `remote_state`, `outcome_unknown`, and `retry_safe` across their host results. Operation records live only in the MCP server process; after a restart, completion is unknown and mutating work must not be blindly retried.

VNC remains a visual fallback. `capture_console_screen` sends no input and returns PNG `ImageContent` plus a short-lived `input_confirmation_token`. Before every command, inspect a fresh token-producing image and confirm a focused shell, then pass that one-use token as `confirmation_token` to `run_console_command`. The command is displayed through a restricted `less` pager; navigate it with `console_pager_action`. If pager tracking expires, `get_console_session_status` reports remote state as unknown: make and inspect a new post-expiry capture, then pass its token to `console_pager_action` for one recovery, quit, interrupt, or visually justified abandon action. A VNC send or capture failure can leave partial text or an unknown command outcome, which is returned explicitly rather than retried.

VNC commands need POSIX `sh` and `less`. Console text is visible and may enter shell history, so never include passwords or tokens. Dell iDRAC VNC SSL encryption requires an external tunnel; the tools do not weaken BMC security settings.

### Persistent Network Inventory

Call `collect_network_inventory(server_ids, transport="auto", save=true)` to run short read-only Linux probes over SOL/VSP. It captures `ip`, sysfs carrier and physical-port data, `ethtool`, PCI vendor/model details, DMI identity, routes, addresses, and optional LLDP neighbors. It compares OS identity with Redfish and configured serial/MAC evidence before replacing a snapshot. Only verified identity is persisted; mismatched and unverified collections are returned as evidence but remain unsaved. For large sets, use `start_network_inventory_collection` and poll `get_operation`.

If serial collection cannot prove a shell, the result says whether visually guarded VNC fallback is available. The tool does not treat OCR as authoritative data. `get_network_hardware` is a separate read-only Redfish fallback for firmware-visible Ethernet interfaces, adapters, physical ports, and device functions; keep its evidence distinct from Linux interface names and carrier state.

The latest validated snapshots live under `network_inventory/hosts/<server_id>.yaml`. `save_network_inventory` and `save_network_inventories` support explicitly supplied structured observations, reject secrets and older timestamps by default, and write atomically.

Use these MCP tools for later retrieval:

- `get_network_inventory(server_id)` returns one complete host document.
- `list_network_inventories()` returns per-host counts of interfaces, active links, and addresses.
- `search_network_inventory(...)` combines paginated filters for `mac`, interface-name substring, `link_up`, exact IP or subnet, vendor substring, exact PCI address, and server ID.
- `validate_network_inventories(...)` reports missing, stale, malformed, duplicate-MAC, and host-identity problems.
- `export_network_inventory(...)` atomically produces one YAML or JSON file with connected-interface, MAC, IP, and separate BMC-MAC indexes.

Examples:

```text
search_network_inventory(mac="b4:96:91:c8:aa:28")
search_network_inventory(link_up=true)
search_network_inventory(ip="fe80::/64", vendor="Intel")
search_network_inventory(interface="ens1", server_id="bmc18-live")
validate_network_inventories(server_ids=["srv-dell-01", "srv-hpe-02"])
export_network_inventory(format="yaml", collection="rack-14")
```

The YAML remains directly searchable without MCP:

```bash
rg -l 'mac_address: b4:96:91:c8:aa:28' network_inventory/hosts
```

## Usage

Once the MCP server is running, your AI assistant will discover the Redfish tools automatically. You can ask it to perform tasks naturally:

- "List all servers in lab-a"
- "What is the power state of srv-dell-01?"
- "Get a hardware inventory for all servers with the 'gpu' tag"
- "Mount the Ubuntu ISO to srv-hpe-02 and boot from it once"
- "Update the firmware on srv-dell-01 using this URL: http://..."
- "Show me the MAC address table on lab1-switch"
- "Run 'show lldp neighbors' on the Junos switch"
- "Run 'show interface status' on the Dell OS10 switch"
- "Dry-run these Dell OS10 configuration commands on both switches, then run the unchanged confirmed plan"
- "Capture the console screen for srv-dell-01 and explain what is happening"
- "Run `journalctl -b` on the console, inspect every page, and tell me why boot failed"
- "Dry-run `systemctl restart NetworkManager` for these hosts over SOL, then execute it only on the confirmed targets"
- "Collect and save network data for every host tagged rack-14, then export its connected-MAC index"
- "Save this host's network inventory, then show every connected Intel interface across all saved hosts"

> **Note on caching:** `get_firmware_inventory`, `get_hardware_overview`, and `get_system_info` cache their responses in memory to reduce BMC load. If results look stale after a hardware change, ask the assistant to run `clear_server_cache` for the affected servers. TTL values are configurable in `global_config.yaml`.
