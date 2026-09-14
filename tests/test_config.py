"""Tests for config.py - logging, _normalize_boot_target, _flatten_dict, _load_config."""

import logging

from pathlib import Path

import yaml
import pytest

from config import _normalize_boot_target, _flatten_dict, _load_config


class TestRequestLogging:
    def test_default_request_log_path(self):
        import config

        assert config.DEFAULT_REQUEST_LOG_PATH == "/tmp/baremetal-mcp-requests.log"

    def test_file_error_falls_back_to_stderr(self, monkeypatch):
        import config

        def raise_file_error(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(config, "RotatingFileHandler", raise_file_error)

        handler = config._create_request_log_handler("/read-only/request.log")

        assert type(handler) is logging.StreamHandler


class TestNormalizeBootTarget:
    def test_empty_string(self):
        assert _normalize_boot_target("") is None

    def test_none_value(self):
        assert _normalize_boot_target(None) is None

    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("pxe", "Pxe"),
            ("network", "Pxe"),
            ("net", "Pxe"),
            ("cd", "Cd"),
            ("dvd", "Cd"),
            ("cdrom", "Cd"),
            ("iso", "Cd"),
            ("hdd", "Hdd"),
            ("disk", "Hdd"),
            ("localdisk", "Hdd"),
            ("usb", "Usb"),
        ],
    )
    def test_known_alias(self, alias, expected):
        assert _normalize_boot_target(alias) == expected

    def test_alias_case_insensitive(self):
        assert _normalize_boot_target("PXE") == "Pxe"
        assert _normalize_boot_target("DVD") == "Cd"

    def test_exact_redfish_enum_uppercase_first(self):
        assert _normalize_boot_target("Cd") == "Cd"
        assert _normalize_boot_target("Pxe") == "Pxe"
        assert _normalize_boot_target("BiosSetup") == "BiosSetup"

    def test_unknown_lowercase_returns_none(self):
        assert _normalize_boot_target("foobar") is None

    def test_whitespace_stripped(self):
        assert _normalize_boot_target("  pxe  ") == "Pxe"


class TestFlattenDict:
    def test_empty_dict(self):
        assert _flatten_dict({}) == {}

    def test_nested_dicts_with_string_leaves(self):
        data = {"a": {"b": "http://url1", "c": "http://url2"}}
        result = _flatten_dict(data)
        assert result == {"a_b": "http://url1", "a_c": "http://url2"}

    def test_list_of_dicts(self):
        data = [{"model_750": {"idrac": "http://idrac.exe"}}]
        result = _flatten_dict(data)
        assert result == {"model_750_idrac": "http://idrac.exe"}

    def test_string_with_prefix(self):
        result = _flatten_dict("http://url", prefix="dell_bios")
        assert result == {"dell_bios": "http://url"}

    def test_string_without_prefix(self):
        result = _flatten_dict("http://url")
        assert result == {}

    def test_mixed_nesting_like_isos(self):
        data = {
            "dell": [
                {
                    "model_750": {
                        "idrac_version": {"7": "http://fw/idrac7.exe"},
                        "bios_version": {"1": "http://fw/bios1.exe"},
                    }
                }
            ]
        }
        result = _flatten_dict(data)
        assert "dell_model_750_idrac_version_7" in result
        assert result["dell_model_750_idrac_version_7"] == "http://fw/idrac7.exe"
        assert result["dell_model_750_bios_version_1"] == "http://fw/bios1.exe"

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": "leaf"}}}}
        assert _flatten_dict(data) == {"a_b_c_d": "leaf"}


