"""Shared fixtures and real-data constants for baremetal-mcp tests."""

import json
from unittest.mock import MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Real Redfish JSON data captured from four server types
# ---------------------------------------------------------------------------

# ---- Dell R750 (host1, regular iDRAC 9) ----

DELL_R750_ROOT = {
    "@odata.id": "/redfish/v1",
    "RedfishVersion": "1.20.1",
    "Vendor": "Dell",
    "Product": "Integrated Dell Remote Access Controller",
    "Oem": {
        "Dell": {
            "@odata.context": "/redfish/v1/$metadata#DellServiceRoot.DellServiceRoot",
            "@odata.type": "#DellServiceRoot.v1_0_0.DellServiceRoot",
            "IsBranded": 0,
            "ManagerMACAddress": "b0:7b:25:ec:2a:ca",
            "ServiceTag": "B5TXMH3",
        }
    },
}

DELL_R750_SYSTEM = {
    "@odata.id": "/redfish/v1/Systems/System.Embedded.1",
    "Manufacturer": "Dell Inc.",
    "Model": "PowerEdge R750",
    "SerialNumber": "B5TXMH3",
    "PowerState": "On",
    "BiosVersion": "1.8.2",
    "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
    "ProcessorSummary": {
        "Count": 2,
        "CoreCount": 56,
        "LogicalProcessorCount": 112,
        "Model": "Intel(R) Xeon(R) Gold 6330N CPU @ 2.20GHz",
        "Status": {"Health": "OK"},
    },
    "MemorySummary": {
        "TotalSystemMemoryGiB": 128,
        "MemoryMirroring": "System",
        "Status": {"Health": "OK"},
    },
    "Boot": {
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideMode": "UEFI",
    },
}

DELL_R750_MANAGER = {
    "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1",
    "FirmwareVersion": "7.20.70.50",
    "ManagerType": "BMC",
    "Model": "15G Monolithic",
}

