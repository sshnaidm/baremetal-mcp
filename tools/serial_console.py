"""Guarded Linux command execution through Dell SOL and HPE iLO VSP."""

from __future__ import annotations

import asyncio
import re
import secrets
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import paramiko

import config as cfg
from config import mcp
from helpers import _configured_port

_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_COMMAND_TIMEOUT = 60.0
_DEFAULT_BATCH_CONCURRENCY = 4
_DEFAULT_OUTPUT_LIMIT = 64 * 1024
_MAX_COMMAND_CHARS = 4096
_MAX_COMMANDS_PER_SESSION = 64
_MAX_BATCH_SIZE = 64
_MAX_CONCURRENCY = 12
_MAX_CONNECT_TIMEOUT = 300.0
_MAX_COMMAND_TIMEOUT = 3600.0
_RECV_SIZE = 65535
_POLL_INTERVAL = 0.05
_ATTACH_SETTLE_SECONDS = 2.0
_WAKE_SETTLE_SECONDS = 0.5
_PROBE_TIMEOUT = 10.0
_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_LOGIN_PROMPT_RE = re.compile(r"(?:login|password):\s*\Z", re.IGNORECASE)
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")

_SERIAL_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class _TransportProfile:
    name: str
    attach_command: str
    detach_sequence: str


_DELL_SOL = _TransportProfile(
    name="idrac-ssh-sol",
    attach_command="console com2",
    detach_sequence="\x1c",
)
_HPE_VSP = _TransportProfile(
    name="ilo-ssh-vsp",
    attach_command="VSP",
    detach_sequence="\x1b(",
)


@dataclass
class _ReadResult:
    text: str
    found: bool
    total_chars: int
    truncated: bool


