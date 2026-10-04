"""Dell OS10 switch tools - run read-only or confirmed CLI commands via SSH."""

import asyncio
import hashlib
import json
import re
import time

import paramiko

import config as cfg
from config import SECRETS, SWITCHES, _load_config, mcp
from helpers import _configured_port


def _get_command_output(channel: paramiko.Channel, command: str, prompt: str, timeout: int | None = None) -> str:
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


def _validate_read_only_command(command: str) -> str | None:
    """Return an error for commands outside Dell OS10's read-only show family."""
    if not command or not command.strip():
        return "Command must not be empty"
    if "\n" in command or "\r" in command:
        return "Command must be a single line"
    if not re.match(r"^show(?:\s|$)", command.strip(), flags=re.IGNORECASE):
        return "Only read-only Dell OS10 'show' commands are allowed"
    return None


def _get_dynamic_command_output(
    channel: paramiko.Channel,
    command: str,
    timeout: int | None = None,
) -> tuple[str, str]:
    """Send one command while allowing the OS10 prompt to change modes."""
    channel.send(command + "\n")
    if timeout is None:
        timeout = cfg.SSH_COMMAND_TIMEOUT
    output = ""
    deadline = time.monotonic() + timeout
    prompt = ""
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out waiting for prompt after command: {command}")
        if channel.recv_ready():
            output += channel.recv(65535).decode("utf-8", errors="replace")
            match = re.search(r"(?:^|\r?\n)([^\r\n]+[>#])\s*$", output)
            if match:
                prompt = match.group(1).strip()
                break
        time.sleep(0.2)

    body = re.sub(r"^" + re.escape(command) + r"\s*\r?\n", "", output, count=1).rstrip()
    if body.endswith(prompt):
        body = body[: -len(prompt)].rstrip()
    return body, prompt


def _validate_command_plan(
    switch_ids: list[str],
    commands: list[str],
    dry_run: bool,
    stop_on_error: bool,
) -> str | None:
    """Validate plan structure without restricting which OS10 commands may run."""
    if not isinstance(switch_ids, list) or not switch_ids:
        return "switch_ids must be a non-empty list"
    if any(not isinstance(switch_id, str) or not switch_id.strip() for switch_id in switch_ids):
        return "each switch_id must be a non-empty string"
    normalized_switch_ids = [switch_id.strip() for switch_id in switch_ids]
    if len(set(normalized_switch_ids)) != len(normalized_switch_ids):
        return "switch_ids must not contain duplicates"
    if not isinstance(commands, list) or not commands:
        return "commands must be a non-empty list"
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            return "each command must be a non-empty string"
        if "\n" in command or "\r" in command:
            return "each command must be a single line; use one list item per CLI command"
    if not isinstance(dry_run, bool):
        return "dry_run must be a boolean"
    if not isinstance(stop_on_error, bool):
        return "stop_on_error must be a boolean"
    return None


def _command_plan(switch_ids: list[str], commands: list[str], stop_on_error: bool) -> dict:
    return {
        "switch_ids": switch_ids,
        "commands": commands,
        "stop_on_error": stop_on_error,
        "session_setup": ["terminal length 0"],
        "commands_are_unrestricted": True,
        "writes_startup_configuration": any(_writes_startup_configuration(command) for command in commands),
    }


def _command_confirmation(plan: dict) -> str:
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"APPLY DELL SWITCH COMMANDS {digest}"


def _writes_startup_configuration(command: str) -> bool:
    """Conservatively identify OS10 commands that may persist switch configuration."""
    for segment in re.split(r"[;&|]", command.casefold()):
        normalized = " ".join(segment.split())
        normalized = normalized.removeprefix("do ")
        parts = normalized.split()
        if not parts:
            continue
        if parts[0] in {"write", "wr", "save"}:
            return True
        if parts[0] in {"copy", "cp"} and len(parts) >= 3:
            destination = parts[-1]
            if destination.startswith("st") or "startup" in destination:
                return True
        if parts[0] in {"delete", "erase"} and any("startup" in part for part in parts[1:]):
            return True
        if "startup-configuration" in normalized or "startup-config" in normalized:
            return True
    return False


def _startup_save_confirmation(plan: dict) -> str:
    """Require a second, plan-bound token for a separately authorized startup write."""
    return _command_confirmation(plan).replace("APPLY DELL SWITCH COMMANDS", "SAVE SWITCH STARTUP CONFIGURATION", 1)


def _has_cli_error(output: str) -> bool:
    return bool(
        re.search(
            r"(?:^|\n)\s*%\s*(?:Error|Invalid|Incomplete|Ambiguous)|unrecognized command",
            output,
            flags=re.IGNORECASE,
        )
    )


