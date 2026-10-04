"""Tests for tools/junos.py - Junos switch SSH operations."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import paramiko
import pytest


class TestJunosSshCommandsSync:
    def test_unknown_switch(self) -> None:
        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("nonexistent", ["show version"])
        assert result["status"] == "error"
        assert "Unknown switch" in result["message"]

    def test_no_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.CONFIG["dummy"] = {"bmc_ip": "1.2.3.4"}
        config.SWITCHES["bad-switch"] = {"model": "test"}
        config.SECRETS["bad-switch"] = {"username": "u", "password": "p"}

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("bad-switch", ["show version"])
        assert result["status"] == "error"
        assert "No address" in result["message"]

    def test_missing_credentials(self) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {}

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Missing credentials" in result["message"]

    def test_requires_explicit_port(self) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1"}
        config.SECRETS["test-switch"] = {"username": "example-user", "password": "not-real"}

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])

        assert result["status"] == "error"
        assert "switch.port" in result["message"]

    def test_auth_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "wrong"}

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = paramiko.AuthenticationException("Auth failed")
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = False
        mock_client.get_transport.return_value = mock_transport

        monkeypatch.setattr("tools.junos.paramiko.SSHClient", lambda: mock_client)

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Authentication failed" in result["message"]

    def test_generic_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = Exception("Connection timeout")
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = False
        mock_client.get_transport.return_value = mock_transport

        monkeypatch.setattr("tools.junos.paramiko.SSHClient", lambda: mock_client)

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Connection timeout" in result["message"]

    def test_successful_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_channel = MagicMock()

        recv_data = iter(
            [
                b"admin@switch> ",
                b"Screen length set to 0\nadmin@switch> ",
                b"show version\nJunos: 20.4R1\nadmin@switch> ",
            ]
        )
        mock_channel.recv_ready.return_value = True
        mock_channel.recv.side_effect = lambda size: next(recv_data)

        mock_client.invoke_shell.return_value = mock_channel
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport

        monkeypatch.setattr("tools.junos.paramiko.SSHClient", lambda: mock_client)

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "success"
        assert "show version" in result["data"]

    def test_prompt_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_channel = MagicMock()
        mock_channel.recv_ready.return_value = False

        mock_client.invoke_shell.return_value = mock_channel
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport

        monkeypatch.setattr("tools.junos.paramiko.SSHClient", lambda: mock_client)

        clock = iter(range(0, 10000, 100))
        monkeypatch.setattr("tools.junos.time.time", lambda: next(clock))
        monkeypatch.setattr("tools.junos.time.sleep", lambda _: None)

        from tools.junos import _junos_ssh_commands_sync

        result = _junos_ssh_commands_sync("test-switch", ["show version"])
        assert result["status"] == "error"
        assert "Timed out" in result["message"]


class TestJunosRunCommand:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config

        config.SWITCHES["test-switch"] = {"hostname": "10.0.0.1", "port": 22}
        config.SECRETS["test-switch"] = {"username": "admin", "password": "pass"}

        mock_result = {
            "switch_id": "test-switch",
            "status": "success",
            "data": {"show interfaces": "ge-0/0/0  up"},
        }

        async def mock_ssh(sid: object, cmds: list[str]) -> object:
            return mock_result

        monkeypatch.setattr("tools.junos._junos_ssh_commands", mock_ssh)

        from tools.junos import junos_run_command

        result = await junos_run_command("test-switch", "show interfaces")
        assert result["status"] == "success"
        assert result["data"] == "ge-0/0/0  up"

    async def test_error_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def mock_ssh(sid: object, cmds: list[str]) -> dict[str, Any]:
            return {"switch_id": sid, "status": "error", "message": "Connection refused"}

        monkeypatch.setattr("tools.junos._junos_ssh_commands", mock_ssh)

        from tools.junos import junos_run_command

        result = await junos_run_command("test-switch", "show version")
        assert result["status"] == "error"