DELL_R750_VM_COLLECTION = {
    "Members": [
        {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/RemovableDisk"},
        {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"},
    ],
    "Members@odata.count": 2,
}

DELL_R750_VM_REMOVABLE = {
    "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/RemovableDisk",
    "MediaTypes": ["USBStick"],
    "Inserted": False,
    "Image": None,
}

DELL_R750_VM_CD = {
    "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD",
    "MediaTypes": ["CD", "DVD"],
    "Inserted": False,
    "Image": None,
    "ConnectedVia": "NotConnected",
}

DELL_R750_FW_INVENTORY_EXPAND = {
    "Members": [
        {"Name": "Backplane 1", "Version": "3.72"},
        {"Name": "iDRAC", "Version": "7.20.70.50"},
        {"Name": "BIOS", "Version": "1.8.2"},
        {"Name": "NIC in Slot 3", "Version": "22.00.6"},
        {"Name": "PERC H755 Front", "Version": "52.28.0-4666"},
        {"Name": "Disk 4 in Backplane 1 of RAID Controller in SL 7", "Version": "BJ02"},
        {"Name": "Disk 5 in Backplane 1 of RAID Controller in SL 7", "Version": "BJ02"},
        {"Name": "Power Supply 1", "Version": "00.2D.68"},
        {"Name": "Power Supply 2", "Version": "00.2D.68"},
    ],
}

# ---- HPE DL380 Gen10 (host100, iLO 5) ----

HPE_DL380_ROOT = {
    "@odata.id": "/redfish/v1",
    "RedfishVersion": "1.6.0",
    "Vendor": "HPE",
    "Product": "ProLiant DL380 Gen10",
    "Oem": {
        "Hpe": {
            "@odata.context": "/redfish/v1/$metadata#HpeiLOServiceExt.HpeiLOServiceExt",
            "@odata.type": "#HpeiLOServiceExt.v2_4_0.HpeiLOServiceExt",
            "Manager": [{"ManagerFirmwareVersion": "2.78", "ManagerType": "iLO 5"}],
        }
    },
}

HPE_DL380_SYSTEM = {
    "@odata.id": "/redfish/v1/Systems/1",
    "Manufacturer": "HPE",
    "Model": "ProLiant DL380 Gen10",
    "SerialNumber": "2M200602B4",
    "PowerState": "Off",
    "BiosVersion": "U30 v2.22 (11/13/2019)",
    "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Disabled"},
    "ProcessorSummary": {
        "Count": 2,
        "Model": "Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz",
        "Status": {"HealthRollup": "OK"},
    },
    "MemorySummary": {"TotalSystemMemoryGiB": 192, "Status": {"HealthRollup": "OK"}},
    "Boot": {
        "BootSourceOverrideEnabled": "Continuous",
        "BootSourceOverrideTarget": "Hdd",
        "BootSourceOverrideMode": "UEFI",
    },
}

HPE_DL380_MANAGER = {
    "@odata.id": "/redfish/v1/Managers/1",
    "FirmwareVersion": "iLO 5 v2.78",
    "ManagerType": "BMC",
    "Model": "iLO 5",
}

HPE_DL380_VM_COLLECTION = {
    "Members": [
        {"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/1"},
        {"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2"},
    ],
    "Members@odata.count": 2,
}

HPE_DL380_VM_1 = {
    "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/1",
    "MediaTypes": ["Floppy"],
    "Inserted": False,
    "Image": "",
}

HPE_DL380_VM_2 = {
    "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
    "MediaTypes": ["CD", "DVD"],
    "Inserted": False,
    "Image": "",
    "ConnectedVia": "NotConnected",
}

HPE_DL380_FW_COLLECTION = {
    "Members": [
        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/1"},
        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/2"},
        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/3"},
    ],
}

HPE_DL380_FW_MEMBER_1 = {"Name": "iLO 5", "Version": "2.78"}
HPE_DL380_FW_MEMBER_2 = {"Name": "HPE Ethernet 1Gb 4-port 331i Adapter - NIC", "Version": "20.14.54"}
HPE_DL380_FW_MEMBER_3 = {"Name": "System BIOS", "Version": "U30 v2.22"}

# ---- Dell R7725 iDRAC-10 (host200) ----

DELL_R7725_ROOT = {
    "@odata.id": "/redfish/v1",
    "RedfishVersion": "1.22.0",
    "Vendor": "Dell",
    "Product": "Integrated Dell Remote Access Controller",
    "Oem": {
        "Dell": {
            "@odata.context": "/redfish/v1/$metadata#DellServiceRoot.DellServiceRoot",
            "@odata.type": "#DellServiceRoot.v1_0_0.DellServiceRoot",
            "IsBranded": 0,
            "ManagerMACAddress": "6c:3c:8c:97:4d:9a",
            "ServiceTag": "1SZT3D4",
        }
    },
}

DELL_R7725_SYSTEM = {
    "@odata.id": "/redfish/v1/Systems/System.Embedded.1",
    "Manufacturer": "Dell Inc.",
    "Model": "PowerEdge R7725",
    "SerialNumber": "1SZT3D4",
    "PowerState": "Off",
    "BiosVersion": "1.3.3",
    "Status": {"Health": "OK", "HealthRollup": "OK"},
    "ProcessorSummary": {
        "Count": 2,
        "CoreCount": 384,
        "LogicalProcessorCount": 768,
        "Model": "AMD EPYC 9965 192-Core Processor",
        "Status": {"Health": "OK"},
    },
    "MemorySummary": {
        "TotalSystemMemoryGiB": 768,
        "MemoryMirroring": "None",
        "Status": {"Health": "OK"},
    },
    "Boot": {
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideMode": "UEFI",
    },
}

DELL_R7725_MANAGER = {
    "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1",
    "FirmwareVersion": "1.20.70.50",
    "ManagerType": "BMC",
    "Model": "17G Monolithic",
}

# ---- Supermicro Super Server (host500) ----

SUPERMICRO_ROOT = {
    "@odata.id": "/redfish/v1/",
    "RedfishVersion": "1.8.0",
    "Oem": {"Supermicro": {}},
}

SUPERMICRO_SYSTEM = {
    "@odata.id": "/redfish/v1/Systems/1",
    "Manufacturer": "Supermicro",
    "Model": "Super Server",
    "SerialNumber": "ZAYOICZ00001",
    "PowerState": "On",
    "BiosVersion": "3.5",
    "Status": {"Health": "OK", "State": "Enabled"},
    "ProcessorSummary": {
        "Count": 1,
        "Model": "Intel(R) Xeon(R) processor",
        "Status": {"Health": "OK"},
    },
    "MemorySummary": {
        "TotalSystemMemoryGiB": 96,
        "MemoryMirroring": "System",
        "Status": {"Health": "OK"},
    },
    "Boot": {
        "BootSourceOverrideEnabled": "Continuous",
        "BootSourceOverrideTarget": "Hdd",
        "BootSourceOverrideMode": "UEFI",
    },
}

SUPERMICRO_MANAGER = {
    "@odata.id": "/redfish/v1/Managers/1",
    "FirmwareVersion": "01.73.06",
    "ManagerType": "BMC",
    "Model": "ASPEED",
}

SUPERMICRO_VM_COLLECTION = {
    "Members": [{"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/CD1"}],
    "Members@odata.count": 1,
}

SUPERMICRO_VM_CD1 = {
    "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/CD1",
    "MediaTypes": ["CD", "DVD"],
    "Inserted": False,
    "Image": "http://10.6.105.10/CentOS-7-x86_64-Minimal-2009.iso",
    "ConnectedVia": "NotConnected",
}

SUPERMICRO_FW_COLLECTION = {
    "Members": [
        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/BIOS"},
        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/BMC"},
    ],
}

SUPERMICRO_FW_BIOS = {"Name": "BIOS", "Version": "3.5"}
SUPERMICRO_FW_BMC = {"Name": "BMC", "Version": "01.73.06"}

SUPERMICRO_STORAGE_EMPTY = {"Members": [], "Members@odata.count": 0}

SUPERMICRO_SIMPLE_STORAGE = {
    "Members": [{"@odata.id": "/redfish/v1/Systems/1/SimpleStorage/1"}],
}

SUPERMICRO_SIMPLE_STORAGE_1 = {
    "Devices": [
        {
            "Name": "NVMe SSD 0",
            "Model": "SAMSUNG MZQLB1T9HAJR-00007",
            "Manufacturer": "Samsung",
            "CapacityBytes": 1920383410176,
            "Status": {"State": "Enabled", "Health": "OK"},
        },
    ],
}

SUPERMICRO_CHASSIS_COLLECTION = {
    "Members": [
        {"@odata.id": "/redfish/v1/Chassis/1"},
        {"@odata.id": "/redfish/v1/Chassis/NVMeSSD.0.0"},
        {"@odata.id": "/redfish/v1/Chassis/NVMeSSD.0.1"},
    ],
}

SUPERMICRO_CHASSIS_DRIVE = {
    "Id": "0",
    "Name": "NVMe SSD 0",
    "Model": "SAMSUNG MZQLB1T9HAJR",
    "SerialNumber": "S439NA0N505123",
    "CapacityBytes": 1920383410176,
    "MediaType": "SSD",
    "Protocol": "NVMe",
    "Status": {"State": "Enabled", "Health": "OK"},
}

# ---------------------------------------------------------------------------
# Mock config data
# ---------------------------------------------------------------------------

MOCK_CONFIG = {
    "host1": {"bmc_ip": "10.0.0.1", "vendor": "dell", "lab": "labA", "tags": ["gpu", "compute", "dell", "r750"]},
    "host100": {"bmc_ip": "10.0.0.100", "vendor": "hpe", "lab": "labB", "tags": ["storage", "hp"]},
    "host200": {"bmc_ip": "10.0.0.200", "vendor": "dell", "lab": "labA", "tags": ["gpu", "dell", "idrac-10"]},
    "host500": {"bmc_ip": "10.0.5.100", "vendor": "supermicro", "lab": "labC", "tags": ["dev", "supermicro"]},
}

MOCK_SECRETS = {
    "host1": {"username": "root", "password": "test-pass-not-real"},
    "host100": {"username": "Administrator", "password": "test-pass-not-real"},
    "host200": {"username": "root", "password": "test-pass-not-real"},
    "host500": {"username": "ADMIN", "password": "test-pass-not-real"},
}

MOCK_SWITCHES = {
    "lab1-switch": {"hostname": "192.168.1.200", "model": "QFX5120", "tags": ["switch"]},
}

MOCK_ISOS = {
    "dell": [
        {
            "model_750": {
                "idrac_version": {"7": "http://fw.local/idrac7.exe"},
                "bios_version": {"1": "http://fw.local/bios1.exe"},
            }
        }
    ]
}

MOCK_ISOS_FLAT = {
    "dell": [
        {
            "model_750": True,
            "idrac_version": {"7": "http://fw.local/idrac7.exe"},
            "bios_version": {"1": "http://fw.local/bios1.exe"},
        }
    ]
}


# ---------------------------------------------------------------------------
# Utility: build httpx.Response from JSON data
# ---------------------------------------------------------------------------


def make_mock_response(status_code=200, json_data=None, content=b"", headers=None):
    """Build a fake httpx.Response."""
    if json_data is not None:
        body = json.dumps(json_data).encode()
        content_type = "application/json"
    else:
        body = content
        content_type = "application/octet-stream"
    resp_headers = {"content-type": content_type}
    if headers:
        resp_headers.update(headers)
    return httpx.Response(
        status_code=status_code,
        headers=resp_headers,
        content=body,
        request=httpx.Request("GET", "https://fake"),
    )


# ---------------------------------------------------------------------------
# Autouse fixture: isolate module-level globals between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_globals():
    """Reset all module-level mutable globals between tests."""
    import config
    import helpers
    from cache import RESPONSE_CACHE

    config.CONFIG.clear()
    config.SECRETS.clear()
    config.SWITCHES.clear()
    config.ISOS.clear()
    config.SETTINGS.clear()
    config.VIRTUAL_MEDIA_PATH_CACHE.clear()
    helpers._HANDLER_CACHE.clear()
    helpers._http_client = None
    RESPONSE_CACHE.clear()

    yield

    config.CONFIG.clear()
    config.SECRETS.clear()
    config.SWITCHES.clear()
    config.ISOS.clear()
    config.SETTINGS.clear()
    config.VIRTUAL_MEDIA_PATH_CACHE.clear()
    helpers._HANDLER_CACHE.clear()
    helpers._http_client = None
    RESPONSE_CACHE.clear()


# ---------------------------------------------------------------------------
# Mock httpx client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redfish_client(monkeypatch):
    """Return a setup function that patches httpx with a URL-to-response routing map.

    Usage:
        client = mock_redfish_client({
            "/redfish/v1/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            ...
        })
    """

    def _setup(route_map: dict):
        def _resolve(method, url, **kwargs):
            url_str = str(url)
            for pattern in sorted(route_map.keys(), key=len, reverse=True):
                if pattern in url_str:
                    resp = route_map[pattern]
                    if callable(resp) and not isinstance(resp, httpx.Response):
                        return resp(method, url, **kwargs)
                    return resp
            return make_mock_response(404, {"error": {"message": f"Not Found: {url_str}"}})

        mock_client = MagicMock(spec=httpx.AsyncClient)

        async def _async_request(method, url, **kwargs):
            return _resolve(method, url, **kwargs)

        async def _async_get(url, **kwargs):
            return _resolve("GET", url, **kwargs)

        mock_client.request = _async_request
        mock_client.get = _async_get
        monkeypatch.setattr("helpers._http_client", mock_client)
        monkeypatch.setattr("helpers._get_http_client", lambda: mock_client)
        return mock_client

    return _setup


# ---------------------------------------------------------------------------
# Pre-configured vendor fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_dell_config():
    """Populate CONFIG/SECRETS for Dell R750 (host1)."""
    import config

    config.CONFIG.update({"host1": dict(MOCK_CONFIG["host1"])})
    config.SECRETS.update({"host1": dict(MOCK_SECRETS["host1"])})


@pytest.fixture
def setup_hpe_config():
    """Populate CONFIG/SECRETS for HPE DL380 (host100)."""
    import config

    config.CONFIG.update({"host100": dict(MOCK_CONFIG["host100"])})
    config.SECRETS.update({"host100": dict(MOCK_SECRETS["host100"])})


@pytest.fixture
def setup_dell_idrac10_config():
    """Populate CONFIG/SECRETS for Dell R7725 iDRAC-10 (host200)."""
    import config

    config.CONFIG.update({"host200": dict(MOCK_CONFIG["host200"])})
    config.SECRETS.update({"host200": dict(MOCK_SECRETS["host200"])})


@pytest.fixture
def setup_supermicro_config():
    """Populate CONFIG/SECRETS for Supermicro (host500)."""
    import config

    config.CONFIG.update({"host500": dict(MOCK_CONFIG["host500"])})
    config.SECRETS.update({"host500": dict(MOCK_SECRETS["host500"])})


@pytest.fixture
def setup_all_configs():
    """Populate CONFIG/SECRETS for all four server types."""
    import config

    for sid in MOCK_CONFIG:
        config.CONFIG[sid] = dict(MOCK_CONFIG[sid])
    for sid in MOCK_SECRETS:
        config.SECRETS[sid] = dict(MOCK_SECRETS[sid])
    config.SWITCHES.update(MOCK_SWITCHES)


@pytest.fixture
def dell_routes():
    """URL route map for Dell R750 (host1)."""
    return {
        "/redfish/v1/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
        "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
        "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/RemovableDisk": make_mock_response(
            200, DELL_R750_VM_REMOVABLE
        ),
        "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia": make_mock_response(200, DELL_R750_VM_COLLECTION),
        "/redfish/v1/Managers/iDRAC.Embedded.1": make_mock_response(200, DELL_R750_MANAGER),
        "/redfish/v1/UpdateService/FirmwareInventory": make_mock_response(200, DELL_R750_FW_INVENTORY_EXPAND),
        "/redfish/v1": make_mock_response(200, DELL_R750_ROOT),
    }


@pytest.fixture
def hpe_routes():
    """URL route map for HPE DL380 (host100)."""
    return {
        "/redfish/v1/Systems/1": make_mock_response(200, HPE_DL380_SYSTEM),
        "/redfish/v1/Managers/1/VirtualMedia/1": make_mock_response(200, HPE_DL380_VM_1),
        "/redfish/v1/Managers/1/VirtualMedia/2": make_mock_response(200, HPE_DL380_VM_2),
        "/redfish/v1/Managers/1/VirtualMedia": make_mock_response(200, HPE_DL380_VM_COLLECTION),
        "/redfish/v1/Managers/1": make_mock_response(200, HPE_DL380_MANAGER),
        "/redfish/v1/UpdateService/FirmwareInventory/1": make_mock_response(200, HPE_DL380_FW_MEMBER_1),
        "/redfish/v1/UpdateService/FirmwareInventory/2": make_mock_response(200, HPE_DL380_FW_MEMBER_2),
        "/redfish/v1/UpdateService/FirmwareInventory/3": make_mock_response(200, HPE_DL380_FW_MEMBER_3),
        "/redfish/v1/UpdateService/FirmwareInventory": make_mock_response(200, HPE_DL380_FW_COLLECTION),
        "/redfish/v1": make_mock_response(200, HPE_DL380_ROOT),
    }


@pytest.fixture
def supermicro_routes():
    """URL route map for Supermicro (host500)."""
    return {
        "/redfish/v1/Systems/1/Storage": make_mock_response(200, SUPERMICRO_STORAGE_EMPTY),
        "/redfish/v1/Systems/1/SimpleStorage/1": make_mock_response(200, SUPERMICRO_SIMPLE_STORAGE_1),
        "/redfish/v1/Systems/1/SimpleStorage": make_mock_response(200, SUPERMICRO_SIMPLE_STORAGE),
        "/redfish/v1/Systems/1": make_mock_response(200, SUPERMICRO_SYSTEM),
        "/redfish/v1/Managers/1/VirtualMedia/CD1": make_mock_response(200, SUPERMICRO_VM_CD1),
        "/redfish/v1/Managers/1/VirtualMedia": make_mock_response(200, SUPERMICRO_VM_COLLECTION),
        "/redfish/v1/Managers/1": make_mock_response(200, SUPERMICRO_MANAGER),
        "/redfish/v1/UpdateService/FirmwareInventory/BIOS": make_mock_response(200, SUPERMICRO_FW_BIOS),
        "/redfish/v1/UpdateService/FirmwareInventory/BMC": make_mock_response(200, SUPERMICRO_FW_BMC),
        "/redfish/v1/UpdateService/FirmwareInventory": make_mock_response(200, SUPERMICRO_FW_COLLECTION),
        "/redfish/v1/Chassis": make_mock_response(200, SUPERMICRO_CHASSIS_COLLECTION),
        "/redfish/v1": make_mock_response(200, SUPERMICRO_ROOT),
    }
