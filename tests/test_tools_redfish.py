"""Tests for tools/redfish.py - guarded low-level Redfish passthrough."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from conftest import make_mock_response

from tools.redfish import parallel_redfish_call, redfish_call


class TestRedfishCall:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    async def test_read_methods_execute_without_confirmation(
        self, monkeypatch: pytest.MonkeyPatch, method: str
    ) -> None:
        call = AsyncMock(return_value={"status": "success"})
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call("host1", method, "/redfish/v1")

        assert result["status"] == "success"
        call.assert_awaited_once_with("host1", method, "/redfish/v1", None)

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    async def test_all_mutating_methods_default_to_no_network(
        self, monkeypatch: pytest.MonkeyPatch, method: str
    ) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call("host1", method, "/redfish/v1/Systems/1")

        assert result["status"] == "error"
        assert result["remote_request_sent"] is False
        assert result["retry_safe"] is True
        assert result["outcome_unknown"] is False
        call.assert_not_awaited()

    async def test_get_passthrough(
        self,
        setup_dell_config: None,
        dell_routes: dict[str, httpx.Response],
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
    ) -> None:
        mock_redfish_client(dell_routes)
        result = await redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert result["data"]["Manufacturer"] == "Dell Inc."

    async def test_mutation_is_refused_without_exact_confirmation(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call(
            "host1",
            "POST",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )

        assert result["status"] == "error"
        assert result["phase"] == "confirmation"
        assert result["dry_run"] is True
        assert result["remote_request_sent"] is False
        assert result["retry_safe"] is True
        assert result["outcome_unknown"] is False
        assert result["required_confirmation"] == (
            "POST /redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
        )
        call.assert_not_awaited()

    async def test_mutation_with_exact_confirmation(
        self,
        setup_dell_config: None,
        dell_routes: dict[str, httpx.Response],
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
    ) -> None:
        routes = dict(dell_routes)
        routes["/Actions/ComputerSystem.Reset"] = make_mock_response(204, content=b"")
        mock_redfish_client(routes)
        path = "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
        result = await redfish_call(
            "host1",
            "POST",
            path,
            {"ResetType": "GracefulRestart"},
            dry_run=False,
            confirm_method_path=f"POST {path}",
        )
        assert result["status"] == "success"

    async def test_confirmation_alone_does_not_disable_default_dry_run(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)
        path = "/redfish/v1/Systems/1"

        result = await redfish_call(
            "host1",
            "PATCH",
            path,
            {"AssetTag": "rack-a"},
            confirm_method_path=f"PATCH {path}",
        )

        assert result["status"] == "error"
        assert result["requested_dry_run"] is True
        call.assert_not_awaited()

    async def test_confirmation_binds_normalized_path_exactly(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call(
            "host1",
            "delete",
            "redfish/v1/Managers/1/LogServices/Log/Entries/1",
            confirm_method_path="delete /redfish/v1/Managers/1/LogServices/Log/Entries/1",
        )

        assert result["status"] == "error"
        assert result["required_confirmation"] == ("DELETE /redfish/v1/Managers/1/LogServices/Log/Entries/1")
        call.assert_not_awaited()

    async def test_unsupported_method_is_rejected(
        self, setup_dell_config: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call("host1", "TRACE", "/redfish/v1")

        assert result["status"] == "error"
        assert result["phase"] == "validation"
        call.assert_not_awaited()

    async def test_payload_must_be_a_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await redfish_call(
            "host1",
            "PATCH",
            "/redfish/v1/Systems/1",
            ["not", "an", "object"],
            dry_run=False,
            confirm_method_path="PATCH /redfish/v1/Systems/1",
        )

        assert result["status"] == "error"
        assert "JSON object" in result["message"]
        assert result["remote_request_sent"] is False
        assert result["retry_safe"] is True
        assert result["outcome_unknown"] is False
        call.assert_not_awaited()

    async def test_ambiguous_mutation_error_is_never_marked_retry_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call = AsyncMock(return_value={"status": "error", "message": "connection lost"})
        monkeypatch.setattr("tools.redfish._redfish_call", call)
        path = "/redfish/v1/Systems/1"

        result = await redfish_call(
            "host1",
            "PATCH",
            path,
            {"AssetTag": "rack-a"},
            dry_run=False,
            confirm_method_path=f"PATCH {path}",
        )

        assert result["remote_request_sent"] is None
        assert result["retry_safe"] is False
        assert result["outcome_unknown"] is True

    async def test_error(self, mock_redfish_client: Callable[[dict[str, Any]], MagicMock]) -> None:
        mock_redfish_client({})
        result = await redfish_call("nonexistent", "GET", "/redfish/v1")
        assert result["status"] == "error"


class TestParallelRedfishCall:
    async def test_multiple_servers(
        self,
        setup_all_configs: None,
        dell_routes: dict[str, httpx.Response],
        hpe_routes: dict[str, httpx.Response],
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
    ) -> None:
        routes = {**dell_routes, **hpe_routes}
        mock_redfish_client(routes)
        result = await parallel_redfish_call(["host1", "host100"], "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert len(result) == 2
        assert result[0]["server_id"] == "host1"
        assert result[1]["server_id"] == "host100"

    async def test_single_server(
        self,
        setup_dell_config: None,
        dell_routes: dict[str, httpx.Response],
        mock_redfish_client: Callable[[dict[str, Any]], MagicMock],
    ) -> None:
        mock_redfish_client(dell_routes)
        result = await parallel_redfish_call(["host1"], "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert len(result) == 1
        assert result[0]["server_id"] == "host1"

    async def test_deduplicates_and_bounds_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        active = 0
        maximum_active = 0
        calls = []

        async def fake_call(server_id: str, method: str, path: str, payload: object) -> dict[str, Any]:
            nonlocal active, maximum_active
            calls.append(server_id)
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            return {"status": "success"}

        monkeypatch.setattr("tools.redfish._redfish_call", fake_call)

        result = await parallel_redfish_call(
            ["host1", "host1", "host2", "host3"],
            "GET",
            "/redfish/v1",
            concurrency=2,
        )

        assert calls == ["host1", "host2", "host3"]
        assert [item["server_id"] for item in result] == ["host1", "host2", "host3"]
        assert maximum_active <= 2

    async def test_parallel_mutation_refused_for_every_unique_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        result = await parallel_redfish_call(
            ["host1", "host1", "host2"],
            "PATCH",
            "/redfish/v1/Systems/1",
            {"AssetTag": "rack-a"},
        )

        assert [item["server_id"] for item in result] == ["host1", "host2"]
        assert all(item["phase"] == "confirmation" for item in result)
        assert all(item["remote_request_sent"] is False for item in result)
        call.assert_not_awaited()

    async def test_parallel_batch_and_concurrency_are_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call = AsyncMock()
        monkeypatch.setattr("tools.redfish._redfish_call", call)

        too_many = await parallel_redfish_call([f"host{i}" for i in range(65)], "GET", "/redfish/v1")
        invalid_concurrency = await parallel_redfish_call(["host1"], "GET", "/redfish/v1", concurrency=13)

        assert too_many[0]["status"] == "error"
        assert "At most 64" in too_many[0]["message"]
        assert invalid_concurrency[0]["status"] == "error"
        assert "between 1 and 12" in invalid_concurrency[0]["message"]
        call.assert_not_awaited()
