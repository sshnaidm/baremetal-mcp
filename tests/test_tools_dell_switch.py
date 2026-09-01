"""Tests for tools/dell_switch.py - Dell OS10 read-only SSH queries."""

from unittest.mock import MagicMock

import paramiko


class TestDellSwitchSshCommandsSync:
    def test_unknown_switch(self):
        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("nonexistent", ["show version"])
        assert result["status"] == "error"
        assert "Unknown switch" in result["message"]

    def test_rejects_non_show_command_before_connecting(self, monkeypatch):
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["configure terminal"])
        assert result["status"] == "error"
        assert "Only read-only" in result["message"]
        mock_client.connect.assert_not_called()

    def test_rejects_multiline_command(self):
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version\nreload"])
        assert result["status"] == "error"
        assert "single line" in result["message"]

    def test_no_address(self):
        import config

        config.SWITCHES["bad-switch"] = {"model": "S5232F-ON"}
        config.SECRETS["bad-switch"] = {"username": "admin", "password": "pass"}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("bad-switch", ["show version"])
        assert result["status"] == "error"
        assert "No address" in result["message"]

    def test_missing_credentials(self):
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {}

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Missing credentials" in result["message"]

    def test_auth_failure(self, monkeypatch):
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "wrong"}
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = paramiko.AuthenticationException("Auth failed")
        monkeypatch.setattr("tools.dell_switch.paramiko.SSHClient", lambda: mock_client)

        from tools.dell_switch import _dell_switch_ssh_commands_sync

        result = _dell_switch_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Authentication failed" in result["message"]
        mock_client.close.assert_called_once()

    def test_successful_command(self, monkeypatch):
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
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
    async def test_success(self, monkeypatch):
        async def mock_ssh(switch_id, commands):
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

    async def test_error_passthrough(self, monkeypatch):
        async def mock_ssh(switch_id, commands):
            return {"switch_id": switch_id, "status": "error", "message": "Connection refused"}

        monkeypatch.setattr("tools.dell_switch._dell_switch_ssh_commands", mock_ssh)

        from tools.dell_switch import dell_switch_run_command

        result = await dell_switch_run_command("test-switch", "show version")
        assert result["status"] == "error"
