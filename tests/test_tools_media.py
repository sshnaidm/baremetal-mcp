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
        result = await boot_from_iso(["host1"], IMAGE_URL, verify=False)
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
        result = await boot_from_iso(["host1"], IMAGE_URL, verify=False)
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
        result = await boot_from_iso(["host1"], IMAGE_URL, reboot=False, verify=False)
        assert result[0]["status"] == "success"
        assert "ForceRestart" not in result[0]["message"]

    async def test_verify_false_still_powers_on_an_off_host(
        self, setup_dell_config, mock_redfish_client
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)
        powered_off = dict(DELL_R750_SYSTEM, PowerState="Off")
        reset_payloads = []

        def reset(_method, _url, **kwargs):
            reset_payloads.append(kwargs["json"])
            return make_mock_response(204, content=b"")

        mock_redfish_client(
            {
                "/Actions/ComputerSystem.Reset": reset,
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Systems/System.Embedded.1": make_mock_response(200, powered_off),
            }
        )

        result = await boot_from_iso(["host1"], IMAGE_URL, verify=False)

        assert result[0]["status"] == "success"
        assert reset_payloads == [{"ResetType": "On"}]
        assert "Reset request accepted: On" in result[0]["message"]
        assert result[0]["reset_request"] == {
            "requested_type": "On",
            "accepted": True,
            "power_state_verified": False,
        }
        assert "reset_type" not in result[0]["verification"]

    async def test_insert_fails(self, setup_dell_config, mock_redfish_client):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(500, {"error": "fail"}),
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL, verify=False)
        assert result[0]["status"] == "error"

    async def test_exception(self, mock_redfish_client):
        mock_redfish_client({})
        result = await boot_from_iso(["nonexistent"], IMAGE_URL, verify=False)
        assert result[0]["status"] == "error"

    async def test_verifies_exact_media_and_boot_override_before_reset(
        self, setup_dell_config, mock_redfish_client
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )
        state = {"media_inserted": False, "boot_patched": False, "reset_seen": False}

        def media(method, _url, **_kwargs):
            from conftest import DELL_R750_VM_CD, make_mock_response

            if method == "POST":
                state["media_inserted"] = True
                return make_mock_response(204, content=b"")
            data = dict(DELL_R750_VM_CD)
            if state["media_inserted"]:
                data.update({"Inserted": True, "Image": IMAGE_URL})
            return make_mock_response(200, data)

        def system(method, _url, **_kwargs):
            from conftest import DELL_R750_SYSTEM, make_mock_response

            if method == "PATCH":
                state["boot_patched"] = True
                return make_mock_response(204, content=b"")
            data = dict(DELL_R750_SYSTEM)
            data["Boot"] = dict(DELL_R750_SYSTEM["Boot"])
            if state["boot_patched"]:
                data["Boot"].update(
                    {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}
                )
            return make_mock_response(200, data)

        def reset(method, _url, **kwargs):
            from conftest import make_mock_response

            assert method == "POST"
            assert state["media_inserted"] is True
            assert state["boot_patched"] is True
            assert kwargs["json"] == {"ResetType": "ForceRestart"}
            state["reset_seen"] = True
            return make_mock_response(204, content=b"")

        mock_redfish_client(
            {
                "/Actions/VirtualMedia.InsertMedia": media,
                "/VirtualMedia/CD": media,
                "/Actions/ComputerSystem.Reset": reset,
                "/Systems/System.Embedded.1": system,
            }
        )
        result = await boot_from_iso(["host1"], IMAGE_URL)
        assert result[0]["status"] == "success"
        assert result[0]["verification"]["media"]["image"] == IMAGE_URL
        assert result[0]["verification"]["boot"]["boot_source_override_target"] == "Cd"
        assert result[0]["reset_request"]["accepted"] is True
        assert result[0]["reset_request"]["power_state_verified"] is False
        assert "reset_type" not in result[0]["verification"]
        assert state["reset_seen"] is True

    async def test_late_insert_failure_preserves_accepted_eject_state(
        self, setup_dell_config, mock_redfish_client
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image="http://old.iso")
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Actions/VirtualMedia.EjectMedia": make_mock_response(204, content=b""),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(
                    400, {"error": "rejected"}
                ),
            }
        )

        result = (await boot_from_iso(["host1"], IMAGE_URL, verify=False))[0]

        assert result["status"] == "error"
        assert result["phase"] == "media-insert"
        assert result["partial_state"] is True
        assert result["accepted_mutations"] == ["eject_media"]
        assert [item["state"] for item in result["mutations"]] == [
            "accepted",
            "rejected",
        ]
        assert result["remote_request_sent"] is True
        assert result["outcome_unknown"] is False
        assert result["retry_safe"] is False
        assert result["actions_completed"] == ["Ejected media http://old.iso"]

    async def test_verification_failure_reports_accepted_but_unconfirmed_insert(
        self, setup_dell_config, mock_redfish_client, monkeypatch
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )

        async def fail_verification(*_args, **_kwargs):
            raise TimeoutError("media state unavailable")

        monkeypatch.setattr("tools.media._wait_for_media", fail_verification)
        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD),
                "/Actions/VirtualMedia.InsertMedia": make_mock_response(204, content=b""),
            }
        )

        result = (await boot_from_iso(["host1"], IMAGE_URL))[0]

        assert result["status"] == "error"
        assert result["phase"] == "media-verification"
        assert result["accepted_mutations"] == ["insert_media"]
        assert result["partial_state"] is True
        assert result["remote_request_sent"] is True
        assert result["outcome_unknown"] is True
        assert result["retry_safe"] is False

    async def test_ambiguous_insert_is_preserved(
        self, setup_dell_config, mock_redfish_client, monkeypatch
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )

        async def ambiguous_insert(*_args, **_kwargs):
            return {
                "status": "error",
                "message": "connection closed",
                "remote_request_sent": None,
                "outcome_unknown": True,
                "retry_safe": False,
            }

        monkeypatch.setattr("tools.media._insert_virtual_media", ambiguous_insert)
        mock_redfish_client({"/VirtualMedia/CD": make_mock_response(200, DELL_R750_VM_CD)})

        result = (await boot_from_iso(["host1"], IMAGE_URL, verify=False))[0]

        assert result["status"] == "error"
        assert result["phase"] == "media-insert"
        assert result["ambiguous_mutations"] == ["insert_media"]
        assert result["partial_state"] is True
        assert result["remote_request_sent"] is None
        assert result["outcome_unknown"] is True
        assert result["retry_safe"] is False

    async def test_ambiguous_boot_override_is_preserved(
        self, setup_dell_config, mock_redfish_client
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)

        def system(method, _url, **_kwargs):
            if method == "PATCH":
                return make_mock_response(503, {"error": "unknown"})
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_redfish_client(
            {
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Systems/System.Embedded.1": system,
            }
        )

        result = (await boot_from_iso(["host1"], IMAGE_URL, verify=False))[0]

        assert result["status"] == "error"
        assert result["phase"] == "boot-override"
        assert result["ambiguous_mutations"] == ["set_boot_override"]
        assert result["mutations"][0]["state"] == "sent_unconfirmed"
        assert result["remote_request_sent"] is True
        assert result["outcome_unknown"] is True
        assert result["retry_safe"] is False

    async def test_ambiguous_reset_preserves_accepted_boot_override(
        self, setup_dell_config, mock_redfish_client
    ):
        import config

        config.VIRTUAL_MEDIA_PATH_CACHE["host1"] = (
            "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia/CD"
        )
        inserted_cd = dict(DELL_R750_VM_CD, Inserted=True, Image=IMAGE_URL)

        def system(method, _url, **_kwargs):
            if method == "PATCH":
                return make_mock_response(204, content=b"")
            return make_mock_response(200, DELL_R750_SYSTEM)

        mock_redfish_client(
            {
                "/Actions/ComputerSystem.Reset": make_mock_response(
                    503, {"error": "unknown"}
                ),
                "/VirtualMedia/CD": make_mock_response(200, inserted_cd),
                "/Systems/System.Embedded.1": system,
            }
        )

        result = (await boot_from_iso(["host1"], IMAGE_URL, verify=False))[0]

        assert result["status"] == "error"
        assert result["phase"] == "reset-request"
        assert result["accepted_mutations"] == ["set_boot_override"]
        assert result["ambiguous_mutations"] == ["request_reset"]
        assert result["partial_state"] is True
        assert result["remote_request_sent"] is True
        assert result["outcome_unknown"] is True
        assert result["retry_safe"] is False

    async def test_rejects_credentialed_or_non_http_image_url(self):
        for image in ("file:///tmp/image.iso", "https://root:secret@example/image.iso"):
            result = await boot_from_iso(["host1"], image)
            assert result[0]["status"] == "error"
            assert result[0]["phase"] == "validation"