class TestLoadConfig:
    def test_example_files_define_all_connection_values(self, monkeypatch, tmp_path):
        import config

        repository = Path(__file__).resolve().parents[1]
        monkeypatch.setattr(
            config,
            "CONFIG_FILE",
            str(repository / "redfish_servers.example.yaml"),
        )
        monkeypatch.setattr(
            config,
            "SECRETS_FILE",
            str(repository / "redfish_secrets.example.yaml"),
        )
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "missing-isos.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "missing-settings.yaml"))

        _load_config()

        assert config.CONFIG
        assert config.SWITCHES
        for server_id, server in config.CONFIG.items():
            assert server["redfish"]["port"] == 443
            assert server["serial_console"]["port"] == 22
            assert server["serial_console"]["transport"] == "auto"
            assert isinstance(server["verify_ssl"], bool)
            credentials = config.get_server_credentials(server_id)
            assert credentials["username"]
            assert credentials["password"]
        for switch_id, switch in config.SWITCHES.items():
            assert switch["port"] == 22
            assert config.SECRETS[switch_id]["username"]
            assert config.SECRETS[switch_id]["password"]
        assert config.CONFIG["dell-db-02"]["vnc_port"] == 5901
        assert config.get_server_credentials("dell-db-02")["vnc_password"]

    def test_loads_all_files(self, tmp_path, monkeypatch):
        servers_file = tmp_path / "servers.yaml"
        secrets_file = tmp_path / "secrets.yaml"
        isos_file = tmp_path / "isos.yaml"
        settings_file = tmp_path / "settings.yaml"

        servers_file.write_text(
            yaml.dump(
                {
                    "server_defaults": {
                        "redfish": {"port": 443},
                        "serial_console": {"port": 22, "transport": "auto"},
                        "verify_ssl": False,
                    },
                    "switch_defaults": {"port": 22},
                    "servers": {"srv1": {"bmc_ip": "10.0.0.1", "vendor": "dell"}},
                    "switches": {"sw1": {"hostname": "10.0.0.2"}},
                }
            )
        )
        secrets_file.write_text(yaml.dump({"srv1": {"username": "root", "password": "pass"}}))
        isos_file.write_text(yaml.dump({"dell": [{"model": "http://url"}]}))
        settings_file.write_text(yaml.dump({"default_timeout": 120, "max_retries": 5}))

        import config

        monkeypatch.setattr(config, "CONFIG_FILE", str(servers_file))
        monkeypatch.setattr(config, "SECRETS_FILE", str(secrets_file))
        monkeypatch.setattr(config, "ISOS_FILE", str(isos_file))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

        _load_config()

        assert "srv1" in config.CONFIG
        assert config.CONFIG["srv1"]["bmc_ip"] == "10.0.0.1"
        assert config.CONFIG["srv1"]["redfish"]["port"] == 443
        assert config.CONFIG["srv1"]["serial_console"] == {
            "port": 22,
            "transport": "auto",
        }
        assert config.CONFIG["srv1"]["verify_ssl"] is False
        assert config.SECRETS["srv1"]["username"] == "root"
        assert "sw1" in config.SWITCHES
        assert config.SWITCHES["sw1"]["port"] == 22
        assert config.DEFAULT_TIMEOUT == 120
        assert config.MAX_RETRIES == 5

    def test_missing_config_file(self, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "nonexistent.yaml"))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "nonexistent2.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "nonexistent3.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "nonexistent4.yaml"))

        _load_config()
        assert config.CONFIG == {}
        assert config.SECRETS == {}

    def test_skip_loading_when_already_populated(self, monkeypatch):
        import config

        config.CONFIG["existing"] = {"bmc_ip": "1.2.3.4"}
        config.SECRETS["existing"] = {"username": "u"}
        config.SETTINGS["key"] = "val"
        config.ISOS["key"] = "val"

        _load_config()
        assert "existing" in config.CONFIG

    def test_config_without_servers_key(self, tmp_path, monkeypatch):
        servers_file = tmp_path / "servers.yaml"
        servers_file.write_text(yaml.dump({"srv1": {"bmc_ip": "10.0.0.1"}}))

        import config

        monkeypatch.setattr(config, "CONFIG_FILE", str(servers_file))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "none.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "none2.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "none3.yaml"))

        _load_config()
        assert "srv1" in config.CONFIG

    def test_invalid_config_format(self, tmp_path, monkeypatch):
        servers_file = tmp_path / "servers.yaml"
        servers_file.write_text(yaml.dump({"servers": ["just", "a", "list"]}))

        import config

        monkeypatch.setattr(config, "CONFIG_FILE", str(servers_file))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "none.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "none2.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "none3.yaml"))

        _load_config()
        assert config.CONFIG == {}

    def test_settings_override_defaults(self, tmp_path, monkeypatch):
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump(
                {
                    "default_timeout": 90,
                    "max_retries": 7,
                    "backoff_factor": 1.5,
                    "cache_ttl_firmware_inventory": 3600,
                    "cache_ttl_hardware_overview": 7200,
                    "cache_ttl_system_info": 900,
                    "cache_ttl_disk_cache": 43200,
                    "ssh_timeout": 30,
                    "ssh_command_timeout": 120,
                    "vnc_capture_timeout": 45,
                    "sol_connect_timeout": 12,
                    "console_command_timeout": 180,
                    "console_batch_concurrency": 8,
                    "network_collection_concurrency": 7,
                    "hardware_inventory_timeout": 240,
                    "hardware_inventory_max_bytes": 1048576,
                    "operation_ttl": 7200,
                }
            )
        )

        import config

        monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "none.yaml"))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "none2.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "none3.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

        _load_config()

        assert config.DEFAULT_TIMEOUT == 90
        assert config.MAX_RETRIES == 7
        assert config.BACKOFF_FACTOR == 1.5
        assert config.SSH_TIMEOUT == 30
        assert config.SSH_COMMAND_TIMEOUT == 120
        assert config.VNC_CAPTURE_TIMEOUT == 45
        assert config.SOL_CONNECT_TIMEOUT == 12
        assert config.CONSOLE_COMMAND_TIMEOUT == 180
        assert config.CONSOLE_BATCH_CONCURRENCY == 8
        assert config.NETWORK_COLLECTION_CONCURRENCY == 7
        assert config.HARDWARE_INVENTORY_TIMEOUT == 240
        assert config.HARDWARE_INVENTORY_MAX_BYTES == 1048576
        assert config.OPERATION_TTL == 7200

    def test_invalid_numeric_settings_keep_safe_defaults(self, tmp_path, monkeypatch):
        import config

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.safe_dump(
                {
                    "console_batch_concurrency": 13,
                    "network_collection_concurrency": 13,
                    "batch_concurrency": 13,
                    "console_output_limit": 1_048_577,
                    "hardware_inventory_timeout": 999999,
                    "hardware_inventory_max_bytes": 268_435_457,
                }
            )
        )
        monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "none.yaml"))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "none2.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "none3.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))
        monkeypatch.setattr(config, "CONSOLE_BATCH_CONCURRENCY", 6)
        monkeypatch.setattr(config, "NETWORK_COLLECTION_CONCURRENCY", 6)
        monkeypatch.setattr(config, "BATCH_CONCURRENCY", 6)
        monkeypatch.setattr(config, "CONSOLE_OUTPUT_LIMIT", 65536)
        monkeypatch.setattr(config, "HARDWARE_INVENTORY_TIMEOUT", 300)
        monkeypatch.setattr(config, "HARDWARE_INVENTORY_MAX_BYTES", 52_428_800)

        _load_config()

        assert config.CONSOLE_BATCH_CONCURRENCY == 6
        assert config.NETWORK_COLLECTION_CONCURRENCY == 6
        assert config.BATCH_CONCURRENCY == 6
        assert config.CONSOLE_OUTPUT_LIMIT == 65536
        assert config.HARDWARE_INVENTORY_TIMEOUT == 300
        assert config.HARDWARE_INVENTORY_MAX_BYTES == 52_428_800

    def test_vendor_alias_is_normalized_while_loading(self, tmp_path, monkeypatch):
        import config

        servers_file = tmp_path / "servers.yaml"
        servers_file.write_text(yaml.safe_dump({"servers": {"hpe1": {"bmc_ip": "10.0.0.1", "vendor": "HP"}}}))
        monkeypatch.setattr(config, "CONFIG_FILE", str(servers_file))
        monkeypatch.setattr(config, "SECRETS_FILE", str(tmp_path / "none.yaml"))
        monkeypatch.setattr(config, "ISOS_FILE", str(tmp_path / "none2.yaml"))
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "none3.yaml"))

        _load_config()

        assert config.CONFIG["hpe1"]["vendor"] == "hpe"
