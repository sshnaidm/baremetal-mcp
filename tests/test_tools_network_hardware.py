"""Tests for normalized read-only Redfish network hardware inventory."""

from fastmcp import Client

import config
from conftest import DELL_R750_SYSTEM, make_mock_response
from tools.network_hardware import get_network_hardware


async def test_collects_interfaces_adapters_ports_and_functions(
    setup_dell_config, mock_redfish_client
):
    system = dict(DELL_R750_SYSTEM)
    routes = {
        "/Systems/System.Embedded.1/EthernetInterfaces/NIC1": make_mock_response(
            200,
            {
                "@odata.id": "/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces/NIC1",
                "Id": "NIC1",
                "Name": "Host Interface",
                "MACAddress": "AA:BB:CC:DD:EE:01",
                "LinkStatus": "LinkUp",
                "CurrentLinkSpeedMbps": 25000,
                "IPv4Addresses": [{"Address": "192.0.2.10"}],
            },
        ),
        "/Systems/System.Embedded.1/EthernetInterfaces": make_mock_response(
            200,
            {
                "Members": [
                    {
                        "@odata.id": "/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces/NIC1"
                    }
                ]
            },
        ),
        "/Systems/System.Embedded.1/NetworkInterfaces": make_mock_response(
            200, {"Members": []}
        ),
        "/Chassis/1/NetworkAdapters/A1/NetworkPorts/P1": make_mock_response(
            200,
            {
                "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1/NetworkPorts/P1",
                "Id": "P1",
                "PhysicalPortNumber": "1",
                "LinkStatus": "Up",
                "CurrentLinkSpeedMbps": 25000,
                "AssociatedNetworkAddresses": ["AA:BB:CC:DD:EE:01"],
            },
        ),
        "/Chassis/1/NetworkAdapters/A1/NetworkPorts": make_mock_response(
            200,
            {
                "Members": [
                    {"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1/NetworkPorts/P1"}
                ]
            },
        ),
        "/Chassis/1/NetworkAdapters/A1/NetworkDeviceFunctions/F1": make_mock_response(
            200,
            {
                "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1/NetworkDeviceFunctions/F1",
                "Id": "F1",
                "DeviceEnabled": True,
                "NetDevFuncType": "Ethernet",
                "Ethernet": {
                    "MACAddress": "AA:BB:CC:DD:EE:01",
                    "PermanentMACAddress": "AA:BB:CC:DD:EE:01",
                },
                "Links": {
                    "PhysicalPortAssignment": {
                        "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1/NetworkPorts/P1"
                    }
                },
            },
        ),
        "/Chassis/1/NetworkAdapters/A1/NetworkDeviceFunctions": make_mock_response(
            200,
            {
                "Members": [
                    {
                        "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1/NetworkDeviceFunctions/F1"
                    }
                ]
            },
        ),
        "/Chassis/1/NetworkAdapters/A1": make_mock_response(
            200,
            {
                "@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1",
                "Id": "A1",
                "Manufacturer": "Intel",
                "Model": "E810",
                "Controllers": [
                    {
                        "ControllerCapabilities": {"NetworkPortCount": 2},
                        "Location": {"PartLocation": {"ServiceLabel": "Slot 1"}},
                    }
                ],
            },
        ),
        "/Chassis/1/NetworkAdapters": make_mock_response(
            200,
            {"Members": [{"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/A1"}]},
        ),
        "/redfish/v1/Chassis/1": make_mock_response(
            200, {"@odata.id": "/redfish/v1/Chassis/1", "Id": "1"}
        ),
        "/redfish/v1/Chassis": make_mock_response(
            200, {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]}
        ),
        "/Systems/System.Embedded.1": make_mock_response(200, system),
    }
    mock_redfish_client(routes)

    result = await get_network_hardware(["host1", "host1"])

    assert result["status"] == "success"
    assert result["duplicates_removed"] == 1
    host = result["results"][0]
    assert host["ethernet_interfaces"][0]["mac_address"] == "aa:bb:cc:dd:ee:01"
    assert host["network_adapters"][0]["model"] == "E810"
    assert host["network_ports"][0]["physical_port_number"] == "1"
    assert host["network_ports"][0]["mac_addresses"] == ["aa:bb:cc:dd:ee:01"]
    assert host["network_device_functions"][0]["physical_port_path"].endswith("/P1")


async def test_identity_mismatch_stops_hardware_walk(
    setup_dell_config, mock_redfish_client
):
    config.CONFIG["host1"]["serial_number"] = "EXPECTED"
    observed = dict(DELL_R750_SYSTEM, SerialNumber="OTHER")
    mock_redfish_client({"/Systems/System.Embedded.1": make_mock_response(200, observed)})

    result = await get_network_hardware(["host1"])

    assert result["status"] == "error"
    assert result["results"][0]["identity"]["status"] == "mismatch"


async def test_network_hardware_tool_is_registered():
    async with Client(config.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "get_network_hardware" in names
