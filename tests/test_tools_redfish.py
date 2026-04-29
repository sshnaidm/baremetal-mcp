"""Tests for tools/redfish.py - low-level Redfish passthrough."""

from conftest import make_mock_response
from tools.redfish import redfish_call, parallel_redfish_call


class TestRedfishCall:
    async def test_get_passthrough(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await redfish_call("host1", "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert result["status"] == "success"
        assert result["data"]["Manufacturer"] == "Dell Inc."

    async def test_with_payload(self, setup_dell_config, dell_routes, mock_redfish_client):
        routes = dict(dell_routes)
        routes["/Actions/ComputerSystem.Reset"] = make_mock_response(204, content=b"")
        mock_redfish_client(routes)
        result = await redfish_call(
            "host1",
            "POST",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )
        assert result["status"] == "success"

    async def test_error(self, mock_redfish_client):
        mock_redfish_client({})
        result = await redfish_call("nonexistent", "GET", "/redfish/v1")
        assert result["status"] == "error"


class TestParallelRedfishCall:
    async def test_multiple_servers(self, setup_all_configs, dell_routes, hpe_routes, mock_redfish_client):
        routes = {**dell_routes, **hpe_routes}
        mock_redfish_client(routes)
        result = await parallel_redfish_call(["host1", "host100"], "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert len(result) == 2
        assert result[0]["server_id"] == "host1"
        assert result[1]["server_id"] == "host100"

    async def test_single_server(self, setup_dell_config, dell_routes, mock_redfish_client):
        mock_redfish_client(dell_routes)
        result = await parallel_redfish_call(["host1"], "GET", "/redfish/v1/Systems/System.Embedded.1")
        assert len(result) == 1
        assert result[0]["server_id"] == "host1"
