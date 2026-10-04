"""Tests for end-to-end console network collection and identity guards."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

import config
from tools.network_collect import collect_network_inventory

LINK = (
    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default "
    "qlen 1000    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
    "2: eno1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP mode DEFAULT group default "
    "qlen 1000    link/ether aa:bb:cc:dd:ee:01 brd ff:ff:ff:ff:ff:ff\n"
    "3: eno2: <BROADCAST,MULTICAST> mtu 1500 qdisc mq state DOWN mode DEFAULT group default qlen 1000    "
    "link/ether aa:bb:cc:dd:ee:02 brd ff:ff:ff:ff:ff:ff\n"
    "4: bond0: <BROADCAST,MULTICAST> mtu 1500 qdisc noqueue state DOWN mode DEFAULT group default "
    "qlen 1000    link/ether aa:bb:cc:dd:ee:01 brd ff:ff:ff:ff:ff:ff\n"
)

SYSFS = """\
SYSFS\teno1\t1\tup\tp0\t0\t0000:31:00.0
SYSFS\teno2\t0\tdown\tp1\t1\t0000:31:00.1
"""

PCI = """\
0000:31:00.0 Ethernet controller [0200]: Intel Corporation Ethernet Controller E810-XXV for SFP [8086:159b] (rev 02)
0000:31:00.1 Ethernet controller [0200]: Intel Corporation Ethernet Controller E810-XXV for SFP [8086:159b] (rev 02)
"""

DETAIL_1 = """\
driver: ice
firmware-version: 2.33
bus-info: 0000:31:00.0
Speed: 25000Mb/s
Duplex: Full
Port: FIBRE
Link detected: yes
PHYS_PORT: p0
DEV_PORT: 0
"""

DETAIL_2 = """\
driver: ice
firmware-version: 2.33
bus-info: 0000:31:00.1
Speed: Unknown!
Duplex: Unknown!
Port: Other
Link detected: no
PHYS_PORT: p1
DEV_PORT: 1
"""


def _session(commands: list[str], transport: str = "idrac-ssh-sol", status: str = "success") -> dict[str, Any]:
    items = []
    for label, output in commands:
        items.append(
            {
                "label": label,
                "status": "success",
                "transport": transport,
                "command_sent": True,
                "result_confirmed": True,
                "retry_safe": False,
                "exit_code": 0,
                "output": output,
                "truncated": False,
            }
        )
    return {
        "status": status,
        "transport": transport,
        "phase": "complete",
        "command_sent": True,
        "retry_safe": False,
        "commands": items,
    }


def _configure() -> None:
    config.CONFIG["host1"] = {
        "bmc_ip": "10.0.0.1",
        "vendor": "dell",
        "expected_host_macs": ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"],
        "serial_number": "ABC123",
        "vnc_port": 5901,
    }


async def test_collect_parses_short_probes_verifies_identity_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()
    calls = 0

    async def fake_serial(server_id: str, commands: list[str], timeout: float) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert [label for label, _ in commands] == ["link", "sysfs", "pci", "system", "lldp"]
            return _session(
                [
                    ("link", LINK),
                    ("sysfs", SYSFS),
                    ("pci", PCI),
                    ("system", "HOSTNAME\tlive\nSERIAL\tABC123\nKERNEL\tLinux 6.12 x86_64\n"),
                    (
                        "lldp",
                        "lldp.eno1.1.chassis.name=leaf01\nlldp.eno1.1.port.id=Ethernet1/1\n",
                    ),
                ]
            )
        assert commands[-2:] == [
            ("address_final", "ip -o address show"),
            ("route_final", "ip -o route show table all"),
        ]
        return _session(
            [
                ("detail_0", DETAIL_1),
                ("detail_1", DETAIL_2),
                ("address_final", "2: eno1    inet 192.0.2.10/24 scope global eno1\n"),
                ("route_final", "default via 192.0.2.1 dev eno1\n"),
            ]
        )

    async def fake_identity(server_id: str) -> dict[str, Any]:
        return {"status": "success", "serial_number": "ABC123", "model": "PowerEdge"}

    saved = {}

    async def fake_save(server_id: str, interfaces: list[dict[str, Any]], **kwargs: object) -> dict[str, Any]:
        saved.update({"server_id": server_id, "interfaces": interfaces, **kwargs})
        return {"status": "success", "server_id": server_id, "path": "/tmp/host1.yaml"}

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)
    monkeypatch.setattr("tools.network_collect._redfish_system_identity", fake_identity)
    monkeypatch.setattr("tools.network_collect.save_network_inventory", fake_save)

    result = await collect_network_inventory(["host1", "host1"], save=True)

    assert result["status"] == "success"
    assert result["duplicates_removed"] == 1
    host = result["results"][0]
    assert host["identity"]["status"] == "verified"
    assert host["summary"] == {"interface_count": 2, "links_up": 1, "address_count": 1}
    assert host["interfaces"][0]["vendor"] == "Intel Corporation"
    assert host["interfaces"][0]["model"].startswith("Ethernet Controller E810")
    assert host["interfaces"][0]["link"]["speed_mbps"] == 25000
    assert host["interfaces"][0]["neighbor"]["port_id"] == "Ethernet1/1"
    assert saved["interfaces"][0]["addresses"][0]["address"] == "192.0.2.10/24"
    assert saved["routes"] == [{"raw": "default via 192.0.2.1 dev eno1"}]


async def test_identity_mismatch_is_not_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()
    calls = 0

    async def fake_serial(*args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _session(
                [
                    ("link", LINK),
                    ("sysfs", SYSFS),
                    ("pci", PCI),
                    ("system", "HOSTNAME\tlive\nSERIAL\tWRONG\nKERNEL\tLinux\n"),
                    ("lldp", "LLDP_UNAVAILABLE\n"),
                ]
            )
        return _session(
            [
                ("detail_0", DETAIL_1),
                ("detail_1", DETAIL_2),
                ("address_final", ""),
                ("route_final", ""),
            ]
        )

    async def fail_save(*args: object, **kwargs: object) -> None:
        raise AssertionError("identity mismatch must not be persisted")

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)

    async def fake_identity(_server_id: str) -> dict[str, Any]:
        return {"status": "success", "serial_number": "ABC123"}

    monkeypatch.setattr("tools.network_collect._redfish_system_identity", fake_identity)
    monkeypatch.setattr("tools.network_collect.save_network_inventory", fail_save)

    result = await collect_network_inventory(["host1"])
    host = result["results"][0]
    assert result["status"] == "error"
    assert host["identity"]["status"] == "mismatch"
    assert host["saved"]["message"] == "identity mismatch: collected data was not persisted"


@pytest.mark.parametrize("unsafe_state", ["sent_unconfirmed", "partial_text_possible"])
async def test_uncertain_base_probe_stops_before_second_session(
    monkeypatch: pytest.MonkeyPatch, unsafe_state: str
) -> None:
    _configure()
    calls = 0

    async def fake_serial(*_args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        result = _session(
            [
                ("link", LINK),
                ("sysfs", SYSFS),
                ("pci", PCI),
                ("system", "HOSTNAME\tlive\nSERIAL\tABC123\nKERNEL\tLinux\n"),
                ("lldp", "LLDP_UNAVAILABLE\n"),
            ]
        )
        if unsafe_state == "sent_unconfirmed":
            result["sent_unconfirmed"] = True
            result["commands"][2].update({"status": "error", "result_confirmed": False, "exit_code": None})
        else:
            result["input_state"] = "partial_text_possible"
        return result

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)

    result = await collect_network_inventory(["host1"], save=False)

    assert result["status"] == "error"
    assert result["results"][0]["phase"] == "base-probes-unknown"
    assert calls == 1


@pytest.mark.parametrize(
    "probe_update",
    [
        {"status": "error", "exit_code": 1},
        {"status": "success", "exit_code": 0, "truncated": True},
    ],
)
async def test_required_probe_must_be_zero_exit_and_untruncated(
    monkeypatch: pytest.MonkeyPatch, probe_update: dict[str, Any]
) -> None:
    _configure()
    calls = 0

    async def fake_serial(*_args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        result = _session(
            [
                ("link", LINK),
                ("sysfs", SYSFS),
                ("pci", PCI),
                ("system", "HOSTNAME\tlive\nSERIAL\tABC123\nKERNEL\tLinux\n"),
                ("lldp", "LLDP_UNAVAILABLE\n"),
            ]
        )
        result["commands"][0].update(probe_update)
        return result

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)

    result = await collect_network_inventory(["host1"], save=False)

    assert result["status"] == "error"
    assert calls == 1


async def test_optional_failed_and_truncated_probes_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()
    calls = 0

    async def fake_serial(_server_id: str, _commands: list[str], _timeout: float) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            result = _session(
                [
                    ("link", LINK),
                    ("sysfs", SYSFS),
                    ("pci", ""),
                    ("system", "HOSTNAME\tlive\nSERIAL\tABC123\nKERNEL\tLinux\n"),
                    ("lldp", "LLDP_UNAVAILABLE\n"),
                ],
                status="partial",
            )
            result["commands"][2].update({"status": "error", "exit_code": 1})
            return result
        result = _session(
            [
                ("detail_0", DETAIL_1),
                ("detail_1", DETAIL_2),
                ("address_final", ""),
                ("route_final", ""),
            ],
            status="partial",
        )
        result["commands"][0]["truncated"] = True
        return result

    async def fake_identity(_server_id: str) -> dict[str, Any]:
        return {"status": "success", "serial_number": "ABC123"}

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)
    monkeypatch.setattr("tools.network_collect._redfish_system_identity", fake_identity)

    result = await collect_network_inventory(["host1"], save=False)

    host = result["results"][0]
    assert host["status"] == "partial"
    assert host["source"]["optional_probe_failures"] == ["pci", "detail_0"]


async def test_configured_redfish_serial_conflict_is_not_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()
    calls = 0

    async def fake_serial(*_args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _session(
                [
                    ("link", LINK),
                    ("sysfs", SYSFS),
                    ("pci", PCI),
                    ("system", "HOSTNAME\tlive\nSERIAL\tABC123\nKERNEL\tLinux\n"),
                    ("lldp", "LLDP_UNAVAILABLE\n"),
                ]
            )
        return _session(
            [
                ("detail_0", DETAIL_1),
                ("detail_1", DETAIL_2),
                ("address_final", ""),
                ("route_final", ""),
            ]
        )

    async def fake_identity(_server_id: str) -> dict[str, Any]:
        return {"status": "success", "serial_number": "DIFFERENT"}

    async def fail_save(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("conflicting serial identities must not be persisted")

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)
    monkeypatch.setattr("tools.network_collect._redfish_system_identity", fake_identity)
    monkeypatch.setattr("tools.network_collect.save_network_inventory", fail_save)

    result = await collect_network_inventory(["host1"])

    host = result["results"][0]
    assert host["identity"]["status"] == "mismatch"
    assert host["identity"]["serial_matches"] is False
    assert host["identity"]["serial_comparisons"]["configured_to_redfish"] is False
    assert host["saved"]["status"] == "error"


async def test_unverified_identity_fails_closed_for_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()
    config.CONFIG["host1"].pop("expected_host_macs")
    config.CONFIG["host1"].pop("serial_number")
    calls = 0

    async def fake_serial(*_args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _session(
                [
                    ("link", LINK),
                    ("sysfs", SYSFS),
                    ("pci", PCI),
                    ("system", "HOSTNAME\tlive\nSERIAL\t\nKERNEL\tLinux\n"),
                    ("lldp", "LLDP_UNAVAILABLE\n"),
                ]
            )
        return _session(
            [
                ("detail_0", DETAIL_1),
                ("detail_1", DETAIL_2),
                ("address_final", ""),
                ("route_final", ""),
            ]
        )

    async def unavailable_identity(_server_id: str) -> dict[str, Any]:
        return {"status": "unavailable"}

    async def fail_save(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unverified identity must not be persisted")

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)
    monkeypatch.setattr("tools.network_collect._redfish_system_identity", unavailable_identity)
    monkeypatch.setattr("tools.network_collect.save_network_inventory", fail_save)

    result = await collect_network_inventory(["host1"], save=True)

    host = result["results"][0]
    assert host["status"] == "error"
    assert host["identity"]["status"] == "unverified"
    assert host["saved"]["message"] == "identity unverified: collected data was not persisted"


async def test_serial_failure_reports_visual_vnc_fallback_without_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure()

    async def fake_serial(*args: object) -> dict[str, Any]:
        return {
            "status": "error",
            "phase": "prompt-probe",
            "transport": "idrac-ssh-sol",
            "command_sent": False,
            "retry_safe": True,
            "message": "no shell prompt",
            "commands": [],
        }

    monkeypatch.setattr("tools.network_collect._run_serial_commands", fake_serial)
    result = await collect_network_inventory(["host1"], transport="auto")
    host = result["results"][0]
    assert host["status"] == "error"
    assert host["vnc_fallback_required"] is True
    assert host["command_sent"] is False
    assert host["retry_safe"] is True


async def test_collect_network_inventory_is_registered() -> None:
    async with Client(config.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "collect_network_inventory" in names
