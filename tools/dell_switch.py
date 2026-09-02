#!/usr/bin/env python3
"""Dell OS10 switch tools - run read-only queries via SSH."""

import asyncio
import re
import time
from typing import Dict, List, Optional

import paramiko

import config as cfg
from config import SECRETS, SWITCHES, _load_config, mcp
from helpers import _configured_port


def _get_command_output(channel, command: str, prompt: str, timeout: Optional[int] = None) -> str:
    """Send a command and return cleaned output after the prompt reappears."""
    channel.send(command + "\n")

    if timeout is None:
        timeout = cfg.SSH_COMMAND_TIMEOUT
    output = ""
    deadline = time.monotonic() + timeout
    while not output.strip().endswith(prompt):
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out waiting for prompt after command: {command}")
        if channel.recv_ready():
            output += channel.recv(65535).decode("utf-8", errors="replace")
        time.sleep(0.2)

    output = re.sub(r"^" + re.escape(command) + r"\s*\r?\n", "", output, count=1)
    output = output.rstrip()
    if output.endswith(prompt):
        output = output[: -len(prompt)].rstrip()
    return output


def _validate_read_only_command(command: str) -> Optional[str]:
    """Return an error for commands outside Dell OS10's read-only show family."""
    if not command or not command.strip():
        return "Command must not be empty"
    if "\n" in command or "\r" in command:
        return "Command must be a single line"
    if not re.match(r"^show(?:\s|$)", command.strip(), flags=re.IGNORECASE):
        return "Only read-only Dell OS10 'show' commands are allowed"
    return None


def _dell_switch_ssh_commands_sync(switch_id: str, commands: List[str]) -> Dict:
    """Connect to a Dell OS10 switch and run read-only commands. Blocking."""
    _load_config()

    switch_cfg = SWITCHES.get(switch_id)
    if not switch_cfg:
        return {"switch_id": switch_id, "status": "error", "message": f"Unknown switch: {switch_id}"}

    for command in commands:
        validation_error = _validate_read_only_command(command)
        if validation_error:
            return {"switch_id": switch_id, "status": "error", "message": validation_error}

    creds = SECRETS.get(switch_id, {})
    host = switch_cfg.get("hostname") or switch_cfg.get("bmc_ip") or switch_cfg.get("address")
    username = creds.get("username")
    password = creds.get("password")

    if not host:
        return {"switch_id": switch_id, "status": "error", "message": "No address configured"}
    if not username or not password:
        return {"switch_id": switch_id, "status": "error", "message": "Missing credentials in secrets"}
    try:
        port = _configured_port(switch_cfg.get("port"), "switch.port")
    except ValueError as exc:
        return {"switch_id": switch_id, "status": "error", "message": str(exc)}

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
        deadline = time.monotonic() + cfg.SSH_TIMEOUT
        while not initial_buffer.strip().endswith((">", "#")):
            if time.monotonic() > deadline:
                return {"switch_id": switch_id, "status": "error", "message": "Timed out waiting for prompt"}
            if channel.recv_ready():
                initial_buffer += channel.recv(4096).decode("utf-8", errors="replace")
            time.sleep(0.1)

        prompt_match = re.search(r"([\w.\-@]+[>#])\s*$", initial_buffer.strip())
        if not prompt_match:
            return {"switch_id": switch_id, "status": "error", "message": "Could not determine device prompt"}

        prompt = prompt_match.group(1)

        # EXEC-mode setting scoped to this SSH session; it does not change switch configuration.
        _get_command_output(channel, "terminal length 0", prompt)

        results = {}
        for command in commands:
            results[command] = _get_command_output(channel, command, prompt)

        return {"switch_id": switch_id, "status": "success", "data": results}

    except paramiko.AuthenticationException:
        return {"switch_id": switch_id, "status": "error", "message": "Authentication failed"}
    except Exception as exc:
        return {"switch_id": switch_id, "status": "error", "message": str(exc)}
    finally:
        client.close()


async def _dell_switch_ssh_commands(switch_id: str, commands: List[str]) -> Dict:
    """Async wrapper around blocking Dell switch SSH commands."""
    return await asyncio.to_thread(_dell_switch_ssh_commands_sync, switch_id, commands)


@mcp.tool(description="Run a read-only show command on a Dell OS10 switch via SSH.")
async def dell_switch_run_command(switch_id: str, command: str) -> Dict:
    """Run one Dell OS10 show command with paging disabled.

    Only a single-line command in the ``show`` command family is accepted.
    """
    result = await _dell_switch_ssh_commands(switch_id, [command])
    if result["status"] == "success":
        result["data"] = result["data"][command]
    return result
