"""Tests for tools/dell_switch.py - Dell OS10 read-only and confirmed CLI execution."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call

import paramiko
import pytest


class TestDellSwitchSshCommandsSync:
    def test_unknown_switch(self) -> None:
        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("nonexistent", ["show version"])
        assert result["status"] == "error"
        assert "Unknown switch" in result["message"]

    def test_rejects_non_show_command_before_connecting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["configure terminal"])
        assert result["status"] == "error"
        assert "Only read-only" in result["message"]
        mock_client.connect.assert_not_called()

    def test_rejects_multiline_command(self) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version\nreload"])
        assert result["status"] == "error"
        assert "single line" in result["message"]

    def test_no_address(self) -> None:
        import config

        config.SWITCHES["bad-switch"] = {"model": "S5232F-ON"}
        config.SECRETS["bad-switch"] = {"username": "admin", "password": "pass"}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("bad-switch", ["show version"])
        assert result["status"] == "error"
        assert "No address" in result["message"]

    def test_missing_credentials(self) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Missing credentials" in result["message"]

    def test_requires_explicit_port(self) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {"username": "example-user", "password": "not-real"}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])

        assert result["status"] == "error"
        assert "switch.port" in result["message"]

    def test_auth_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "wrong"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = paramiko.AuthenticationException("Auth failed")
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Authentication failed" in result["message"]
        mock_client.close.assert_called_once()

    def test_successful_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_channel = MagicMock()
        recv_data = iter(
            [
                b"Dell EMC Networking OS10\r\nadmin@switch# ",
                b"terminal length 0\r\nadmin@switch# ",
                b"show version\r\nOS Version: 10.5.2.6\r\nadmin@switch# ",
            ]
        )
        mock_channel.recv_ready.return_value = True
        mock_channel.recv.side_effect = lambda size: next(recv_data)
        mock_client.invoke_shell.return_value = mock_channel
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "success"
        assert result["data"]["show version"] == "OS Version: 10.5.2.6"
        mock_channel.send.assert_any_call("terminal length 0\n")
        mock_channel.send.assert_any_call("show version\n")
        mock_client.close.assert_called_once()


class TestDellSwitchRunCommand:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def mock_ssh(switch_id: str, commands: list[str]) -> dict[str, Any]:
            return {
                "switch_id": switch_id,
                "status": "success",
                "data": {"show interface status": "Eth 1/1/1 up"},
            }

        monkeypatch.setattr("tools.dell_switch._dell_switch_ssh_commands", mock_ssh)

        from tools.dell_switch import dell_switch_run_command

        result = await dell_switch_run_command("test-switch", "show interface status")
        assert result["status"] == "success"
        assert result["data"] == "Eth 1/1/1 up"

    async def test_error_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def mock_ssh(switch_id: str, commands: list[str]) -> dict[str, Any]:
            return {"switch_id": switch_id, "status": "error", "message": "Connection refused"}

        monkeypatch.setattr("tools.dell_switch._dell_switch_ssh_commands", mock_ssh)

        from tools.dell_switch import dell_switch_run_command

        result = await dell_switch_run_command("test-switch", "show version")
        assert result["status"] == "error"


class TestDellSwitchApplyCommandsSync:
    def test_runs_configuration_and_save_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_channel = MagicMock()
        recv_data = iter(
            [
                b"Dell EMC Networking OS10\r\nadmin@switch# ",
                b"terminal length 0\r\nadmin@switch# ",
                b"configure terminal\r\nadmin@switch(config)# ",
                b"hostname replacement\r\nadmin@switch(config)# ",
                b"end\r\nadmin@switch# ",
                b"copy running-configuration startup-configuration\r\nCopy completed\r\nadmin@switch# ",
            ]
        )
        mock_channel.recv_ready.return_value = True
        mock_channel.recv.side_effect = lambda size: next(recv_data)
        mock_client.invoke_shell.return_value = mock_channel
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_apply_commands_sync

        commands = [
            "configure terminal",
            "hostname replacement",
            "end",
            "copy running-configuration startup-configuration",
        ]
        result = _dell_switch_apply_commands_sync("test-switch", commands, True, startup_save_authorized=True)

        assert result["status"] == "success"
        assert result["commands_executed"] == commands
        assert [item["command"] for item in result["command_results"]] == commands
        for command in commands:
            mock_channel.send.assert_any_call(f"{command}\n")

    def test_rejects_startup_save_before_connecting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock(spec=paramiko.SSHClient)
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_apply_commands_sync

        result = _dell_switch_apply_commands_sync(
            "test-switch", ["copy running-configuration startup-configuration"], True
        )

        assert result["status"] == "error"
        assert result["phase"] == "startup-save-confirmation"
        mock_client.connect.assert_not_called()

    def test_stops_after_cli_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_channel = MagicMock()
        recv_data = iter(
            [
                b"admin@switch# ",
                b"terminal length 0\r\nadmin@switch# ",
                b"bad command\r\n% Error: invalid command\r\nadmin@switch# ",
            ]
        )
        mock_channel.recv_ready.return_value = True
        mock_channel.recv.side_effect = lambda size: next(recv_data)
        mock_client.invoke_shell.return_value = mock_channel
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_apply_commands_sync

        result = _dell_switch_apply_commands_sync("test-switch", ["bad command", "reload"], True)

        assert result["status"] == "error"
        assert result["commands_executed"] == ["bad command"]
        assert call("reload\n") not in mock_channel.send.call_args_list


class TestDellSwitchApplyCommands:
    async def test_dry_run_allows_configuration_and_save_commands(self) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        commands = [
            "configure terminal",
            "interface breakout 1/1/3 map 25g-4x",
            "end",
            "copy running-configuration startup-configuration",
        ]
        result = await dell_switch_apply_commands(["test-switch"], commands)

        assert result["status"] == "success"
        assert result["phase"] == "dry-run"
        assert result["plan"]["commands"] == commands
        assert result["plan"]["session_setup"] == ["terminal length 0"]
        assert result["plan"]["commands_are_unrestricted"] is True
        assert result["plan"]["writes_startup_configuration"] is True
        assert result["confirmation_required"].startswith("APPLY DELL SWITCH COMMANDS ")
        assert result["startup_save_requires_explicit_user_confirmation"] is True
        assert result["startup_save_confirmation_required"].startswith("SAVE SWITCH STARTUP CONFIGURATION ")

    async def test_requires_exact_confirmation_before_connecting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        mock_apply = MagicMock()
        monkeypatch.setattr("tools.dell_switch._dell_switch_apply_commands", mock_apply)

        result = await dell_switch_apply_commands(
            ["test-switch"],
            ["write memory"],
            dry_run=False,
            confirmation="wrong",
        )

        assert result["status"] == "error"
        assert result["phase"] == "confirmation"
        mock_apply.assert_not_called()

    async def test_executes_on_multiple_switches_after_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        commands = ["configure terminal", "vlan 307", "end", "write memory"]
        dry_run = await dell_switch_apply_commands(["switch-1", "switch-2"], commands)

        async def mock_apply(
            switch_id: str, supplied_commands: list[str], stop_on_error: bool, startup_save_authorized: bool
        ) -> dict[str, Any]:
            assert supplied_commands == commands
            assert stop_on_error is True
            assert startup_save_authorized is True
            return {"switch_id": switch_id, "status": "success"}

        monkeypatch.setattr("tools.dell_switch._dell_switch_apply_commands", mock_apply)
        result = await dell_switch_apply_commands(
            ["switch-1", "switch-2"],
            commands,
            dry_run=False,
            confirmation=dry_run["confirmation_required"],
            startup_save_user_confirmed=True,
            startup_save_confirmation=dry_run["startup_save_confirmation_required"],
        )

        assert result["status"] == "success"
        assert [item["switch_id"] for item in result["results"]] == ["switch-1", "switch-2"]

    async def test_startup_save_requires_separate_user_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        commands = ["copy running-configuration startup-configuration"]
        dry_run = await dell_switch_apply_commands(["test-switch"], commands)
        mock_apply = MagicMock()
        monkeypatch.setattr("tools.dell_switch._dell_switch_apply_commands", mock_apply)

        for user_confirmed, save_confirmation in [
            (False, dry_run["startup_save_confirmation_required"]),
            (True, None),
            (True, "wrong"),
        ]:
            result = await dell_switch_apply_commands(
                ["test-switch"],
                commands,
                dry_run=False,
                confirmation=dry_run["confirmation_required"],
                startup_save_user_confirmed=user_confirmed,
                startup_save_confirmation=save_confirmation,
            )
            assert result["status"] == "error"
            assert result["phase"] == "startup-save-confirmation"
        mock_apply.assert_not_called()

    @pytest.mark.parametrize(
        "command",
        [
            "copy running-configuration startup-configuration",
            "COPY run start",
            "do copy running-config startup-config",
            "write memory",
            "wr mem",
            "save",
            "delete startup-configuration",
            "show version; write memory",
        ],
    )
    async def test_startup_write_aliases_are_guarded(self, command: str) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        preview = await dell_switch_apply_commands(["test-switch"], [command])

        assert preview["plan"]["writes_startup_configuration"] is True
        assert preview["startup_save_requires_explicit_user_confirmation"] is True

    async def test_running_config_only_does_not_require_startup_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        commands = ["configure terminal", "interface ethernet1/1/6:2", "end"]
        preview = await dell_switch_apply_commands(["test-switch"], commands)
        assert preview["plan"]["writes_startup_configuration"] is False
        assert "startup_save_confirmation_required" not in preview

        async def mock_apply(
            switch_id: str, supplied_commands: list[str], stop_on_error: bool, startup_save_authorized: bool
        ) -> dict[str, Any]:
            assert startup_save_authorized is False
            return {"switch_id": switch_id, "status": "success"}

        monkeypatch.setattr("tools.dell_switch._dell_switch_apply_commands", mock_apply)
        result = await dell_switch_apply_commands(
            ["test-switch"],
            commands,
            dry_run=False,
            confirmation=preview["confirmation_required"],
        )
        assert result["status"] == "success"

    async def test_rejects_multiline_command(self) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        result = await dell_switch_apply_commands(["test-switch"], ["show version\nreload"])

        assert result["status"] == "error"
        assert result["phase"] == "validation"

    async def test_rejects_duplicate_switch_ids_after_normalization(self) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        result = await dell_switch_apply_commands(["test-switch", " test-switch "], ["show version"])

        assert result["status"] == "error"
        assert result["phase"] == "validation"

    async def test_confirmation_changes_with_plan(self) -> None:
        from tools.dell_switch import dell_switch_apply_commands

        first = await dell_switch_apply_commands(["test-switch"], ["show version"])
        second = await dell_switch_apply_commands(["test-switch"], ["write memory"])

        assert first["confirmation_required"] != second["confirmation_required"]
