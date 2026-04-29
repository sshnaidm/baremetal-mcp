"""Tests for config.py - _normalize_boot_target, _flatten_dict, _load_config."""

import yaml
import pytest

from config import _normalize_boot_target, _flatten_dict, _load_config


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
    def test_loads_all_files(self, tmp_path, monkeypatch):
        servers_file = tmp_path / "servers.yaml"
        secrets_file = tmp_path / "secrets.yaml"
        isos_file = tmp_path / "isos.yaml"
        settings_file = tmp_path / "settings.yaml"

        servers_file.write_text(
            yaml.dump(
                {
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
        assert config.SECRETS["srv1"]["username"] == "root"
        assert "sw1" in config.SWITCHES
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