def _dell_switch_apply_commands_sync(
    switch_id: str,
    commands: list[str],
    stop_on_error: bool,
    startup_save_authorized: bool = False,
) -> dict:
    """Run a confirmed OS10 sequence, guarding startup writes before connecting."""
    if any(_writes_startup_configuration(command) for command in commands) and not startup_save_authorized:
        return {
            "switch_id": switch_id,
            "status": "error",
            "phase": "startup-save-confirmation",
            "message": "Startup-configuration writes require separate explicit authorization",
        }
    _load_config()
    switch_cfg = SWITCHES.get(switch_id)
    if not switch_cfg:
        return {"switch_id": switch_id, "status": "error", "message": f"Unknown switch: {switch_id}"}
    creds = SECRETS.get(switch_id, {})
    host = switch_cfg.get("hostname") or switch_cfg.get("bmc_ip") or switch_cfg.get("address")
    username = creds.get("username")
    password = creds.get("password")
    if not host:
        return {"switch_id": switch_id, "status": "error", "message": "No address configured"}
    if not username or not password:
        return {"switch_id": switch_id, "status": "error", "message": "Missing credentials in secrets"}
    try:
        ssh_port = _configured_port(switch_cfg.get("port"), "switch.port")
    except ValueError as exc:
        return {"switch_id": switch_id, "status": "error", "message": str(exc)}

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    executed: list[str] = []
    command_results: list[dict[str, str]] = []
    try:
        client.connect(
            hostname=host,
            username=username,
            password=password,
            port=ssh_port,
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
        _get_dynamic_command_output(channel, "terminal length 0")
        for command in commands:
            executed.append(command)
            output, _ = _get_dynamic_command_output(channel, command)
            command_results.append({"command": command, "output": output})
            if stop_on_error and _has_cli_error(output):
                return {
                    "switch_id": switch_id,
                    "status": "error",
                    "phase": "execution",
                    "message": f"Dell OS10 reported an error for command: {command}",
                    "commands_executed": executed,
                    "command_results": command_results,
                }
        return {
            "switch_id": switch_id,
            "status": "success",
            "phase": "complete",
            "commands_executed": executed,
            "command_results": command_results,
        }
    except paramiko.AuthenticationException:
        return {"switch_id": switch_id, "status": "error", "message": "Authentication failed"}
    except Exception as exc:
        return {
            "switch_id": switch_id,
            "status": "error",
            "message": str(exc),
            "commands_executed": executed,
            "command_results": command_results,
        }
    finally:
        client.close()


async def _dell_switch_apply_commands(
    switch_id: str,
    commands: list[str],
    stop_on_error: bool,
    startup_save_authorized: bool = False,
) -> dict:
    return await asyncio.to_thread(
        _dell_switch_apply_commands_sync,
        switch_id,
        commands,
        stop_on_error,
        startup_save_authorized,
    )


def _dell_switch_ssh_commands_sync(switch_id: str, commands: list[str]) -> dict:
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


async def _dell_switch_ssh_commands(switch_id: str, commands: list[str]) -> dict:
    """Async wrapper around blocking Dell switch SSH commands."""
    return await asyncio.to_thread(_dell_switch_ssh_commands_sync, switch_id, commands)


@mcp.tool(description="Run a read-only show command on a Dell OS10 switch via SSH.")
async def dell_switch_run_command(switch_id: str, command: str) -> dict:
    """Run one Dell OS10 show command with paging disabled.

    Only a single-line command in the ``show`` command family is accepted.
    """
    result = await _dell_switch_ssh_commands(switch_id, [command])
    if result["status"] == "success":
        result["data"] = result["data"][command]
    return result


@mcp.tool(
    description=(
        "Run Dell OS10 CLI commands after dry-run confirmation. Startup-configuration writes "
        "also require an explicit user request and separate startup-save confirmation."
    )
)
async def dell_switch_apply_commands(
    switch_ids: list[str],
    commands: list[str],
    dry_run: bool = True,
    confirmation: str | None = None,
    stop_on_error: bool = True,
    startup_save_user_confirmed: bool = False,
    startup_save_confirmation: str | None = None,
) -> dict:
    """Run confirmed CLI commands; startup writes need separate user authorization."""
    validation_error = _validate_command_plan(switch_ids, commands, dry_run, stop_on_error)
    if not isinstance(startup_save_user_confirmed, bool):
        validation_error = "startup_save_user_confirmed must be a boolean"
    if validation_error:
        return {
            "status": "error",
            "phase": "validation",
            "message": validation_error,
        }

    switch_ids = [switch_id.strip() for switch_id in switch_ids]
    commands = [command.strip() for command in commands]
    plan = _command_plan(switch_ids, commands, stop_on_error)
    required_confirmation = _command_confirmation(plan)
    if dry_run:
        preview = {
            "status": "success",
            "phase": "dry-run",
            "plan": plan,
            "confirmation_required": required_confirmation,
        }
        if plan["writes_startup_configuration"]:
            preview["startup_save_requires_explicit_user_confirmation"] = True
            preview["startup_save_confirmation_required"] = _startup_save_confirmation(plan)
        return preview
    if confirmation != required_confirmation:
        return {
            "status": "error",
            "phase": "confirmation",
            "message": "confirmation must exactly match confirmation_required from the dry run",
            "confirmation_required": required_confirmation,
            "plan": plan,
        }

    if plan["writes_startup_configuration"] and (
        not startup_save_user_confirmed or startup_save_confirmation != _startup_save_confirmation(plan)
    ):
        return {
            "status": "error",
            "phase": "startup-save-confirmation",
            "message": (
                "Writing switch startup configuration requires a separate explicit user request "
                "and confirmation; set startup_save_user_confirmed=true only after that user "
                "confirmation and provide startup_save_confirmation_required from the dry run"
            ),
            "startup_save_confirmation_required": _startup_save_confirmation(plan),
            "plan": plan,
        }

    results = await asyncio.gather(
        *(
            _dell_switch_apply_commands(switch_id, commands, stop_on_error, plan["writes_startup_configuration"])
            for switch_id in switch_ids
        )
    )
    return {
        "status": "success" if all(result.get("status") == "success" for result in results) else "error",
        "phase": "complete",
        "results": results,
    }
