#!/usr/bin/env python3
"""In-memory background operations for fleet workflows that exceed MCP call timeouts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
import uuid

import config as cfg
from config import mcp
from tools.dell import export_hardware_inventory_xml
from tools.network_collect import collect_network_inventory
from tools.serial_console import run_console_command_batch


_MAX_OPERATIONS = 128
_OPERATIONS: Dict[str, Dict[str, Any]] = {}
_OPERATION_LOCK = asyncio.Lock()
_PROCESS_INSTANCE_ID = uuid.uuid4().hex[:12]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _operation_ttl() -> int:
    try:
        value = int(getattr(cfg, "OPERATION_TTL", 3600))
    except (TypeError, ValueError):
        value = 3600
    return max(60, min(value, 86400))


def _public_operation(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"task", "private_arguments", "expires_at_monotonic"}
    }


def _prune_operations() -> None:
    now = time.monotonic()
    expired = [
        operation_id
        for operation_id, record in _OPERATIONS.items()
        if record.get("expires_at_monotonic", now + 1) <= now
        and record.get("state") in {"completed", "failed"}
    ]
    for operation_id in expired:
        _OPERATIONS.pop(operation_id, None)
    if len(_OPERATIONS) <= _MAX_OPERATIONS:
        return
    completed = sorted(
        (
            (record.get("completed_at", ""), operation_id)
            for operation_id, record in _OPERATIONS.items()
            if record.get("state") in {"completed", "failed"}
        )
    )
    for _completed_at, operation_id in completed[: max(0, len(_OPERATIONS) - _MAX_OPERATIONS)]:
        _OPERATIONS.pop(operation_id, None)


def _entry_remote_state(entry: Dict[str, Any]) -> str:
    """Classify one completed result without assuming an unreported mutation outcome."""
    if entry.get("outcome_unknown") is True:
        return "unknown"

    command_sent = entry.get("command_sent")
    if command_sent is False:
        return "not_sent"
    if command_sent is True:
        return "confirmed" if entry.get("result_confirmed") is True else "unknown"

    if "remote_request_sent" in entry:
        remote_request_sent = entry.get("remote_request_sent")
        if remote_request_sent is False:
            return "not_sent"
        if remote_request_sent is None:
            return "unknown"
        if remote_request_sent is True:
            if entry.get("outcome_unknown") is False or entry.get("status") == "success":
                return "confirmed"
            return "unknown"

    if entry.get("result_confirmed") is True or entry.get("status") == "success":
        return "confirmed"
    if entry.get("retry_safe") is True:
        return "not_sent"
    return "unknown"


def _aggregate_remote_outcome(result: Any) -> Dict[str, Any]:
    """Summarize nested remote outcomes for safe operation-level decisions."""
    if not isinstance(result, dict):
        return {
            "remote_state": "unknown",
            "outcome_unknown": True,
            "retry_safe": False,
        }

    nested = result.get("results")
    entries = (
        [entry for entry in nested if isinstance(entry, dict)]
        if isinstance(nested, list)
        else []
    )
    if not entries:
        entries = [result]

    states = {_entry_remote_state(entry) for entry in entries}
    remote_state = next(iter(states)) if len(states) == 1 else "mixed"
    outcome_unknown = result.get("outcome_unknown") is True or "unknown" in states
    retry_safe = (
        not outcome_unknown
        and result.get("retry_safe") is not False
        and all(entry.get("retry_safe") is True for entry in entries)
    )
    return {
        "remote_state": remote_state,
        "outcome_unknown": outcome_unknown,
        "retry_safe": retry_safe,
    }


async def _run_operation(
    operation_id: str,
    runner: Callable[[], Awaitable[Dict[str, Any]]],
) -> None:
    record = _OPERATIONS[operation_id]
    record.update({"state": "running", "started_at": _utc_now()})
    try:
        result = await runner()
        record["result"] = result
        record["outcome_status"] = (
            result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        )
        record.update(_aggregate_remote_outcome(result))
        record["state"] = "completed"
    except asyncio.CancelledError:
        # Server shutdown may cancel the task. Never represent a possibly sent
        # fleet command as retry-safe merely because local execution stopped.
        record["state"] = "failed"
        record["outcome_status"] = "unknown"
        record["remote_state"] = "unknown"
        record["outcome_unknown"] = True
        record["retry_safe"] = False
        record["error"] = "Operation task was cancelled; remote state may be unknown"
        raise
    except Exception as exc:
        record["state"] = "failed"
        record["outcome_status"] = "unknown"
        record["remote_state"] = "unknown"
        record["outcome_unknown"] = True
        record["retry_safe"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"[-1000:]
    finally:
        record["completed_at"] = _utc_now()
        record["expires_at_monotonic"] = time.monotonic() + _operation_ttl()
        record.pop("task", None)


async def _start_operation(
    operation_type: str,
    summary: Dict[str, Any],
    private_arguments: Dict[str, Any],
    runner: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    async with _OPERATION_LOCK:
        _prune_operations()
        active = sum(record.get("state") in {"queued", "running"} for record in _OPERATIONS.values())
        if len(_OPERATIONS) >= _MAX_OPERATIONS and active >= _MAX_OPERATIONS:
            return {"status": "error", "message": "Too many active background operations"}
        operation_id = f"{_PROCESS_INSTANCE_ID}-{uuid.uuid4().hex}"
        record: Dict[str, Any] = {
            "status": "success",
            "operation_id": operation_id,
            "operation_type": operation_type,
            "state": "queued",
            "created_at": _utc_now(),
            "summary": summary,
            "storage": "volatile_process_memory",
            "restart_behavior": (
                "After MCP server restart this operation ID cannot prove remote completion; "
                "inspect the target before retrying any mutating command"
            ),
            "private_arguments": private_arguments,
            "expires_at_monotonic": time.monotonic() + _operation_ttl(),
        }
        _OPERATIONS[operation_id] = record
        record["task"] = asyncio.create_task(_run_operation(operation_id, runner))
        return _public_operation(record)


@mcp.tool(
    description=(
        "Start an explicitly authorized Dell SOL/HPE VSP fleet command in the background. "
        "Defaults to a no-connection dry run; execution needs dry_run=false and an exact command confirmation."
    )
)
async def start_console_command_batch(
    server_ids: List[str],
    command: str,
    timeout_seconds: Optional[float] = None,
    concurrency: Optional[int] = None,
    dry_run: bool = True,
    confirm_command: Optional[str] = None,
) -> Dict[str, Any]:
    if dry_run:
        return await run_console_command_batch(
            server_ids,
            command,
            timeout_seconds=timeout_seconds,
            concurrency=concurrency,
            dry_run=True,
        )
    if not isinstance(confirm_command, str) or not isinstance(command, str) or not secrets.compare_digest(
        confirm_command, command
    ):
        return {
            "status": "error",
            "phase": "validation",
            "message": "Execution requires confirm_command to exactly match command",
        }

    # This validation pass makes no network connection and avoids creating a
    # background operation for malformed arguments.
    validation = await run_console_command_batch(
        server_ids,
        command,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        dry_run=True,
    )
    if validation.get("phase") == "validation":
        return validation

    arguments = {
        "server_ids": list(server_ids),
        "command": command,
        "timeout_seconds": timeout_seconds,
        "concurrency": concurrency,
        "dry_run": False,
        "confirm_command": command,
    }

    async def run() -> Dict[str, Any]:
        return await run_console_command_batch(**arguments)

    return await _start_operation(
        "console_command_batch",
        {
            "server_ids": validation.get("server_ids", []),
            "host_count": validation.get("unique_count", 0),
            "command_length": len(command),
            "dry_run": False,
        },
        arguments,
        run,
    )


@mcp.tool(description="Start read-only console network collection and optional local persistence in the background.")
async def start_network_inventory_collection(
    server_ids: List[str],
    transport: str = "auto",
    save: bool = True,
    concurrency: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    arguments = {
        "server_ids": list(server_ids) if isinstance(server_ids, list) else server_ids,
        "transport": transport,
        "save": save,
        "concurrency": concurrency,
        "timeout_seconds": timeout_seconds,
    }

    async def run() -> Dict[str, Any]:
        return await collect_network_inventory(**arguments)

    return await _start_operation(
        "network_inventory_collection",
        {
            "host_count": len(server_ids) if isinstance(server_ids, list) else 0,
            "transport": transport,
            "save": save,
        },
        arguments,
        run,
    )


@mcp.tool(description="Start bounded Dell hardware XML export and manifest generation in the background.")
async def start_hardware_inventory_export(
    server_ids: List[str],
    collection: Optional[str] = None,
    refresh: bool = False,
    concurrency: Optional[int] = None,
    include_xml: bool = False,
    poll_interval_seconds: Optional[float] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    arguments = {
        "server_ids": list(server_ids) if isinstance(server_ids, list) else server_ids,
        "collection": collection,
        "refresh": refresh,
        "concurrency": concurrency,
        "include_xml": include_xml,
        "poll_interval_seconds": poll_interval_seconds,
        "timeout_seconds": timeout_seconds,
    }

    async def run() -> Dict[str, Any]:
        return await export_hardware_inventory_xml(**arguments)

    return await _start_operation(
        "hardware_inventory_export",
        {
            "host_count": len(server_ids) if isinstance(server_ids, list) else 0,
            "collection": collection,
            "refresh": refresh,
            "include_xml": include_xml,
        },
        arguments,
        run,
    )


@mcp.tool(description="Read current state and result for one background fleet operation.")
async def get_operation(operation_id: str) -> Dict[str, Any]:
    if not isinstance(operation_id, str) or not operation_id.strip():
        return {"status": "error", "message": "operation_id is required"}
    async with _OPERATION_LOCK:
        _prune_operations()
        record = _OPERATIONS.get(operation_id.strip())
        if not record:
            return {
                "status": "error",
                "state": "unknown",
                "remote_state": "unknown",
                "message": (
                    "Operation not found, expired, or created by another MCP process; "
                    "remote completion cannot be inferred and mutating work must not be blindly retried"
                ),
            }
        return _public_operation(record)


@mcp.tool(
    description=(
        "Start a new background attempt only for failed console hosts proven not to have received "
        "the prior command. Sent or ambiguous hosts are always excluded."
    )
)
async def retry_console_operation_failures(
    operation_id: str,
    confirm_command: str,
    concurrency: Optional[int] = None,
) -> Dict[str, Any]:
    async with _OPERATION_LOCK:
        _prune_operations()
        record = _OPERATIONS.get(operation_id)
        if not record:
            return {
                "status": "error",
                "state": "unknown",
                "remote_state": "unknown",
                "message": (
                    "Operation not found or expired; remote state is unknown and automatic retry is refused"
                ),
            }
        if record.get("operation_type") != "console_command_batch":
            return {"status": "error", "message": "Operation is not a console command batch"}
        if record.get("state") != "completed":
            return {"status": "error", "message": "Operation has not completed"}
        arguments = record.get("private_arguments", {})
        command = arguments.get("command")
        if not isinstance(command, str) or not secrets.compare_digest(confirm_command, command):
            return {"status": "error", "message": "confirm_command must exactly match the original command"}
        batch_result = record.get("result", {})
        eligible = [
            result.get("server_id")
            for result in batch_result.get("results", [])
            if result.get("status") != "success"
            and result.get("command_sent") is False
            and result.get("retry_safe") is True
            and result.get("server_id")
        ]
        excluded = [
            result.get("server_id")
            for result in batch_result.get("results", [])
            if result.get("status") != "success" and result.get("server_id") not in eligible
        ]
        timeout_seconds = arguments.get("timeout_seconds")

    if not eligible:
        return {
            "status": "error",
            "message": "No failed hosts are proven safe to retry",
            "excluded_sent_or_ambiguous": excluded,
        }
    started = await start_console_command_batch(
        eligible,
        command,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        dry_run=False,
        confirm_command=command,
    )
    if started.get("status") == "success":
        started["retried_from_operation_id"] = operation_id
        started["excluded_sent_or_ambiguous"] = excluded
    return started
