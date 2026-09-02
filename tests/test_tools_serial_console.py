"""Tests for guarded Dell SOL and HPE iLO VSP command execution."""

import asyncio
import re
from unittest.mock import MagicMock

import paramiko
import pytest


@pytest.fixture(autouse=True)
def _isolate_serial_console(monkeypatch):
    import tools.serial_console as serial

    serial._SERIAL_LOCKS.clear()

    async def run_direct(function, *args):
        return function(*args)

    monkeypatch.setattr(serial, "_run_in_thread", run_direct)
    monkeypatch.setattr(serial, "_ATTACH_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(serial, "_WAKE_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(serial, "_POLL_INTERVAL", 0.001)
    yield
    serial._SERIAL_LOCKS.clear()


def _configure(config, server_id="host1", vendor="dell"):
    config.CONFIG[server_id] = {
        "bmc_ip": "192.0.2.10",
        "serial_console": {"port": 22, "transport": "auto"},
        "vendor": vendor,
    }
    config.SECRETS[server_id] = {
        "username": "example-console-user",
        "password": "not-real",
    }


class ScriptedChannel:
    def __init__(self, outcomes=None, probe=True, login=False, fail_after_command=False):
        self.outcomes = list(outcomes or [("output", 0)])
        self.probe = probe
        self.login = login
        self.fail_after_command = fail_after_command
        self.sent = []
        self.received = []
        self.closed = False
        self.command_sends = 0

    def sendall(self, value):
        self.sent.append(value)
        if value in {"console com2\r", "VSP\r"}:
            self.received.append(b"fedora login: " if self.login else b"root@fedora:~# ")
        elif "__BM_PROBE_%s__" in value and self.probe:
            nonce = re.findall(r"'([0-9a-f]{24})'", value)[0]
            self.received.append(f"\r\n__BM_PROBE_{nonce}__\r\nroot@fedora:~# ".encode())
        elif "__BM_BEGIN_%s__" in value:
            nonce = re.findall(r"'([0-9a-f]{24})'", value)[0]
            output, rc = self.outcomes[self.command_sends]
            self.command_sends += 1
            if rc is None:
                response = f"\r\n__BM_BEGIN_{nonce}__\r\n{output}"
            else:
                response = f"\r\n__BM_BEGIN_{nonce}__\r\n{output}" f"\r\n__BM_END_{nonce}__ rc={rc}\r\nroot@fedora:~# "
            self.received.append(response.encode())

    def recv_ready(self):
        if self.fail_after_command and self.command_sends:
            raise OSError("simulated console read failure")
        return bool(self.received)

    def recv(self, _size):
        return self.received.pop(0)

    def close(self):
        self.closed = True


class FakeSshClient:
    def __init__(self, channel):
        self.channel = channel
        self.closed = False

    def invoke_shell(self, **_kwargs):
        return self.channel

    def close(self):
        self.closed = True


def _install_fake_connection(monkeypatch, channel):
    import tools.serial_console as serial

    client = FakeSshClient(channel)
    monkeypatch.setattr(serial, "_connect_ssh", lambda _settings: client)
    return client


class TestSerialCommand:
    def test_requires_explicit_serial_port(self):
        import config
        import tools.serial_console as serial

        _configure(config)
        config.CONFIG["host1"]["serial_console"].pop("port")

        settings, error = serial._serial_settings("host1")

        assert settings is None
        assert "serial_console.port" in error["message"]

    async def test_dell_success_uses_probe_markers_and_detaches(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(outcomes=[("hello", 0)])
        client = _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "printf hello", 1)

        assert result["status"] == "success"
        assert result["transport"] == "idrac-ssh-sol"
        assert result["command_sent"] is True
        assert result["result_confirmed"] is True
        assert result["exit_code"] == 0
        assert result["output"] == "hello"
        assert result["truncated"] is False
        assert result["retry_safe"] is False
        assert channel.sent[0] == "console com2\r"
        assert any("__BM_PROBE_%s__" in value for value in channel.sent)
        assert any("sh -c 'printf hello'" in value for value in channel.sent)
        assert channel.sent[-1] == "\x1c"
        assert channel.closed is True
        assert client.closed is True

    async def test_hpe_uses_vsp_attach_and_detach(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config, vendor="HPE")
        channel = ScriptedChannel()
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "true", 1)

        assert result["status"] == "success"
        assert result["transport"] == "ilo-ssh-vsp"
        assert channel.sent[0] == "VSP\r"
        assert channel.sent[-1] == "\x1b("

    async def test_prompt_probe_failure_does_not_send_requested_command(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(probe=False)
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "systemctl restart NetworkManager", 0.1)

        assert result["status"] == "error"
        assert result["phase"] == "prompt-probe"
        assert result["command_sent"] is False
        assert result["retry_safe"] is True
        assert not any("systemctl restart" in value for value in channel.sent)
        assert channel.sent[-1] == "\x1c"

    async def test_login_prompt_blocks_probe_and_command(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        monkeypatch.setattr(serial, "_ATTACH_SETTLE_SECONDS", 0.01)
        channel = ScriptedChannel(login=True)
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "true", 1)

        assert result["phase"] == "shell-readiness"
        assert result["command_sent"] is False
        assert "login" in result["message"]
        assert not any("__BM_PROBE" in value for value in channel.sent)

    async def test_sent_without_completion_is_unknown_and_not_retried(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(outcomes=[("still running", None)])
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "some-command", 0.1)

        assert result["status"] == "error"
        assert result["phase"] == "command-result"
        assert result["command_sent"] is True
        assert result["result_confirmed"] is False
        assert result["exit_code"] is None
        assert result["retry_safe"] is False
        assert "outcome is unknown" in result["message"]
        assert channel.command_sends == 1
        assert channel.sent[-1] == "\x1c"

    async def test_read_failure_after_send_is_unknown_and_detaches(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(fail_after_command=True)
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "some-command", 1)

        assert result["status"] == "error"
        assert result["command_sent"] is True
        assert result["result_confirmed"] is False
        assert result["retryable"] is False
        assert "outcome is unknown" in result["message"]
        assert channel.command_sends == 1
        assert channel.sent[-1] == "\x1c"
        assert channel.closed is True

    async def test_nonzero_exit_is_confirmed(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(outcomes=[("failed", 7)])
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "false", 1)

        assert result["status"] == "error"
        assert result["result_confirmed"] is True
        assert result["exit_code"] == 7
        assert result["output"] == "failed"

    async def test_output_is_bounded_and_reports_truncation(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        monkeypatch.setattr(serial, "_output_limit", lambda: 1024)
        channel = ScriptedChannel(outcomes=[("x" * 4096, 0)])
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_command("host1", "large-output", 1)

        assert result["status"] == "success"
        assert result["truncated"] is True
        assert 0 < len(result["output"]) <= 1024
        assert result["output_chars_total"] >= 4096

    async def test_multicommand_session_stops_after_unknown_result(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        channel = ScriptedChannel(outcomes=[("first", 0), ("second", None)])
        _install_fake_connection(monkeypatch, channel)

        result = await serial._run_serial_commands(
            "host1",
            [("one", "cmd-one"), ("two", "cmd-two"), ("three", "cmd-three")],
            0.1,
        )

        assert [item["label"] for item in result["commands"]] == ["one", "two", "three"]
        assert result["commands"][0]["status"] == "success"
        assert result["commands"][1]["command_sent"] is True
        assert result["commands"][1]["result_confirmed"] is False
        assert result["commands"][2]["phase"] == "skipped"
        assert result["commands"][2]["command_sent"] is False
        assert result["commands"][2]["retry_safe"] is False
        assert result["sent_unconfirmed"] is True
        assert channel.command_sends == 2

    async def test_validation_happens_before_connect(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        connect = MagicMock()
        monkeypatch.setattr(serial, "_connect_ssh", connect)

        result = await serial._run_serial_command("host1", "one\ntwo", 1)

        assert result["status"] == "error"
        assert result["phase"] == "validation"
        assert result["command_sent"] is False
        connect.assert_not_called()

    async def test_unknown_server_is_structured_and_does_not_connect(self, monkeypatch):
        import tools.serial_console as serial

        connect = MagicMock()
        monkeypatch.setattr(serial, "_connect_ssh", connect)

        result = await serial._run_serial_command("missing", "true", 1)

        assert result["status"] == "error"
        assert result["phase"] == "config"
        assert result["command_sent"] is False
        assert "Unknown server" in result["message"]
        connect.assert_not_called()


class TestAuthentication:
    def test_keyboard_interactive_fallback(self, monkeypatch):
        import tools.serial_console as serial

        ssh = MagicMock()
        ssh.connect.side_effect = paramiko.BadAuthenticationType(
            "password not allowed",
            ["keyboard-interactive"],
        )
        fake_socket = MagicMock()
        transport = MagicMock()
        answers = {}

        def auth_interactive(username, callback):
            answers["username"] = username
            answers["values"] = callback(
                "",
                "",
                [("Password: ", False), ("Login: ", True)],
            )

        transport.auth_interactive.side_effect = auth_interactive
        monkeypatch.setattr(serial.paramiko, "SSHClient", lambda: ssh)
        monkeypatch.setattr(serial.socket, "create_connection", lambda *_args, **_kwargs: fake_socket)
        monkeypatch.setattr(serial.paramiko, "Transport", lambda _socket: transport)
        settings = {
            "host": "192.0.2.10",
            "port": 22,
            "username": "Administrator",
            "password": "not-real",
            "connect_timeout": 4,
        }

        result = serial._connect_ssh(settings)

        assert result is ssh
        assert answers == {
            "username": "Administrator",
            "values": ["not-real", "Administrator"],
        }
        transport.start_client.assert_called_once_with(timeout=4)
        assert ssh._transport is transport


class TestBatchTool:
    async def test_preserves_first_seen_order_and_deduplicates(self, monkeypatch):
        import tools.serial_console as serial

        async def fake_run(server_id, _command, _timeout):
            return {
                "server_id": server_id,
                "status": "success",
                "transport": "idrac-ssh-sol",
                "phase": "complete",
                "command_sent": True,
                "exit_code": 0,
                "output": "",
                "truncated": False,
                "output_chars_total": 0,
                "result_confirmed": True,
                "retry_safe": False,
            }

        monkeypatch.setattr(serial, "_run_serial_command", fake_run)
        result = await serial.run_console_command_batch(
            ["cnfdr2", "cnfdr1", "cnfdr2"],
            "true",
            timeout_seconds=5,
            concurrency=2,
            dry_run=False,
            confirm_command="true",
        )

        assert result["status"] == "success"
        assert result["server_ids"] == ["cnfdr2", "cnfdr1"]
        assert [item["server_id"] for item in result["results"]] == ["cnfdr2", "cnfdr1"]
        assert result["requested_count"] == 3
        assert result["unique_count"] == 2
        assert result["duplicates_removed"] == 1

    async def test_same_host_calls_are_serialized(self, monkeypatch):
        import tools.serial_console as serial

        active = 0
        maximum = 0

        def fake_sync(server_id, commands, _timeout):
            item = {
                "label": commands[0][0],
                "server_id": server_id,
                "status": "success",
                "transport": "idrac-ssh-sol",
                "phase": "complete",
                "command_sent": True,
                "exit_code": 0,
                "output": "",
                "truncated": False,
                "output_chars_total": 0,
                "result_confirmed": True,
                "retry_safe": False,
                "retryable": False,
            }
            return serial._aggregate_session(
                server_id,
                "idrac-ssh-sol",
                [item],
                phase="complete",
            )

        async def tracked_runner(function, *args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            result = function(*args)
            active -= 1
            return result

        monkeypatch.setattr(serial, "_run_serial_commands_sync", fake_sync)
        monkeypatch.setattr(serial, "_run_in_thread", tracked_runner)
        await asyncio.gather(
            serial._run_serial_command("same", "true", 1),
            serial._run_serial_command("same", "true", 1),
        )

        assert maximum == 1

    async def test_batch_size_and_concurrency_are_bounded(self):
        import tools.serial_console as serial

        too_many = await serial.run_console_command_batch(
            [f"host-{number}" for number in range(65)],
            "true",
        )
        bad_concurrency = await serial.run_console_command_batch(
            ["host1"],
            "true",
            concurrency=13,
        )

        assert too_many["status"] == "error"
        assert "At most 64" in too_many["message"]
        assert bad_concurrency["status"] == "error"
        assert "between 1 and 12" in bad_concurrency["message"]

    async def test_defaults_to_no_connection_dry_run(self, monkeypatch):
        import config
        import tools.serial_console as serial

        _configure(config)
        execute = MagicMock()
        monkeypatch.setattr(serial, "_run_serial_command", execute)

        result = await serial.run_console_command_batch(["host1"], "true")

        assert result["status"] == "success"
        assert result["dry_run"] is True
        assert result["results"][0]["phase"] == "dry-run"
        assert result["results"][0]["command_sent"] is False
        execute.assert_not_called()

    async def test_execution_requires_exact_command_confirmation(self, monkeypatch):
        import tools.serial_console as serial

        execute = MagicMock()
        monkeypatch.setattr(serial, "_run_serial_command", execute)

        result = await serial.run_console_command_batch(
            ["host1"],
            "systemctl restart NetworkManager",
            dry_run=False,
            confirm_command="systemctl restart networkmanager",
        )

        assert result["status"] == "error"
        assert "exactly match" in result["message"]
        execute.assert_not_called()
