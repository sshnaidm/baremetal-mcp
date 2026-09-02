"""Tests for sanitized host-operation preflight."""

from fastmcp import Client

import config
from tools.configuration import validate_host_configuration


async def test_validate_host_configuration_never_returns_secret_values():
    config.CONFIG.update(
        {
            "dell1": {
                "bmc_ip": "10.0.0.1",
                "redfish": {"port": 443},
                "serial_console": {"port": 22, "transport": "auto"},
                "verify_ssl": False,
                "vendor": "iDRAC",
                "credential_profile": "dell-lab",
                "vnc": {"port": 5901, "key_delay": 0.01},
            }
        }
    )
    config.SECRETS.update(
        {
            "profiles": {
                "dell-lab": {
                    "username": "root",
                    "password": "super-secret-value",
                    "vnc_password": "other-secret-value",
                }
            }
        }
    )

    result = await validate_host_configuration(["dell1"], ["redfish", "serial", "vnc", "hardware_xml"])

    assert result["status"] == "success"
    assert result["results"][0]["vendor"] == "dell"
    assert result["results"][0]["credential_fields_present"] == {
        "username": True,
        "password": True,
        "vnc_password": True,
    }
    assert "super-secret-value" not in repr(result)
    assert "other-secret-value" not in repr(result)


async def test_validate_host_configuration_reports_per_host_failures():
    config.CONFIG.update(
        {
            "broken": {
                "bmc_ip": "not a host",
                "vendor": "unknown",
                "vnc": {"port": 70000, "key_delay": 0.01},
                "host_mac": "bad-mac",
            },
            "hpe1": {
                "bmc_ip": "10.0.0.2",
                "vendor": "HP",
                "serial_console": {"port": 22, "transport": "auto"},
            },
        }
    )
    config.SECRETS["hpe1"] = {"username": "Administrator", "password": "placeholder"}

    result = await validate_host_configuration(["broken", "hpe1", "missing"], ["serial", "vnc"])

    assert result["status"] == "error"
    assert result["ready"] == []  # hpe1 lacks its requested VNC capability
    assert set(result["failed"]) == {"broken", "hpe1", "missing"}
    broken = result["results"][0]
    assert "bmc_ip is missing or invalid" in broken["errors"]
    assert any("unsupported vendor" in error for error in broken["errors"])
    assert any("vnc.port" in error for error in broken["errors"])
    assert any("invalid configured host MAC" in error for error in broken["errors"])
    assert result["results"][1]["vendor"] == "hpe"


async def test_serial_preflight_rejects_supermicro_and_custom_attach_commands():
    config.CONFIG.update(
        {
            "supermicro1": {
                "bmc_ip": "10.0.0.3",
                "vendor": "supermicro",
                "serial_console": {
                    "port": 22,
                    "transport": "auto",
                    "attach_command": "start /system1/console1",
                },
            },
            "dell-custom": {
                "bmc_ip": "10.0.0.4",
                "vendor": "dell",
                "serial_console": {
                    "port": 22,
                    "transport": "auto",
                    "attach_command": "custom console",
                },
            },
        }
    )
    config.SECRETS.update(
        {
            "supermicro1": {"username": "ADMIN", "password": "placeholder"},
            "dell-custom": {"username": "root", "password": "placeholder"},
        }
    )

    result = await validate_host_configuration(
        ["supermicro1", "dell-custom"],
        ["serial"],
    )

    assert result["status"] == "error"
    assert result["ready"] == []
    assert any("Dell SOL and HPE VSP only" in error for error in result["results"][0]["errors"])
    assert any("attach_command" in error for error in result["results"][1]["errors"])


async def test_hardware_xml_preflight_rejects_non_dell_vendor():
    config.CONFIG["hpe-xml"] = {
        "bmc_ip": "10.0.0.5",
        "redfish": {"port": 443},
        "verify_ssl": False,
        "vendor": "hpe",
    }
    config.SECRETS["hpe-xml"] = {"username": "Administrator", "password": "placeholder"}

    result = await validate_host_configuration(["hpe-xml"], ["hardware_xml"])

    assert result["status"] == "error"
    assert result["ready"] == []
    assert result["failed"] == ["hpe-xml"]
    assert any("unsupported" in error for error in result["results"][0]["errors"])


async def test_configuration_tool_registration():
    async with Client(config.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "validate_host_configuration" in names
