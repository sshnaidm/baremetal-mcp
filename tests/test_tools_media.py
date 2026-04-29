"""Tests for tools/media.py - virtual media management."""

from conftest import (
    make_mock_response,
    DELL_R750_SYSTEM,
    DELL_R750_VM_CD,
)
from tools.media import inject_media, eject_media, boot_from_iso

IMAGE_URL = "http://iso.local/rhel9.iso"


class TestInjectMedia:
    async def test_already_inserted_same_image(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, inserted_cd)})
        result = await inject_media(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"
        assert "already inserted" in result[0]["message"]

    async def test_different_image_eject_and_insert(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image="http://old.iso")
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b""),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b""),
            }
        )
        result = await inject_media(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"
        assert "inserted" in result[0]["message"]

    async def test_nothing_inserted(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b""),
            }
        )
        result = await inject_media(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"

    async def test_eject_fails(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image="http://old.iso")
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(500, {"error": "fail"}),
            }
        )
        result = await inject_media(["host1"], IMAGE_URL)
        assert result[0]["status"] == "error"

    async def test_insert_fails(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(500, {"error": "fail"}),
            }
        )
        result = await inject_media(["host1"], IMAGE_URL)
        assert result[0]["status"] == "error"

    async def test_exception(self, mock_redfish_client):
        mock_redfish_client({})
        result = await inject_media(["nonexistent"], IMAGE_URL)
        assert result[0]["status"] == "error"


class TestEjectMedia:
    async def test_nothing_inserted(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD)})
        result = await eject_media(["host1"])
        assert result[0]["status"] == "success"
        assert "Nothing ejected" in result[0]["message"]

    async def test_inserted_eject_success(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b""),
            }
        )
        result = await eject_media(["host1"])
        assert result[0]["status"] == "success"
        assert IMAGE_URL in result[0]["message"]

    async def test_inserted_no_image_url(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=None)
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b""),
            }
        )
        result = await eject_media(["host1"])
        assert result[0]["status"] == "success"

    async def test_eject_fails(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(500, {"error": "fail"}),
            }
        )
        result = await eject_media(["host1"])
        assert result[0]["status"] == "error"


class TestBootFromIso:
    async def test_full_flow(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b""),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/Actions/ComputerSystem.Reset": make_mock_response(204, content=b""),
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"
        assert "Inserted" in result[0]["message"]
        assert "Boot override" in result[0]["message"]
        assert "ForceRestart" in result[0]["message"]

    async def test_same_iso_already_inserted(self, setup_dell_config, mock_redfish_client):
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
                "/Actions/ComputerSystem.Reset": make_mock_response(204, content=b""),
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"
        assert "already inserted" in result[0]["message"]

    async def test_no_reboot(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b""),
                "/Systems/System.Embedded.1": make_mock_response(200, DELL_R750_SYSTEM),
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL, reboot=False)
        assert result[0]["status"] == "success"
        assert "ForceRestart" not in result[0]["message"]

    async def test_insert_fails(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(500, {"error": "fail"}),
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL)
        assert result[0]["status"] == "error"

    async def test_exception(self, mock_redfish_client):
        mock_redfish_client({})
        result = await boot_from_iso(["nonexistent"], IMAGE_URL)
        assert result[0]["status"] == "error"
