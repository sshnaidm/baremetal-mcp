"""BMC graphical-console capture and explicitly authorized input tools."""

import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Literal

from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from PIL import Image as PILImage

import config as cfg
from config import CONFIG, _load_config, mcp
from helpers import _configured_port

_WORKER_PATH = Path(__file__).with_name("vnc_capture_worker.py")
_ACTIVE_PAGERS: dict[str, dict[str, Any]] = {}
_CONSOLE_PREFLIGHTS: dict[str, dict[str, Any]] = {}
_CONSOLE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_PAGER_ACTIONS = {
    "refresh",
    "next_page",
    "previous_page",
    "first_page",
    "last_page",
    "quit",
    "interrupt",
}


def _error(server_id: str, message: str) -> dict[str, Any]:
    return {"server_id": server_id, "status": "error", "message": message}


def _console_settings(server_id: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Return validated VNC connection settings or a structured error."""
    _load_config()

    server_cfg = CONFIG.get(server_id)
    if not server_cfg:
        return None, _error(server_id, f"Unknown server: {server_id}")

    vnc_port = server_cfg.get("vnc_port")
    if not vnc_port:
        return None, _error(server_id, "No VNC console configured for this server")

    host = server_cfg.get("bmc_ip")
    if not host:
        return None, _error(server_id, "No VNC host or BMC address configured")

    try:
        port = _configured_port(vnc_port, "vnc_port")
    except ValueError as exc:
        return None, _error(server_id, str(exc))

    credentials = cfg.get_server_credentials(server_id) or {}
    password = credentials.get("vnc_password")
    if not password:
        return None, _error(server_id, "Missing vnc_password in secrets")
    password = str(password)

    timeout = cfg.VNC_CAPTURE_TIMEOUT
    key_delay = 0.01

    return {
        "host": str(host),
        "port": port,
        "password": password,
        "timeout": timeout,
        "key_delay": key_delay,
    }, None


def _run_vnc_worker_sync(
    server_id: str,
    mode: str,
    *,
    mode_args: tuple[str | int | float, ...] = (),
    command: str | None = None,
) -> dict[str, Any]:
    """Run one VNC operation in an isolated child process."""
    settings, error = _console_settings(server_id)
    if error:
        return error

    fd, output_name = tempfile.mkstemp(prefix="baremetal-mcp-console-", suffix=".png")
    os.close(fd)
    output_path = Path(output_name)

    worker_stage: str | None = None
    try:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(_WORKER_PATH),
                    mode,
                    settings["host"],
                    str(settings["port"]),
                    str(output_path),
                    str(settings["timeout"]),
                    *[str(value) for value in mode_args],
                ],
                input=settings["password"] + "\n" + (command or ""),
                text=True,
                capture_output=True,
                timeout=(
                    settings["timeout"]
                    + sum(float(value) for value in mode_args if isinstance(value, (int, float)))
                    + ((len(command or "") + 300) * settings["key_delay"] if command else 0)
                    + 5
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stage = _worker_stage(output)
            result = _error(server_id, f"VNC {mode} operation timed out")
            result.update(_input_state(mode, stage))
            return result

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "VNC capture failed").strip()
            detail = detail.replace(settings["password"], "[redacted]")[-1000:]
            result = _error(server_id, detail)
            result.update(_input_state(mode, _worker_stage(completed.stdout or "")))
            return result

        worker_stage = _worker_stage(completed.stdout or "")
        image_data = output_path.read_bytes()
        if not image_data.startswith(b"\x89PNG\r\n\x1a\n"):
            result = _error(server_id, "VNC capture did not produce a valid PNG image")
            result.update(_input_state(mode, worker_stage))
            return result

        with PILImage.open(io.BytesIO(image_data)) as captured:
            width, height = captured.size

        result = {
            "server_id": server_id,
            "status": "success",
            "protocol": "vnc",
            "width": width,
            "height": height,
            "mime_type": "image/png",
            "operation": mode,
            "image": image_data,
        }
        result.update(_input_state(mode, worker_stage))
        return result
    except (OSError, SyntaxError, ValueError, PILImage.DecompressionBombError) as exc:
        result = _error(server_id, f"VNC capture failed: {exc}")
        result.update(_input_state(mode, worker_stage))
        return result
    finally:
        output_path.unlink(missing_ok=True)


def _capture_vnc_console_sync(server_id: str) -> dict[str, Any]:
    """Capture a configured VNC console in an isolated child process."""
    return _run_vnc_worker_sync(server_id, "capture")


def _worker_stage(output: str) -> str | None:
    stages = [line.split("=", 1)[1].strip() for line in output.splitlines() if line.startswith("BM_VNC_STAGE=")]
    return stages[-1] if stages else None


def _input_state(mode: str, stage: str | None) -> dict[str, Any]:
    if mode == "capture":
        return {"input_sent": False, "retry_safe": True}
    if mode == "run":
        if stage == "sent":
            return {
                "command_sent": True,
                "completion_confirmed": False,
                "retry_safe": False,
                "input_state": "sent_unconfirmed",
            }
        if stage == "typing":
            return {
                "command_sent": False,
                "completion_confirmed": False,
                "retry_safe": False,
                "input_state": "partial_text_possible",
            }
        return {
            "command_sent": False,
            "completion_confirmed": False,
            "retry_safe": True,
            "input_state": "not_sent",
        }
    if mode == "pager":
        return {
            "input_sent": stage == "pager_input",
            "retry_safe": stage != "pager_input",
        }
    return {}


def _validate_command(server_id: str, command: str) -> dict[str, Any] | None:
    if not isinstance(command, str) or not command.strip():
        return _error(server_id, "Console command must not be empty")
    if len(command) > 4096:
        return _error(server_id, "Console command must not exceed 4096 characters")
    if any(not 32 <= ord(char) <= 126 for char in command):
        return _error(server_id, "Console command must contain printable ASCII on one line")
    return None


def _validate_wait(server_id: str, wait_seconds: float) -> tuple[float | None, dict[str, object] | None]:
    try:
        wait = float(wait_seconds)
    except (TypeError, ValueError):
        return None, _error(server_id, "Invalid console wait time")
    if not 0.1 <= wait <= 30:
        return None, _error(server_id, "Console wait time must be between 0.1 and 30 seconds")
    return wait, None


def _run_console_command_sync(server_id: str, command: str, wait_seconds: float) -> dict[str, Any]:
    """Type one command and capture the first forced-pager screen."""
    error = _validate_command(server_id, command)
    if error:
        return error
    wait, error = _validate_wait(server_id, wait_seconds)
    if error:
        return error

    settings, error = _console_settings(server_id)
    if error:
        return error
    return _run_vnc_worker_sync(
        server_id,
        "run",
        mode_args=(wait, settings["key_delay"]),
        command=command,
    )


def _pager_action_sync(server_id: str, action: str, wait_seconds: float) -> dict[str, Any]:
    """Send one bounded less-pager action and capture the resulting screen."""
    if action not in _PAGER_ACTIONS:
        return _error(server_id, f"Unsupported console pager action: {action}")
    wait, error = _validate_wait(server_id, wait_seconds)
    if error:
        return error
    return _run_vnc_worker_sync(server_id, "pager", mode_args=(action, wait))


async def _capture_vnc_console(server_id: str) -> dict[str, Any]:
    """Run blocking VNC capture without blocking the MCP event loop."""
    return await asyncio.to_thread(_capture_vnc_console_sync, server_id)


async def _run_console_command(
    server_id: str,
    command: str,
    wait_seconds: float,
) -> dict[str, Any]:
    """Run blocking console command setup without blocking the MCP event loop."""
    return await asyncio.to_thread(_run_console_command_sync, server_id, command, wait_seconds)


async def _pager_action(server_id: str, action: str, wait_seconds: float) -> dict[str, Any]:
    """Run a blocking pager action without blocking the MCP event loop."""
    return await asyncio.to_thread(_pager_action_sync, server_id, action, wait_seconds)


def _tool_result(result: dict[str, Any]) -> ToolResult:
    image_data = result.pop("image", None)
    summary = json.dumps(result)
    content = [summary]
    if image_data is not None:
        content.append(Image(data=image_data, format="png"))
    return ToolResult(content=content, structured_content=result)


def _console_lock(server_id: str) -> asyncio.Lock:
    lock = _CONSOLE_LOCKS.get(server_id)
    if lock is None:
        lock = asyncio.Lock()
        _CONSOLE_LOCKS[server_id] = lock
    return lock


def _session_ttl() -> int:
    try:
        return max(10, min(int(cfg.CONSOLE_SESSION_TTL), 86400))
    except (TypeError, ValueError):
        return 300


def _expire_console_state(server_id: str) -> dict[str, Any] | None:
    now = time.monotonic()
    session = _ACTIVE_PAGERS.get(server_id)
    if session and session.get("expires_at", 0) <= now:
        # Expiry only invalidates blind input; it cannot prove that the remote
        # pager exited. Retain the session so new commands remain blocked until
        # a fresh framebuffer is inspected and explicitly authorizes recovery.
        session["expired"] = True
    preflight = _CONSOLE_PREFLIGHTS.get(server_id)
    if preflight and preflight.get("expires_at", 0) <= now:
        _CONSOLE_PREFLIGHTS.pop(server_id, None)
    return session


def _new_preflight(server_id: str) -> dict[str, Any]:
    ttl = _session_ttl()
    value = {
        "token": uuid.uuid4().hex,
        "created_at": time.monotonic(),
        "expires_at": time.monotonic() + ttl,
    }
    _CONSOLE_PREFLIGHTS[server_id] = value
    return value


def _consume_preflight(
    server_id: str,
    token: str,
    *,
    created_after: float | None = None,
) -> bool:
    _expire_console_state(server_id)
    value = _CONSOLE_PREFLIGHTS.get(server_id)
    if (
        not value
        or not isinstance(token, str)
        or value.get("token") != token
        or (created_after is not None and float(value.get("created_at", 0)) < created_after)
    ):
        return False
    _CONSOLE_PREFLIGHTS.pop(server_id, None)
    return True


@mcp.tool(
    description=(
        "Capture the current BMC VNC console as a read-only PNG. A short-lived input token is "
        "returned only after a successful capture so callers can inspect the screen before typing."
    )
)
async def capture_console_screen(server_id: str) -> ToolResult:
    """Return console metadata and native MCP ImageContent without sending input."""
    async with _console_lock(server_id):
        _expire_console_state(server_id)
        result = await _capture_vnc_console(server_id)
        if result.get("status") == "success":
            preflight = _new_preflight(server_id)
            result.update(
                {
                    "input_confirmation_token": preflight["token"],
                    "input_token_expires_in_seconds": _session_ttl(),
                    "next": "Inspect this image before passing the token to run_console_command",
                }
            )
    return _tool_result(result)


@mcp.tool(
    description=(
        "Type an explicitly authorized, non-interactive shell command into a BMC VNC console "
        "and capture its first less-pager screen. This changes console state."
    )
)
async def run_console_command(
    server_id: str,
    command: str,
    confirmation_token: str,
    wait_seconds: float = 1.5,
) -> ToolResult:
    """Start a command in a forced pager and return a token for subsequent paging."""
    async with _console_lock(server_id):
        _expire_console_state(server_id)
        if server_id in _ACTIVE_PAGERS:
            session = _ACTIVE_PAGERS[server_id]
            result = _error(
                server_id,
                "A console pager session is already active or remotely unknown; recover, quit, "
                "or visually authorize abandon before starting another command",
            )
            result.update(
                {
                    "command_sent": False,
                    "retry_safe": True,
                    "input_state": "not_sent",
                    "pager_session_id": session.get("session_id"),
                    "pager_session_expired": bool(session.get("expired")),
                }
            )
            return _tool_result(result)

        validation_error = _validate_command(server_id, command)
        wait, wait_error = _validate_wait(server_id, wait_seconds)
        if validation_error or wait_error:
            result = validation_error or wait_error
            result.update({"command_sent": False, "retry_safe": True, "input_state": "not_sent"})
            return _tool_result(result)
        if not _consume_preflight(server_id, confirmation_token):
            result = _error(
                server_id,
                "A valid unexpired confirmation_token from capture_console_screen is required",
            )
            result.update({"command_sent": False, "retry_safe": True, "input_state": "not_sent"})
            return _tool_result(result)

        result = await _run_console_command(server_id, command, wait)
        if result.get("status") == "success":
            session_id = uuid.uuid4().hex
            now = time.monotonic()
            _ACTIVE_PAGERS[server_id] = {
                "session_id": session_id,
                "page": 1,
                "created_at": now,
                "last_activity": now,
                "expires_at": now + _session_ttl(),
                "expired": False,
            }
            result.update(
                {
                    "session_id": session_id,
                    "pager_active": True,
                    "page": 1,
                    "visual_verification_required": True,
                    "command_sent": True,
                    "completion_confirmed": False,
                    "retry_safe": False,
                    "input_state": "sent_unconfirmed",
                    "next": "Inspect the image, then use console_pager_action; never blindly retry",
                }
            )
        else:
            # Configuration and local validation failures happen before the
            # worker can type. Worker failures already carry a conservative
            # stage-derived state, which must never be weakened here.
            result.setdefault("command_sent", False)
            result.setdefault("retry_safe", result.get("command_sent") is False)
            result.setdefault("input_state", "not_sent" if result["retry_safe"] else "sent_unconfirmed")
    return _tool_result(result)


@mcp.tool(
    description=(
        "Navigate or close an active BMC console less-pager session and capture the resulting screen. "
        "Supported actions: refresh, next_page, previous_page, first_page, last_page, quit, "
        "interrupt, abandon. Abandon clears local tracking without sending a key."
    )
)
async def console_pager_action(
    server_id: str,
    session_id: str,
    action: Literal[
        "refresh",
        "next_page",
        "previous_page",
        "first_page",
        "last_page",
        "quit",
        "interrupt",
        "abandon",
    ],
    wait_seconds: float = 0.75,
    confirmation_token: str | None = None,
) -> ToolResult:
    """Perform one guarded pager action and return the new framebuffer."""
    async with _console_lock(server_id):
        session = _expire_console_state(server_id)
        if not session or session.get("session_id") != session_id:
            result = _error(
                server_id,
                "No matching active console pager session; it may have expired. Capture before further input.",
            )
            result.update({"input_sent": False, "retry_safe": True})
            return _tool_result(result)

        if session.get("expired") and not _consume_preflight(
            server_id,
            confirmation_token or "",
            created_after=float(session.get("expires_at", 0)),
        ):
            result = _error(
                server_id,
                "The pager session expired and remote state is unknown. Capture and inspect a fresh "
                "framebuffer, then pass its confirmation_token to authorize this recovery action.",
            )
            result.update(
                {
                    "input_sent": False,
                    "retry_safe": True,
                    "pager_active": True,
                    "pager_session_expired": True,
                    "session_id": session_id,
                }
            )
            return _tool_result(result)

        if action == "abandon":
            _ACTIVE_PAGERS.pop(server_id, None)
            result = {
                "server_id": server_id,
                "status": "success",
                "operation": "local_session",
                "session_id": session_id,
                "pager_active": False,
                "page": session.get("page"),
                "action": action,
                "message": "Local pager tracking cleared; no VNC key was sent",
            }
            return _tool_result(result)

        result = await _pager_action(server_id, action, wait_seconds)
        if result.get("status") == "success":
            page = session.get("page")
            if action == "next_page" and isinstance(page, int):
                page += 1
            elif action == "previous_page" and isinstance(page, int):
                page = max(1, page - 1)
            elif action == "first_page":
                page = 1
            elif action == "last_page":
                page = None

            finished = action in {"quit", "interrupt"}
            if finished:
                _ACTIVE_PAGERS.pop(server_id, None)
            else:
                session["page"] = page
                session["last_activity"] = time.monotonic()
                session["expires_at"] = time.monotonic() + _session_ttl()
                session["expired"] = False
            result.update(
                {
                    "session_id": session_id,
                    "pager_active": not finished,
                    "page": page,
                    "action": action,
                }
            )
    return _tool_result(result)


@mcp.tool(
    description=(
        "Report local VNC pager/preflight tracking for one host without sending console input. "
        "Expired pager state is retained as remotely unknown until visually authorized recovery."
    )
)
async def get_console_session_status(server_id: str) -> dict[str, Any]:
    async with _console_lock(server_id):
        session = _expire_console_state(server_id)
        preflight = _CONSOLE_PREFLIGHTS.get(server_id)
        now = time.monotonic()
        return {
            "server_id": server_id,
            "status": "success",
            "pager": (
                {
                    "tracked": True,
                    "session_id": session["session_id"],
                    "page": session.get("page"),
                    "expires_in_seconds": max(0, int(session["expires_at"] - now)),
                    "remote_state_verified": False,
                    "expired": bool(session.get("expired")),
                    "recovery_confirmation_required": bool(session.get("expired")),
                }
                if session
                else {"tracked": False, "remote_state_verified": False}
            ),
            "input_preflight": (
                {
                    "available": True,
                    "confirmation_token": preflight["token"],
                    "expires_in_seconds": max(0, int(preflight["expires_at"] - now)),
                }
                if preflight
                else {"available": False}
            ),
        }
