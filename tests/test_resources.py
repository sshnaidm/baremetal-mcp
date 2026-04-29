"""Tests for resources.py - MCP resource functions."""

import pytest

from resources import (
    get_all_hosts,
    get_host_details,
    get_multiple_host_details,
    get_all_hosts_from_lab,
    get_all_hosts_for_tag,
)


class TestGetAllHosts:
    def test_returns_config(self, setup_all_configs):
        result = get_all_hosts()
        assert "host1" in result
        assert "host100" in result

    def test_empty_config(self):
        result = get_all_hosts()
        assert result == {}


class TestGetHostDetails:
    def test_found(self, setup_all_configs):
        result = get_host_details("host1")
        assert result["bmc_ip"] == "10.0.0.1"

    def test_not_found(self, setup_all_configs):
        with pytest.raises(ValueError, match="not found"):
            get_host_details("nonexistent")


class TestGetMultipleHostDetails:
    def test_all_found(self, setup_all_configs):
        result = get_multiple_host_details("host1,host100")
        assert len(result) == 2
        assert result[0]["bmc_ip"] == "10.0.0.1"
        assert result[1]["bmc_ip"] == "10.0.0.100"

    def test_unknown_raises(self, setup_all_configs):
        with pytest.raises(ValueError, match="not found"):
            get_multiple_host_details("host1,nonexistent")

    def test_whitespace_handling(self, setup_all_configs):
        result = get_multiple_host_details("host1 , host100")
        assert len(result) == 2


class TestGetAllHostsFromLab:
    def test_found(self, setup_all_configs):
        result = get_all_hosts_from_lab("labA")
        assert len(result) >= 1
        for host in result:
            assert host.get("lab") == "labA"

    def test_not_found(self, setup_all_configs):
        result = get_all_hosts_from_lab("nonexistent_lab")
        assert len(result) == 1
        assert "No servers found" in result[0]["message"]


class TestGetAllHostsForTag:
    def test_found(self, setup_all_configs):
        result = get_all_hosts_for_tag("gpu")
        assert len(result) >= 1

    def test_not_found(self, setup_all_configs):
        result = get_all_hosts_for_tag("nonexistent_tag")
        assert len(result) == 1
        assert "No servers found" in result[0]["message"]

    def test_host_with_no_tags(self):
        import config

        config.CONFIG["notags"] = {"bmc_ip": "10.0.0.99"}
        result = get_all_hosts_for_tag("gpu")
        assert len(result) == 1
        assert "No servers found" in result[0]["message"]
