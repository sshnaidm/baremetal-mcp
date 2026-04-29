"""Tests for tools/hosts.py - host listing and filtering."""

from tools.hosts import (
    _check_config,
    list_hosts,
    get_host,
    get_hosts,
    list_hosts_by_lab,
    list_hosts_by_tag,
    list_switches,
)


class TestCheckConfig:
    def test_config_loaded(self, setup_all_configs):
        result = _check_config()
        assert result == {}

    def test_config_missing(self, monkeypatch):
        import config
        import tools.hosts

        monkeypatch.setattr(config, "CONFIG_FILE", "/nonexistent/path.yaml")
        monkeypatch.setattr(tools.hosts, "CONFIG_FILE", "/nonexistent/path.yaml")
        result = _check_config()
        assert result.get("status") == "error"


class TestListHosts:
    def test_returns_all(self, setup_all_configs):
        result = list_hosts()
        assert result["status"] == "success"
        assert "host1" in result["data"]
        assert "host100" in result["data"]
        assert "host500" in result["data"]


class TestGetHost:
    def test_found(self, setup_all_configs):
        result = get_host("host1")
        assert result["status"] == "success"
        assert result["data"]["bmc_ip"] == "10.0.0.1"

    def test_not_found(self, setup_all_configs):
        result = get_host("nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["message"]


class TestGetHosts:
    def test_all_found(self, setup_all_configs):
        result = get_hosts(["host1", "host100"])
        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["missing"] == []

    def test_some_missing(self, setup_all_configs):
        result = get_hosts(["host1", "nonexistent"])
        assert result["status"] == "success"
        assert "host1" in result["data"]
        assert "nonexistent" in result["missing"]

    def test_all_missing(self, setup_all_configs):
        result = get_hosts(["nope1", "nope2"])
        assert len(result["data"]) == 0
        assert len(result["missing"]) == 2


class TestListHostsByLab:
    def test_match(self, setup_all_configs):
        result = list_hosts_by_lab("labA")
        assert result["status"] == "success"
        assert "host1" in result["data"]

    def test_no_match(self, setup_all_configs):
        result = list_hosts_by_lab("nonexistent")
        assert result["status"] == "success"
        assert result["data"] == {}


class TestListHostsByTag:
    def test_match(self, setup_all_configs):
        result = list_hosts_by_tag("gpu")
        assert result["status"] == "success"
        assert len(result["data"]) >= 1

    def test_no_match(self, setup_all_configs):
        result = list_hosts_by_tag("nonexistent")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_host_with_no_tags_key(self):
        import config

        config.CONFIG["notags"] = {"bmc_ip": "10.0.0.99"}
        result = list_hosts_by_tag("gpu")
        assert result["status"] == "success"
        assert "notags" not in result["data"]


class TestListSwitches:
    def test_populated(self, setup_all_configs):
        result = list_switches()
        assert result["status"] == "success"
        assert "lab1-switch" in result["data"]

    def test_empty(self):
        import config

        config.CONFIG["dummy"] = {"bmc_ip": "1.2.3.4"}
        result = list_switches()
        assert result["status"] == "success"
        assert result["data"] == {}
