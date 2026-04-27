# Baremetal MCP Server

An MCP (Model Context Protocol) server for managing bare-metal infrastructure. It exposes Redfish BMC operations (Dell iDRAC, HPE iLO, Supermicro) and Junos switch queries as tools for AI assistants, providing inventory collection, power management, virtual media injection, boot control, firmware updates, and network switch CLI access.

Built with [FastMCP](https://github.com/jlowin/fastmcp), it works with any MCP-compatible client including **Claude Code**, **Gemini CLI**, and others.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Running manually](#running-manually)
- [Claude Code](#claude-code)
  - [Option 1: Clone and auto-detect](#option-1-clone-and-auto-detect-simplest)
  - [Option 2: One-liner manual setup](#option-2-one-liner-manual-setup)
  - [Option 3: Install as a plugin from GitHub](#option-3-install-as-a-plugin-from-github)
  - [Managing the MCP server](#managing-the-mcp-server)
  - [Verifying](#verifying)
- [Gemini CLI](#gemini-cli)
  - [Installation](#installation-1)
  - [Verifying](#verifying-1)
- [Configuration](#configuration)
  - [Servers Configuration](#1-servers-configuration-redfish_serversyaml)
  - [Secrets Configuration](#2-secrets-configuration-redfish_secretsyaml)
  - [Environment Variables](#environment-variables)
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

## Installation

### Running manually

```bash
# stdio transport (for MCP clients)
fastmcp run -t stdio main.py

# HTTP transport (for network access)
fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http main.py
```

## Claude Code

There are three ways to set up this MCP server with Claude Code:

### Option 1: Clone and auto-detect (simplest)

Clone the repository and open it with Claude Code. The included `.mcp.json` is detected automatically — you'll be prompted to approve the server on first use.

```bash
git clone https://github.com/sshnaidm/baremetal-mcp.git
cd baremetal-mcp
claude
```

### Option 2: One-liner manual setup

Register the server directly from the cloned repo:

```bash
claude mcp add --transport stdio baremetal-mcp -- fastmcp run -t stdio /path/to/baremetal-mcp/main.py
```

Add environment variables for custom config paths:

```bash
claude mcp add --transport stdio baremetal-mcp \
  --env GLOBAL_CONFIG=/path/to/global_config.yaml \
  --env REDFISH_CONFIG=/path/to/redfish_servers.yaml \
  --env REDFISH_SECRETS=/path/to/redfish_secrets.yaml \
  -- fastmcp run -t stdio /path/to/baremetal-mcp/main.py
```

### Option 3: Install as a plugin from GitHub

```bash
claude plugin marketplace add sshnaidm/baremetal-mcp
claude plugin install baremetal-mcp@baremetal-mcp-marketplace
```

### Managing the MCP server

```bash
claude mcp list                # see configured servers
claude mcp get baremetal-mcp   # inspect configuration
claude mcp remove baremetal-mcp  # remove the server
```

### Verifying

- `claude mcp list` - See configured MCP servers.
- `/mcp` within a session - See active tools and server status.

## Gemini CLI

### Installation

Install directly from the repository:

```bash
gemini extensions install https://github.com/sshnaidm/baremetal-mcp.git
```

During installation, you will be prompted for the paths to your configuration files. You can press `Enter` to skip these and provide them later (see [Configuration](#configuration) below).

### Verifying

- `/extensions list` - See installed extensions.
- `/mcp` - See active tools and server status.

## Configuration

The server uses up to four YAML configuration files. If not specified, it looks for default filenames in the current working directory.

**Important:** If your config files are not in the working directory, export the env vars before launching Claude Code or Gemini CLI, or pass them via `--env` flags (Claude Code) or extension settings (Gemini CLI).

### 1. Servers Configuration (`redfish_servers.yaml`)

Define your hosts, their BMC IPs, and optional metadata like labs or tags.

```yaml
srv-dell-01:
  bmc_ip: "10.10.1.5"
  vendor: "dell"
  lab: "lab-a"
  tags: ["compute", "gpu"]
srv-hpe-02:
  bmc_ip: "10.10.1.6"
  vendor: "hpe"
  lab: "lab-b"
```

### 2. Secrets Configuration (`redfish_secrets.yaml`)

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

### Environment Variables

You can specify custom file paths via environment variables:

```bash
export GLOBAL_CONFIG="/path/to/global_config.yaml"  # Optional: override default settings
export REDFISH_CONFIG="/path/to/my_servers.yaml"
export REDFISH_SECRETS="/path/to/my_secrets.yaml"
export ISOS_FILE="/path/to/isos.yaml"  # Optional: firmware/ISO URL catalog
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

> **Note on caching:** `get_firmware_inventory`, `get_hardware_overview`, and `get_system_info` cache their responses in memory to reduce BMC load. If results look stale after a hardware change, ask the assistant to run `clear_server_cache` for the affected servers. TTL values are configurable in `global_config.yaml`.
