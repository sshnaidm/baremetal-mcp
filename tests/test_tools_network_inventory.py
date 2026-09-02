"""Tests for persistent network inventory tools."""

from pathlib import Path

import yaml
from fastmcp import Client

import config
from tools.network_inventory import (
    export_network_inventory,
    get_network_inventory,
    list_network_inventories,
    save_network_inventory,
    save_network_inventories,
    search_network_inventory,
    validate_network_inventories,
)


def _interfaces():
    return [
        {
            "name": "eno1",
            "mac_address": "AA-BB-CC-DD-EE-01",
            "vendor": "Intel Corporation",
            "model": "E810",
            "pci_address": "0000:31:00.0",
            "physical_port": "p0",
            "driver": "ice",
            "link": {"detected": True, "state": "up", "speed_mbps": 25000, "media": "fibre"},
            "addresses": [{"address": "192.0.2.10/24", "scope": "global"}, "fe80::10/64"],
        },
        {
            "name": "eno2",
            "mac_address": "aa:bb:cc:dd:ee:02",
            "vendor": "Broadcom Inc.",
            "pci_address": "0000:04:00.0",
            "link": {"detected": False, "state": "down"},
            "addresses": [],
        },
    ]


def _use_temp_inventory(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NETWORK_INVENTORY_DIR", str(tmp_path / "inventory"))


async def test_inventory_tools_are_registered():
    expected = {
        "save_network_inventory",
        "get_network_inventory",
        "list_network_inventories",
        "search_network_inventory",
        "save_network_inventories",
        "validate_network_inventories",
        "export_network_inventory",
    }
    async with Client(config.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert expected <= names


async def test_save_get_and_list_inventory(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)

    saved = await save_network_inventory(
        "host/1",
        _interfaces(),
        host={"model": "PowerEdge R650"},
        routes=[{"destination": "0.0.0.0/0", "gateway": "192.0.2.1", "interface": "eno1"}],
        source={"collection_method": "vnc-console"},
        observed_at="2026-09-01T10:00:00Z",
    )

    assert saved["status"] == "success"
    assert saved["operation"] == "created"
    assert saved["interface_count"] == 2
    assert saved["links_up"] == 1
    assert saved["address_count"] == 2
    path = Path(saved["path"])
    assert path.name == "host%2F1.yaml"
    document = yaml.safe_load(path.read_text())
    assert document["schema_version"] == 1
    assert document["interfaces"][0]["mac_address"] == "aa:bb:cc:dd:ee:01"
    assert document["interfaces"][0]["addresses"][0]["family"] == "ipv4"

    loaded = await get_network_inventory("host/1")
    assert loaded["status"] == "success"
    assert loaded["inventory"]["host"]["model"] == "PowerEdge R650"

    listed = await list_network_inventories()
    assert listed["count"] == 1
    assert listed["hosts"][0]["server_id"] == "host/1"
    assert listed["hosts"][0]["links_up"] == 1


async def test_save_replaces_latest_snapshot(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    first = await save_network_inventory("host1", _interfaces())
    second = await save_network_inventory("host1", _interfaces()[:1])

    assert first["operation"] == "created"
    assert second["operation"] == "updated"
    loaded = await get_network_inventory("host1")
    assert len(loaded["inventory"]["interfaces"]) == 1


async def test_search_inventory_filters(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    await save_network_inventory("host1", _interfaces(), observed_at="2026-09-01T10:00:00Z")

    by_mac = await search_network_inventory(mac="aabb.ccdd.ee01")
    assert [match["name"] for match in by_mac["matches"]] == ["eno1"]

    active_intel = await search_network_inventory(link_up=True, vendor="intel")
    assert active_intel["count"] == 1
    assert active_intel["matches"][0]["speed_mbps"] == 25000

    by_interface = await search_network_inventory(interface="NO2", link_up=False)
    assert [match["mac_address"] for match in by_interface["matches"]] == ["aa:bb:cc:dd:ee:02"]

    by_subnet = await search_network_inventory(ip="192.0.2.0/24", pci_address="0000:31:00.0")
    assert [match["server_id"] for match in by_subnet["matches"]] == ["host1"]

    by_ip = await search_network_inventory(ip="fe80::10")
    assert by_ip["count"] == 1


async def test_validation_errors(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)

    assert (await save_network_inventory("", []))["status"] == "error"
    assert (await save_network_inventory("host1", [{"name": "eno1", "mac_address": "bad"}]))["status"] == "error"
    assert (await save_network_inventory("host1", [{"name": "eno1"}, {"name": "eno1"}]))["status"] == "error"
    assert (await save_network_inventory("host1", [], host={"password": "must-not-be-stored"}))["status"] == "error"
    assert (await save_network_inventory("host1", [], observed_at="2026-09-01T10:00:00"))["status"] == "error"
    assert (await get_network_inventory("missing"))["status"] == "error"
    assert (await search_network_inventory(ip="not-an-ip"))["status"] == "error"
    assert (await search_network_inventory(mac="not-a-mac"))["status"] == "error"


async def test_list_and_search_report_malformed_documents(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    hosts = tmp_path / "inventory" / "hosts"
    hosts.mkdir(parents=True)
    (hosts / "broken.yaml").write_text("schema_version: 999\ninterfaces: []\n")

    listed = await list_network_inventories()
    searched = await search_network_inventory()

    assert listed["count"] == 0
    assert len(listed["errors"]) == 1
    assert searched["count"] == 0
    assert len(searched["errors"]) == 1


async def test_deeply_malformed_documents_are_reported_by_all_consumers(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    hosts = tmp_path / "inventory" / "hosts"
    hosts.mkdir(parents=True)
    (hosts / "broken.yaml").write_text(
        "schema_version: 1\n"
        "server_id: broken\n"
        "observed_at: '2026-09-01T10:00:00Z'\n"
        "interfaces:\n"
        "  - not-a-mapping\n"
        "routes: []\n"
    )

    loaded = await get_network_inventory("broken")
    listed = await list_network_inventories()
    searched = await search_network_inventory()
    validated = await validate_network_inventories(["broken"])
    exported = await export_network_inventory(["broken"])

    assert loaded["status"] == "error"
    assert "interfaces[0]" in loaded["message"]
    assert listed["status"] == "partial" and len(listed["errors"]) == 1
    assert searched["status"] == "partial" and len(searched["errors"]) == 1
    assert validated["status"] == "partial" and len(validated["malformed"]) == 1
    assert exported["status"] == "error" and len(exported["malformed"]) == 1


async def test_rejects_older_snapshot_and_invalid_typed_fields(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    first = await save_network_inventory("host1", _interfaces(), observed_at="2026-09-02T10:00:00Z")
    older = await save_network_inventory("host1", _interfaces(), observed_at="2026-09-01T10:00:00Z")

    assert first["status"] == "success"
    assert older["status"] == "error"
    assert "newer" in older["message"]
    invalid_link = await save_network_inventory("host2", [{"name": "eno1", "link": {}}])
    assert invalid_link["status"] == "error"
    invalid_family = _interfaces()
    invalid_family[0]["addresses"] = [{"address": "192.0.2.10/24", "family": "ipv6"}]
    assert (await save_network_inventory("host2", invalid_family))["status"] == "error"


async def test_batch_save_duplicate_guard_and_search_pagination(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    batch = await save_network_inventories(
        [
            {"server_id": "host1", "interfaces": _interfaces()},
            {"server_id": "host1", "interfaces": _interfaces()},
            {"server_id": "host2", "interfaces": _interfaces()},
        ]
    )
    assert batch["status"] == "partial"
    assert batch["saved"] == 2
    assert batch["results"][1]["message"] == "duplicate server_id in batch"

    first_page = await search_network_inventory(limit=1)
    assert first_page["count"] == 1
    assert first_page["total_count"] == 4
    assert first_page["has_more"] is True
    assert (await search_network_inventory(limit=0))["status"] == "error"


async def test_validate_and_export_indexes_keep_bmc_macs_separate(monkeypatch, tmp_path):
    _use_temp_inventory(monkeypatch, tmp_path)
    config.CONFIG.update(
        {
            "host1": {
                "bmc_ip": "10.0.0.1",
                "vendor": "dell",
                "expected_host_macs": ["aa:bb:cc:dd:ee:01"],
            },
            "host2": {
                "bmc_ip": "10.0.0.2",
                "vendor": "dell",
                "expected_host_macs": ["aa:bb:cc:dd:ee:99"],
            },
            "host3": {"bmc_ip": "10.0.0.3", "vendor": "hpe"},
        }
    )
    await save_network_inventory(
        "host1",
        _interfaces(),
        host={"bmc_address": "10.0.0.1", "bmc_mac_address": "00:11:22:33:44:55"},
    )
    await save_network_inventory(
        "host2",
        _interfaces(),
        host={"bmc_address": "10.0.0.2", "bmc_mac_address": "00:11:22:33:44:66"},
    )

    validated = await validate_network_inventories(["host1", "host2", "host3"])
    assert validated["status"] == "partial"
    assert validated["missing"] == ["host3"]
    assert validated["identity_mismatches"][0]["server_id"] == "host2"
    assert "aa:bb:cc:dd:ee:01" in validated["duplicate_macs"]

    exported = await export_network_inventory(format="json", collection="rack-a")
    assert exported["status"] == "partial"
    document = yaml.safe_load(Path(exported["path"]).read_text())
    assert document["mac_index"]["aa:bb:cc:dd:ee:01"][0]["host"] == "host1"
    assert document["bmc_mac_index"]["00:11:22:33:44:55"][0]["host"] == "host1"
    assert "00:11:22:33:44:55" not in document["mac_index"]
    assert (await export_network_inventory(collection="../escape"))["status"] == "error"
