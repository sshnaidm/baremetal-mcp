"""Tests for background fleet operations and retry safety."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp import Client

import config
from tools.operations import (
    _OPERATIONS,
    get_operation,
    retry_console_operation_failures,
    start_console_command_batch,
    start_network_inventory_collection,
)


@pytest.fixture(autouse=True)
def _clear_operations() -> Iterator[None]:
    _OPERATIONS.clear()
    yield
    for record in _OPERATIONS.values():
        task = record.get("task")
        if task and not task.done():
            task.cancel()
    _OPERATIONS.clear()


async def _completed(operation_id: str) -> dict[str, Any]:
    for _ in range(100):
        value = await get_operation(operation_id)
        if value.get("state") in {"completed", "failed"}:
            return value
        await asyncio.sleep(0)
    raise AssertionError("background operation did not complete")


async def test_console_background_requires_exact_confirmation_and_hides_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_batch(server_ids: list[str], command: str, **kwargs: object) -> dict[str, Any]:
        calls.append((server_ids, command, kwargs))
        if kwargs.get("dry_run"):
            return {
                "status": "success",
                "phase": "dry-run",
                "server_ids": server_ids,
                "unique_count": len(server_ids),
                "results": [],
            }
        return {
            "status": "success",
            "phase": "complete",
            "results": [
                {
                    "server_id": server_id,
                    "status": "success",
                    "command_sent": True,
                    "result_confirmed": True,
                    "retry_safe": False,
                }
                for server_id in server_ids
            ],
        }

    monkeypatch.setattr("tools.operations.run_console_command_batch", fake_batch)
    rejected = await start_console_command_batch(
        ["host1"], "systemctl restart NetworkManager", dry_run=False, confirm_command="wrong"
    )
    assert rejected["status"] == "error"
    assert calls == []

    started = await start_console_command_batch(
        ["host1"],
        "systemctl restart NetworkManager",
        dry_run=False,
        confirm_command="systemctl restart NetworkManager",
    )
    assert started["state"] == "queued"
    assert "systemctl restart" not in repr(started)
    done = await _completed(started["operation_id"])
    assert done["state"] == "completed"
    assert done["outcome_status"] == "success"
    assert done["remote_state"] == "confirmed"
    assert done["outcome_unknown"] is False
    assert done["retry_safe"] is False
    assert done["storage"] == "volatile_process_memory"
    assert done["result"]["status"] == "success"


@pytest.mark.parametrize(
    ("host_result", "remote_state", "outcome_unknown", "retry_safe"),
    [
        (
            {
                "server_id": "host1",
                "status": "error",
                "command_sent": False,
                "result_confirmed": False,
                "retry_safe": True,
            },
            "not_sent",
            False,
            True,
        ),
        (
            {
                "server_id": "host1",
                "status": "error",
                "command_sent": True,
                "result_confirmed": False,
                "retry_safe": False,
            },
            "unknown",
            True,
            False,
        ),
    ],
)
async def test_console_background_aggregates_remote_outcome(
    monkeypatch: pytest.MonkeyPatch,
    host_result: dict[str, Any],
    remote_state: str,
    outcome_unknown: bool,
    retry_safe: bool,
) -> None:
    async def fake_batch(server_ids: list[str], command: str, **kwargs: object) -> dict[str, Any]:
        if kwargs.get("dry_run"):
            return {
                "status": "success",
                "phase": "dry-run",
                "server_ids": server_ids,
                "unique_count": len(server_ids),
                "results": [],
            }
        return {"status": "error", "phase": "complete", "results": [host_result]}

    monkeypatch.setattr("tools.operations.run_console_command_batch", fake_batch)
    started = await start_console_command_batch(["host1"], "true", dry_run=False, confirm_command="true")
    done = await _completed(started["operation_id"])

    assert done["remote_state"] == remote_state
    assert done["outcome_unknown"] is outcome_unknown
    assert done["retry_safe"] is retry_safe


async def test_retry_excludes_sent_or_ambiguous_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def fake_batch(server_ids: list[str], command: str, **kwargs: object) -> dict[str, Any]:
        nonlocal attempts
        if kwargs.get("dry_run"):
            return {
                "status": "success",
                "phase": "dry-run",
                "server_ids": server_ids,
                "unique_count": len(server_ids),
                "results": [],
            }
        attempts += 1
        if attempts == 1:
            return {
                "status": "error",
                "results": [
                    {
                        "server_id": "not-sent",
                        "status": "error",
                        "command_sent": False,
                        "retry_safe": True,
                    },
                    {
                        "server_id": "ambiguous",
                        "status": "error",
                        "command_sent": True,
                        "retry_safe": False,
                    },
                ],
            }
        assert server_ids == ["not-sent"]
        return {"status": "success", "results": []}

    monkeypatch.setattr("tools.operations.run_console_command_batch", fake_batch)
    started = await start_console_command_batch(
        ["not-sent", "ambiguous"], "true", dry_run=False, confirm_command="true"
    )
    done = await _completed(started["operation_id"])
    assert done["remote_state"] == "mixed"
    assert done["outcome_unknown"] is True
    assert done["retry_safe"] is False
    retried = await retry_console_operation_failures(started["operation_id"], "true")
    assert retried["status"] == "success"
    assert retried["excluded_sent_or_ambiguous"] == ["ambiguous"]
    await _completed(retried["operation_id"])


async def test_network_collection_runs_in_background(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_collect(**arguments: object) -> dict[str, Any]:
        assert arguments["save"] is True
        return {"status": "success", "collected": 2, "saved": 2}

    monkeypatch.setattr("tools.operations.collect_network_inventory", fake_collect)
    started = await start_network_inventory_collection(["host1", "host2"])
    done = await _completed(started["operation_id"])
    assert done["result"]["saved"] == 2


async def test_operation_tools_are_registered() -> None:
    async with Client(config.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert {
        "start_console_command_batch",
        "start_network_inventory_collection",
        "start_hardware_inventory_export",
        "get_operation",
        "retry_console_operation_failures",
    } <= names


async def test_unknown_operation_refuses_to_imply_retry_safety() -> None:
    result = await get_operation("another-process-deadbeef")

    assert result["status"] == "error"
    assert result["state"] == "unknown"
    assert result["remote_state"] == "unknown"
    assert "blindly retried" in result["message"]