class _BoundedCapture:
    """Keep bounded head and tail text while counting the complete stream."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.head_limit = (self.limit + 1) // 2
        self.tail_limit = self.limit - self.head_limit
        self.head = ""
        self.tail = ""
        self.total = 0

    def add(self, value: str) -> None:
        if not value:
            return
        self.total += len(value)
        head_missing = self.head_limit - len(self.head)
        if head_missing > 0:
            self.head += value[:head_missing]
            value = value[head_missing:]
        if value and self.tail_limit:
            self.tail = (self.tail + value)[-self.tail_limit :]

    def result(self, found: bool) -> _ReadResult:
        return _ReadResult(
            text=self.head + self.tail,
            found=found,
            total_chars=self.total,
            truncated=self.total > self.limit,
        )


class _ChannelReader:
    """Read token-delimited console output without losing post-token bytes."""

    def __init__(self, channel: paramiko.Channel) -> None:
        self.channel = channel
        self.pending = ""

    def read_until(self, token: str, deadline: float, capture_limit: int) -> _ReadResult:
        capture = _BoundedCapture(capture_limit)
        buffer = self.pending
        self.pending = ""

        while True:
            index = buffer.find(token)
            if index >= 0:
                capture.add(buffer[:index])
                self.pending = buffer[index + len(token) :]
                return capture.result(found=True)

            # Keep only the suffix that might be the start of a split token.
            flush_length = max(0, len(buffer) - len(token) + 1)
            if flush_length:
                capture.add(buffer[:flush_length])
                buffer = buffer[flush_length:]

            if time.monotonic() >= deadline:
                capture.add(buffer)
                return capture.result(found=False)

            if self.channel.recv_ready():
                chunk = self.channel.recv(_RECV_SIZE)
                if not chunk:
                    capture.add(buffer)
                    return capture.result(found=False)
                if isinstance(chunk, bytes):
                    buffer += chunk.decode("utf-8", errors="replace")
                else:
                    buffer += str(chunk)
            else:
                time.sleep(_POLL_INTERVAL)

    def drain(self, duration: float, capture_limit: int = 8192) -> _ReadResult:
        capture = _BoundedCapture(capture_limit)
        capture.add(self.pending)
        self.pending = ""
        deadline = time.monotonic() + max(0.0, duration)
        while time.monotonic() < deadline:
            if self.channel.recv_ready():
                chunk = self.channel.recv(_RECV_SIZE)
                if not chunk:
                    break
                capture.add(chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk))
            else:
                time.sleep(_POLL_INTERVAL)
        return capture.result(found=False)


def _configured_number(name: str, fallback: float) -> float:
    try:
        return float(getattr(cfg, name, fallback))
    except (TypeError, ValueError):
        return float(fallback)


def _configured_int(name: str, fallback: int) -> int:
    try:
        return int(getattr(cfg, name, fallback))
    except (TypeError, ValueError):
        return int(fallback)


def _clean_terminal(value: str) -> str:
    value = _ANSI_OSC_RE.sub("", value)
    value = _ANSI_CSI_RE.sub("", value).replace("\r", "")
    # Apply simple terminal backspace semantics before removing controls.
    while "\b" in value:
        value = re.sub(r"[^\n]\x08", "", value)
        value = value.replace("\b", "")
    return "".join(char for char in value if char in "\n\t" or ord(char) >= 32)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _safe_exception(exc: Exception, password: str = "") -> str:
    message = f"{type(exc).__name__}: {exc}"
    if password:
        message = message.replace(password, "[redacted]")
    return message[-1000:]


def _error_result(
    server_id: str,
    message: str,
    *,
    phase: str,
    transport: str | None = None,
    command_sent: bool = False,
) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "status": "error",
        "transport": transport,
        "phase": phase,
        "command_sent": command_sent,
        "exit_code": None,
        "output": "",
        "truncated": False,
        "output_chars_total": 0,
        "result_confirmed": False,
        "retry_safe": not command_sent,
        "retryable": not command_sent,
        "message": message,
    }


def _validate_command(command: str) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return "Console command must not be empty"
    if len(command) > _MAX_COMMAND_CHARS:
        return f"Console command must not exceed {_MAX_COMMAND_CHARS} characters"
    if any(not 32 <= ord(char) <= 126 for char in command):
        return "Console command must contain printable ASCII on one line only"
    return None


def _validate_commands(commands: Sequence[tuple[str, str]]) -> str | None:
    if not isinstance(commands, (list, tuple)) or not commands:
        return "At least one labeled command is required"
    if len(commands) > _MAX_COMMANDS_PER_SESSION:
        return f"At most {_MAX_COMMANDS_PER_SESSION} commands may share one console session"
    labels: list[str] = []
    for item in commands:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return "Each command must be a (label, command) pair"
        label, command = item
        if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
            return "Command labels must be 1-64 characters using letters, digits, dot, underscore, or dash"
        error = _validate_command(command)
        if error:
            return f"Command {label}: {error}"
        labels.append(label)
    if len(labels) != len(set(labels)):
        return "Command labels must be unique within a console session"
    return None


def _normalize_transport(server: dict[str, Any]) -> tuple[_TransportProfile | None, str | None]:
    serial = server.get("serial_console")
    if not isinstance(serial, dict):
        return None, "serial_console configuration is required"
    if "transport" not in serial:
        return None, "serial_console.transport must be explicitly configured"
    requested = str(serial.get("transport")).strip().lower()
    raw_vendor = server.get("vendor") or server.get("bmc_type") or ""
    normalize_vendor = getattr(cfg, "normalize_vendor", None)
    vendor = normalize_vendor(raw_vendor) if callable(normalize_vendor) else str(raw_vendor).strip().lower()

    if requested in {"sol", "dell", "idrac", "idrac-ssh-sol"}:
        profile = _DELL_SOL
    elif requested in {"vsp", "hpe", "hp", "ilo", "ilo-ssh-vsp"}:
        profile = _HPE_VSP
    elif requested == "auto":
        if vendor in {"dell", "idrac"}:
            profile = _DELL_SOL
        elif vendor in {"hpe", "hp", "ilo"}:
            profile = _HPE_VSP
        else:
            return None, "Serial console transport requires a Dell or HPE vendor hint"
    else:
        return None, f"Unsupported serial console transport: {requested}"

    if vendor in {"dell", "idrac"} and profile is not _DELL_SOL:
        return None, "Configured serial console transport conflicts with Dell vendor"
    if vendor in {"hpe", "hp", "ilo"} and profile is not _HPE_VSP:
        return None, "Configured serial console transport conflicts with HPE vendor"
    return profile, None


def _serial_settings(server_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    cfg._load_config()
    server = cfg.CONFIG.get(server_id)
    if not isinstance(server, dict):
        return None, _error_result(server_id, f"Unknown server: {server_id}", phase="config")

    profile, transport_error = _normalize_transport(server)
    if transport_error:
        return None, _error_result(server_id, transport_error, phase="config")

    host = server.get("bmc_ip") or server.get("hostname")
    if not host:
        return None, _error_result(server_id, "No BMC address configured", phase="config", transport=profile.name)

    credential_resolver = getattr(cfg, "get_server_credentials", None)
    credential = credential_resolver(server_id) if callable(credential_resolver) else cfg.SECRETS.get(server_id)
    if not isinstance(credential, dict):
        credential = {}
    username = credential.get("username")
    password = credential.get("password")
    if not username or not password:
        return None, _error_result(
            server_id,
            "Missing BMC username or password in secrets",
            phase="config",
            transport=profile.name,
        )

    serial = server.get("serial_console")
    if not isinstance(serial, dict):
        return None, _error_result(
            server_id,
            "serial_console configuration is required",
            phase="config",
            transport=profile.name,
        )
    try:
        port = _configured_port(serial.get("port"), "serial_console.port")
    except ValueError as exc:
        return None, _error_result(server_id, str(exc), phase="config", transport=profile.name)

    connect_timeout = serial.get(
        "connect_timeout",
        _configured_number("SOL_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT),
    )
    try:
        connect_timeout = float(connect_timeout)
    except (TypeError, ValueError):
        connect_timeout = 0
    if not 0.1 <= connect_timeout <= _MAX_CONNECT_TIMEOUT:
        return None, _error_result(
            server_id,
            f"Serial-console connect timeout must be between 0.1 and {_MAX_CONNECT_TIMEOUT:g} seconds",
            phase="config",
            transport=profile.name,
        )

    return {
        "server_id": server_id,
        "host": str(host),
        "port": port,
        "username": str(username),
        "password": str(password),
        "connect_timeout": connect_timeout,
        "profile": profile,
    }, None


def _connect_ssh(settings: dict[str, Any]) -> paramiko.SSHClient:
    """Connect with password auth, falling back to keyboard-interactive auth."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            settings["host"],
            port=settings["port"],
            username=settings["username"],
            password=settings["password"],
            timeout=settings["connect_timeout"],
            auth_timeout=settings["connect_timeout"],
            banner_timeout=settings["connect_timeout"],
            look_for_keys=False,
            allow_agent=False,
        )
        return client
    except paramiko.BadAuthenticationType as exc:
        if "keyboard-interactive" not in exc.allowed_types:
            client.close()
            raise
        # The failed password attempt may leave a transport/socket behind.
        client.close()

    transport: paramiko.Transport | None = None
    raw_socket = None
    try:
        raw_socket = socket.create_connection(
            (settings["host"], settings["port"]),
            timeout=settings["connect_timeout"],
        )
        transport = paramiko.Transport(raw_socket)
        transport.banner_timeout = settings["connect_timeout"]
        transport.auth_timeout = settings["connect_timeout"]
        transport.start_client(timeout=settings["connect_timeout"])

        def answer_prompts(
            _title: str,
            _instructions: str,
            prompts: list[tuple[str, bool]],
        ) -> list[str]:
            return [settings["username"] if echo else settings["password"] for _prompt, echo in prompts]

        transport.auth_interactive(settings["username"], answer_prompts)
        client._transport = transport
        return client
    except Exception:
        if transport is not None:
            transport.close()
        elif raw_socket is not None:
            raw_socket.close()
        client.close()
        raise


