# Baremetal MCP Server

An MCP (Model Context Protocol) server for managing bare-metal infrastructure. It exposes Redfish BMC operations (Dell iDRAC, HPE iLO, Supermicro) and Junos switch queries as tools for AI assistants, providing inventory collection, power management, virtual media injection, boot control, firmware updates, and network switch CLI access.

Built with [FastMCP](https://github.com/jlowin/fastmcp), it works with any MCP-compatible client including **Claude Code**, **Gemini CLI**, and others.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Claude Code](#claude-code)
- [Gemini CLI](#gemini-cli)
- [Configuration](#configuration)
- [Usage](#usage)

## Features

- **Inventory:** Detailed hardware overview (CPUs, Memory, NICs, Storage).
- **Power Control:** On, Off, Graceful Shutdown, and Reboots.
- **Boot Management:** Set one-time boot targets (PXE, CD/ISO, HDD, USB).
- **Virtual Media:** Mount and eject ISO images remotely.
- **Dell-Specific:** Firmware updates and detailed XML inventory exports.
- **Junos Switches:** Query Juniper switch configuration, interfaces, MAC tables, and run arbitrary CLI commands via SSH.
- **Parallelism:** Perform actions on multiple servers simultaneously.
- **Caching:** Slow inventory calls (`get_firmware_inventory`, `get_hardware_overview`, `get_system_info`) are cached in memory with TTLs to avoid redundant BMC requests.

## Requirements

Ensure you have Python installed and install the necessary dependencies:

```bash
pip install fastmcp httpx PyYAML urllib3 paramiko
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

## Configuration

The server uses up to four YAML configuration files controlled by environment variables.

| Env var | Default filename | Required | Description |
| --------- | ----------------- | ---------- | ------------- |
| `REDFISH_CONFIG` | `redfish_servers.yaml` | **Yes** | Server/switch definitions (BMC IPs, vendor, tags) |
| `REDFISH_SECRETS` | `redfish_secrets.yaml` | **Yes** | Per-server credentials (username/password) |
| `ISOS_FILE` | `isos.yaml` | No | Firmware/ISO URL catalog |
| `GLOBAL_CONFIG` | `global_config.yaml` | No | Settings overrides (timeouts, retries, cache TTLs) |

### Servers Configuration (`redfish_servers.yaml`)

Each entry requires `bmc_ip` (the BMC management address). All other fields are optional — `vendor` is auto-detected if omitted.

```yaml
srv-dell-01:
  bmc_ip: "10.10.1.5"         # required
  vendor: "dell"               # optional, auto-detected if omitted
  lab: "lab-a"                 # optional
  tags: ["compute", "gpu"]     # optional
srv-hpe-02:
  bmc_ip: "10.10.1.6"
  vendor: "hpe"
  lab: "lab-b"
```

### Switches Configuration

Switches are defined under a separate `switches:` section in the same file. Only `hostname` is required.

```yaml
switches:
  lab1-switch:
    hostname: "192.168.1.200"
    model: "Juniper QFX5120"   # optional
    tags: ["switch", "lab1"]   # optional
```

### Secrets Configuration (`redfish_secrets.yaml`)

Define the credentials for each server ID.

```yaml
srv-dell-01:
  username: "root"
  password: "calvin-password"
srv-hpe-02:
  username: "admin"
  password: "hp-password"
```

See `*.example.yaml` files for complete format examples.

## Usage

Once the MCP server is running, your AI assistant will discover the Redfish tools automatically. You can ask it to perform tasks naturally:

- "List all servers in lab-a"
- "What is the power state of srv-dell-01?"
- "Get a hardware inventory for all servers with the 'gpu' tag"
- "Mount the Ubuntu ISO to srv-hpe-02 and boot from it once"
- "Update the firmware on srv-dell-01 using this URL: http://..."
- "Show me the MAC address table on lab1-switch"
- "Run 'show lldp neighbors' on the Junos switch"

> **Note on caching:** `get_firmware_inventory`, `get_hardware_overview`, and `get_system_info` cache their responses in memory to reduce BMC load. If results look stale after a hardware change, ask the assistant to run `clear_server_cache` for the affected servers. TTL values are configurable in `global_config.yaml`.
