#!/usr/bin/env python3
"""
Junos switch tools - query Juniper switches via SSH.
"""

import asyncio
import re
import time
from typing import Dict, List

import paramiko

import config as cfg
from config import mcp, CONFIG, SECRETS, _load_config


def _get_command_output(channel, command: str, prompt: str, timeout: int = None) -> str:
    """Send a command and return cleaned output, waiting for the prompt."""
    channel.send(command + "\n")

    if timeout is None:
        timeout = cfg.SSH_COMMAND_TIMEOUT
    output = ""
    deadline = time.time() + timeout
    while not output.strip().endswith(prompt):
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for prompt after command: {command}")
        if channel.recv_ready():
            output += channel.recv(65535).decode("utf-8", errors="replace")
        time.sleep(0.2)

    output = re.sub(r"^" + re.escape(command) + r"\s*\r?\n", "", output, count=1)
    output = output.rstrip()
    if output.endswith(prompt):
        output = output[: -len(prompt)].rstrip()
    return output


def _junos_ssh_commands_sync(switch_id: str, commands: List[str]) -> Dict:
    """Connect to a Junos switch via SSH and run commands. Blocking."""
    _load_config()

    switch_cfg = CONFIG.get(switch_id)
    if not switch_cfg:
        return {"switch_id": switch_id, "status": "error", "message": f"Unknown switch: {switch_id}"}

    creds = SECRETS.get(switch_id, {})
    host = switch_cfg.get("bmc_ip") or switch_cfg.get("address")
    username = creds.get("username")
    password = creds.get("password")
    port = int(switch_cfg.get("port", 22))

    if not host:
        return {"switch_id": switch_id, "status": "error", "message": "No address configured"}
    if not username or not password:
        return {"switch_id": switch_id, "status": "error", "message": "Missing credentials in secrets"}

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            username=username,
            password=password,
            port=port,
            look_for_keys=False,
            allow_agent=False,
            timeout=cfg.SSH_TIMEOUT,
        )

        channel = client.invoke_shell()

        initial_buffer = ""
        deadline = time.time() + 10
        while not initial_buffer.strip().endswith((">", "#")):
            if time.time() > deadline:
                return {"switch_id": switch_id, "status": "error", "message": "Timed out waiting for prompt"}
            if channel.recv_ready():
                initial_buffer += channel.recv(4096).decode("utf-8", errors="replace")
            time.sleep(0.1)

        prompt_match = re.search(r"([\w\-\.@]+[>#])\s*$", initial_buffer.strip())
        if not prompt_match:
            return {"switch_id": switch_id, "status": "error", "message": "Could not determine device prompt"}

        prompt = prompt_match.group(1)

        _get_command_output(channel, "set cli screen-length 0", prompt)

        results = {}
        for cmd in commands:
            results[cmd] = _get_command_output(channel, cmd, prompt)

        return {"switch_id": switch_id, "status": "success", "data": results}

    except paramiko.AuthenticationException:
        return {"switch_id": switch_id, "status": "error", "message": "Authentication failed"}
    except Exception as e:
        return {"switch_id": switch_id, "status": "error", "message": str(e)}
    finally:
        transport = client.get_transport()
        if transport and transport.is_active():
            client.close()


async def _junos_ssh_commands(switch_id: str, commands: List[str]) -> Dict:
    """Async wrapper around blocking SSH commands."""
    return await asyncio.to_thread(_junos_ssh_commands_sync, switch_id, commands)


@mcp.tool(description="Run a CLI command on a Junos switch via SSH.")
async def junos_run_command(switch_id: str, command: str) -> Dict:
    """Run any Junos CLI command and return its output.

    The command runs in operational mode. Paging is automatically disabled.
    """
    result = await _junos_ssh_commands(switch_id, [command])
    if result["status"] == "success":
        result["data"] = result["data"][command]
    return result