def _send(channel: paramiko.Channel, value: str) -> None:
    channel.sendall(value)


def _prompt_probe(
    channel: paramiko.Channel,
    reader: _ChannelReader,
    timeout_seconds: float,
) -> tuple[bool, str]:
    nonce = secrets.token_hex(12)
    marker = f"__BM_PROBE_{nonce}__"
    command = f"printf '\\n__BM_PROBE_%s__\\n' '{nonce}'\r"
    _send(channel, command)
    result = reader.read_until(
        marker,
        time.monotonic() + min(timeout_seconds, _PROBE_TIMEOUT),
        8192,
    )
    return result.found, _clean_terminal(result.text)


def _execute_command(
    channel: paramiko.Channel,
    reader: _ChannelReader,
    server_id: str,
    transport: str,
    label: str,
    command: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    nonce = secrets.token_hex(12)
    begin_marker = f"__BM_BEGIN_{nonce}__"
    end_marker = f"__BM_END_{nonce}__"
    command_line = (
        "printf '\\n__BM_BEGIN_%s__\\n' "
        f"'{nonce}'; sh -c {_shell_quote(command)} </dev/null 2>&1; "
        "__bm_rc=$?; printf '\\n__BM_END_%s__ rc=%s\\n' "
        f"'{nonce}' \"$__bm_rc\"\r"
    )
    base = {
        "label": label,
        "server_id": server_id,
        "transport": transport,
        "command_sent": False,
        "exit_code": None,
        "output": "",
        "truncated": False,
        "output_chars_total": 0,
        "result_confirmed": False,
        "retry_safe": True,
        "retryable": True,
    }

    try:
        # A sendall failure can occur after a partial write. Conservatively
        # classify the command as possibly sent so callers never auto-retry it.
        base["command_sent"] = True
        base["retry_safe"] = False
        base["retryable"] = False
        _send(channel, command_line)
    except Exception as exc:
        return {
            **base,
            "status": "error",
            "phase": "command-send",
            "message": _safe_exception(exc),
        }

    try:
        deadline = time.monotonic() + timeout_seconds
        started = reader.read_until(begin_marker, deadline, 8192)
        if not started.found:
            return {
                **base,
                "status": "error",
                "phase": "command-result",
                "message": "Command was sent but its start marker was not observed; outcome is unknown",
            }

        output = reader.read_until(end_marker, deadline, _output_limit())
        base.update(
            {
                "output": _clean_terminal(output.text).strip("\n"),
                "truncated": output.truncated,
                "output_chars_total": output.total_chars,
            }
        )
        if not output.found:
            return {
                **base,
                "status": "error",
                "phase": "command-result",
                "message": "Command was sent but its completion marker was not observed; outcome is unknown",
            }

        suffix = reader.read_until("\n", deadline, 256)
        rc_match = re.fullmatch(r"\s*rc=(\d{1,3})\s*", _clean_terminal(suffix.text))
        if not suffix.found or not rc_match:
            return {
                **base,
                "status": "error",
                "phase": "command-result",
                "message": "Command completion marker did not contain a valid exit code; outcome is unknown",
            }
    except Exception as exc:
        return {
            **base,
            "status": "error",
            "phase": "command-result",
            "message": f"Console read failed after command send; outcome is unknown: {_safe_exception(exc)}",
        }

    exit_code = int(rc_match.group(1))
    base.update({"exit_code": exit_code, "result_confirmed": True})
    if exit_code == 0:
        return {**base, "status": "success", "phase": "complete"}
    return {
        **base,
        "status": "error",
        "phase": "complete",
        "message": f"Command completed with exit code {exit_code}",
    }


def _output_limit() -> int:
    value = _configured_int("CONSOLE_OUTPUT_LIMIT", _DEFAULT_OUTPUT_LIMIT)
    return min(max(value, 1024), 1024 * 1024)


def _not_sent_commands(
    server_id: str,
    transport: str | None,
    commands: Sequence[tuple[str, str]],
    phase: str,
    message: str,
    *,
    retry_safe: bool,
) -> list[dict[str, Any]]:
    return [
        {
            **_error_result(
                server_id,
                message,
                phase=phase,
                transport=transport,
                command_sent=False,
            ),
            "label": label,
            "retry_safe": retry_safe,
            "retryable": retry_safe,
        }
        for label, _command in commands
    ]


def _aggregate_session(
    server_id: str,
    transport: str | None,
    commands: list[dict[str, Any]],
    *,
    phase: str,
    message: str | None = None,
) -> dict[str, Any]:
    successful = sum(item.get("status") == "success" for item in commands)
    sent = sum(item.get("command_sent") is True for item in commands)
    unconfirmed = any(
        item.get("command_sent") is True and item.get("result_confirmed") is not True for item in commands
    )
    if commands and successful == len(commands):
        status = "success"
    elif successful:
        status = "partial"
    else:
        status = "error"
    result: dict[str, Any] = {
        "server_id": server_id,
        "status": status,
        "transport": transport,
        "phase": phase,
        "command_sent": sent > 0,
        "commands_requested": len(commands),
        "commands_sent": sent,
        "commands_successful": successful,
        "sent_unconfirmed": unconfirmed,
        "retry_safe": sent == 0,
        "retryable": sent == 0,
        "commands": commands,
    }
    if message:
        result["message"] = message
    return result


def _run_serial_commands_sync(
    server_id: str,
    commands: Sequence[tuple[str, str]],
    timeout_seconds: float,
) -> dict[str, Any]:
    settings, settings_error = _serial_settings(server_id)
    if settings_error:
        items = _not_sent_commands(
            server_id,
            settings_error.get("transport"),
            commands,
            settings_error["phase"],
            settings_error["message"],
            retry_safe=True,
        )
        return _aggregate_session(
            server_id,
            settings_error.get("transport"),
            items,
            phase=settings_error["phase"],
            message=settings_error["message"],
        )

    profile: _TransportProfile = settings["profile"]
    client: paramiko.SSHClient | None = None
    channel: paramiko.Channel | None = None
    attached = False
    phase = "ssh-connect"
    results: list[dict[str, Any]] = []
    try:
        client = _connect_ssh(settings)
        phase = "shell-open"
        channel = client.invoke_shell(term="vt100", width=160, height=50)
        reader = _ChannelReader(channel)

        phase = "serial-attach"
        _send(channel, profile.attach_command + "\r")
        attached = True
        attach_output = reader.drain(_ATTACH_SETTLE_SECONDS)
        _send(channel, "\r")
        wake_output = reader.drain(_WAKE_SETTLE_SECONDS)
        visible = _clean_terminal(attach_output.text + wake_output.text).rstrip()
        if visible:
            last_line = visible.splitlines()[-1]
            if _LOGIN_PROMPT_RE.search(last_line):
                message = f"Serial console is waiting at a {last_line.strip()}; requested command was not sent"
                items = _not_sent_commands(
                    server_id,
                    profile.name,
                    commands,
                    "shell-readiness",
                    message,
                    retry_safe=True,
                )
                return _aggregate_session(
                    server_id,
                    profile.name,
                    items,
                    phase="shell-readiness",
                    message=message,
                )

        phase = "prompt-probe"
        prompt_ready, _probe_output = _prompt_probe(channel, reader, timeout_seconds)
        if not prompt_ready:
            message = "Shell prompt probe did not return its nonce; requested command was not sent"
            items = _not_sent_commands(
                server_id,
                profile.name,
                commands,
                phase,
                message,
                retry_safe=True,
            )
            return _aggregate_session(
                server_id,
                profile.name,
                items,
                phase=phase,
                message=message,
            )

        for index, (label, command) in enumerate(commands):
            phase = "command-result"
            result = _execute_command(
                channel,
                reader,
                server_id,
                profile.name,
                label,
                command,
                timeout_seconds,
            )
            results.append(result)
            if result["command_sent"] and not result["result_confirmed"]:
                message = (
                    f"Command {label} was sent without a confirmed result; "
                    "remaining commands were not sent and no automatic retry was attempted"
                )
                results.extend(
                    _not_sent_commands(
                        server_id,
                        profile.name,
                        commands[index + 1 :],
                        "skipped",
                        message,
                        retry_safe=False,
                    )
                )
                return _aggregate_session(
                    server_id,
                    profile.name,
                    results,
                    phase="command-result",
                    message=message,
                )

        return _aggregate_session(
            server_id,
            profile.name,
            results,
            phase="complete",
        )
    except Exception as exc:
        message = _safe_exception(exc, settings["password"])
        # If an exception follows a successful send, preserve that uncertainty
        # and do not classify the command as retry-safe.
        sent = any(item.get("command_sent") for item in results)
        remaining = commands[len(results) :]
        results.extend(
            _not_sent_commands(
                server_id,
                profile.name,
                remaining,
                phase,
                message,
                retry_safe=not sent,
            )
        )
        return _aggregate_session(
            server_id,
            profile.name,
            results,
            phase=phase,
            message=message,
        )
    finally:
        if channel is not None:
            if attached:
                try:
                    _send(channel, profile.detach_sequence)
                except Exception:
                    pass
            try:
                channel.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _timeout_value(timeout_seconds: float | None) -> tuple[float | None, str | None]:
    value = (
        _configured_number("CONSOLE_COMMAND_TIMEOUT", _DEFAULT_COMMAND_TIMEOUT)
        if timeout_seconds is None
        else timeout_seconds
    )
    if isinstance(value, bool):
        return None, "Invalid console command timeout"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None, "Invalid console command timeout"
    if not 0.1 <= value <= _MAX_COMMAND_TIMEOUT:
        return None, f"Console command timeout must be between 0.1 and {_MAX_COMMAND_TIMEOUT:g} seconds"
    return value, None


def _serial_lock(server_id: str) -> asyncio.Lock:
    return _SERIAL_LOCKS.setdefault(server_id, asyncio.Lock())


_ThreadResult = TypeVar("_ThreadResult")


async def _run_in_thread(
    function: Callable[..., _ThreadResult],
    *args: object,
) -> _ThreadResult:
    """Small indirection that keeps the blocking transport easy to test/wrap."""
    return await asyncio.to_thread(function, *args)


async def _run_serial_commands(
    server_id: str,
    commands: list[tuple[str, str]],
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Attach once and execute ordered, labeled commands through SOL or VSP."""
    server_id = server_id.strip() if isinstance(server_id, str) else ""
    if not server_id:
        return _aggregate_session(
            "",
            None,
            [],
            phase="validation",
            message="server_id is required",
        )
    error = _validate_commands(commands)
    if error:
        return _aggregate_session(
            server_id,
            None,
            [],
            phase="validation",
            message=error,
        )
    timeout, timeout_error = _timeout_value(timeout_seconds)
    if timeout_error:
        items = _not_sent_commands(
            server_id,
            None,
            commands,
            "validation",
            timeout_error,
            retry_safe=True,
        )
        return _aggregate_session(
            server_id,
            None,
            items,
            phase="validation",
            message=timeout_error,
        )

    normalized = [(str(label), str(command)) for label, command in commands]
    async with _serial_lock(server_id):
        return await _run_in_thread(
            _run_serial_commands_sync,
            server_id,
            normalized,
            timeout,
        )


async def _run_serial_command(
    server_id: str,
    command: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Execute one command and return a flattened per-host result."""
    result = await _run_serial_commands(
        server_id,
        [("command", command)],
        timeout_seconds,
    )
    if result.get("commands"):
        command_result = dict(result["commands"][0])
        command_result.pop("label", None)
        if result.get("message") and "message" not in command_result:
            command_result["message"] = result["message"]
        return command_result
    return _error_result(
        server_id if isinstance(server_id, str) else "",
        result.get("message", "Serial command validation failed"),
        phase=result.get("phase", "validation"),
        transport=result.get("transport"),
    )


def _normalize_server_ids(server_ids: list[str]) -> tuple[list[str] | None, str | None, int]:
    if not isinstance(server_ids, list) or not server_ids:
        return None, "server_ids must be a non-empty list", 0
    if len(server_ids) > _MAX_BATCH_SIZE:
        return None, f"At most {_MAX_BATCH_SIZE} server IDs may be requested at once", 0
    ordered: list[str] = []
    seen = set()
    for value in server_ids:
        if not isinstance(value, str) or not value.strip():
            return None, "Every server_id must be a non-empty string", 0
        server_id = value.strip()
        if len(server_id) > 128 or any(not 32 <= ord(char) <= 126 for char in server_id):
            return None, "Invalid server_id", 0
        if server_id not in seen:
            seen.add(server_id)
            ordered.append(server_id)
    return ordered, None, len(server_ids) - len(ordered)


def _concurrency_value(concurrency: int | None) -> tuple[int | None, str | None]:
    value = (
        _configured_int("CONSOLE_BATCH_CONCURRENCY", _DEFAULT_BATCH_CONCURRENCY) if concurrency is None else concurrency
    )
    if isinstance(value, bool):
        return None, "Invalid console batch concurrency"
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None, "Invalid console batch concurrency"
    if not 1 <= value <= _MAX_CONCURRENCY:
        return None, f"Console batch concurrency must be between 1 and {_MAX_CONCURRENCY}"
    return value, None


@mcp.tool(
    description=(
        "Dry-run by default for one non-interactive shell command on multiple configured Dell "
        "SOL or HPE iLO VSP consoles. Actual input requires dry_run=false and confirm_command "
        "exactly matching command. Hosts are ordered and deduplicated. Each result reports "
        "whether the command was sent and whether its exit status was confirmed; sent-but-"
        "unconfirmed commands are never retried automatically."
    )
)
async def run_console_command_batch(
    server_ids: list[str],
    command: str,
    timeout_seconds: float | None = None,
    concurrency: int | None = None,
    dry_run: bool = True,
    confirm_command: str | None = None,
) -> dict[str, Any]:
    """Run one guarded console command across an explicitly selected host batch."""
    normalized, ids_error, duplicates_removed = _normalize_server_ids(server_ids)
    command_error = _validate_command(command)
    timeout, timeout_error = _timeout_value(timeout_seconds)
    limit, concurrency_error = _concurrency_value(concurrency)
    guard_error = None
    if not isinstance(dry_run, bool):
        guard_error = "dry_run must be true or false"
    elif not dry_run and (not isinstance(confirm_command, str) or not secrets.compare_digest(confirm_command, command)):
        guard_error = "Execution requires confirm_command to exactly match command"
    validation_error = ids_error or command_error or timeout_error or concurrency_error or guard_error
    if validation_error:
        return {
            "status": "error",
            "phase": "validation",
            "message": validation_error,
            "requested_count": len(server_ids) if isinstance(server_ids, list) else 0,
            "unique_count": len(normalized or []),
            "duplicates_removed": duplicates_removed,
            "successful": 0,
            "failed": list(normalized or []),
            "retryable": bool(normalized),
            "retryable_hosts": list(normalized or []),
            "results": [],
        }

    if dry_run:
        results = []
        for server_id in normalized:
            settings, settings_error = _serial_settings(server_id)
            if settings_error:
                result = {
                    **settings_error,
                    "phase": "dry-run",
                    "retryable": True,
                    "retry_safe": True,
                }
            else:
                result = {
                    "server_id": server_id,
                    "status": "success",
                    "transport": settings["profile"].name,
                    "phase": "dry-run",
                    "command_sent": False,
                    "exit_code": None,
                    "output": "",
                    "truncated": False,
                    "output_chars_total": 0,
                    "result_confirmed": False,
                    "retry_safe": True,
                    "retryable": True,
                    "message": "Configuration is ready; no connection or console input was attempted",
                }
            results.append(result)
        successful = sum(result["status"] == "success" for result in results)
        status = "success" if successful == len(results) else "partial" if successful else "error"
        failed = [server_id for server_id, result in zip(normalized, results) if result["status"] != "success"]
        return {
            "status": status,
            "phase": "dry-run",
            "dry_run": True,
            "server_ids": normalized,
            "requested_count": len(server_ids),
            "unique_count": len(normalized),
            "duplicates_removed": duplicates_removed,
            "successful": successful,
            "failed": failed,
            "retryable": bool(failed),
            "retryable_hosts": failed,
            "results": results,
        }

    semaphore = asyncio.Semaphore(limit)

    async def run(server_id: str) -> dict[str, Any]:
        async with semaphore:
            return await _run_serial_command(server_id, command, timeout)

    results = await asyncio.gather(*(run(server_id) for server_id in normalized))
    successful = sum(result.get("status") == "success" for result in results)
    if successful == len(results):
        status = "success"
    elif successful:
        status = "partial"
    else:
        status = "error"
    failed_results = [result for result in results if result.get("status") != "success"]
    retryable_hosts = [
        server_id
        for server_id, result in zip(normalized, results)
        if result.get("status") != "success" and result.get("command_sent") is False
    ]
    return {
        "status": status,
        "phase": "complete",
        "dry_run": False,
        "server_ids": normalized,
        "requested_count": len(server_ids),
        "unique_count": len(normalized),
        "duplicates_removed": duplicates_removed,
        "successful": successful,
        "failed": [server_id for server_id, result in zip(normalized, results) if result.get("status") != "success"],
        "retryable": bool(failed_results) and len(retryable_hosts) == len(failed_results),
        "retryable_hosts": retryable_hosts,
        "results": results,
    }
