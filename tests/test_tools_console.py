"""Tests for BMC VNC console capture and command paging."""

from pathlib import Path
import subprocess
import time

from PIL import Image as PILImage
import pytest


@pytest.fixture(autouse=True)
def _clear_console_sessions():
    from tools.console import _ACTIVE_PAGERS, _CONSOLE_LOCKS, _CONSOLE_PREFLIGHTS

    _ACTIVE_PAGERS.clear()
    _CONSOLE_PREFLIGHTS.clear()
    _CONSOLE_LOCKS.clear()
    yield
    _ACTIVE_PAGERS.clear()
    _CONSOLE_PREFLIGHTS.clear()
    _CONSOLE_LOCKS.clear()


def _configure_console(config, *, vnc=None, secrets=None):
    vnc_settings = {"port": 5901, "key_delay": 0.01}
    if vnc is not None:
        vnc_settings.update(vnc)
    config.CONFIG["console-host"] = {
        "bmc_ip": "10.0.0.20",
        "vendor": "dell",
        "vnc": vnc_settings,
    }
    config.SECRETS["console-host"] = {"vnc_password": "not-real"} if secrets is None else secrets


def _allow_input(token="confirmed-screen"):
    from tools.console import _CONSOLE_PREFLIGHTS

    _CONSOLE_PREFLIGHTS["console-host"] = {
        "token": token,
        "created_at": time.monotonic(),
        "expires_at": time.monotonic() + 60,
    }
    return token


class TestCaptureVncConsoleSync:
    def test_unknown_server(self):
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("missing")
        assert result["status"] == "error"
        assert "Unknown server" in result["message"]

    def test_requires_vnc_configuration(self):
        import config

        config.CONFIG["console-host"] = {"bmc_ip": "10.0.0.20"}
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "error"
        assert "No VNC console" in result["message"]

    def test_requires_separate_vnc_password(self):
        import config

        _configure_console(config, secrets={"username": "root", "password": "redfish-only"})
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "error"
        assert "vnc_password" in result["message"]

    def test_rejects_invalid_port(self):
        import config

        _configure_console(config, vnc={"port": 70000})
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "error"
        assert "vnc.port" in result["message"]

    def test_requires_explicit_key_delay(self):
        import config

        config.CONFIG["console-host"] = {
            "bmc_ip": "10.0.0.20",
            "vendor": "dell",
            "vnc": {"port": 5901},
        }
        config.SECRETS["console-host"] = {"vnc_password": "not-real"}
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")

        assert result["status"] == "error"
        assert "key_delay" in result["message"]

    def test_success_returns_png_without_password_in_argv(self, monkeypatch):
        import config

        _configure_console(config)

        def fake_run(args, **kwargs):
            assert "not-real" not in args
            assert kwargs["input"] == "not-real\n"
            assert args[2] == "capture"
            output_path = Path(args[5])
            PILImage.new("RGB", (1024, 768), "black").save(output_path, format="PNG")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "success"
        assert result["width"] == 1024
        assert result["height"] == 768
        assert result["image"].startswith(b"\x89PNG")

    def test_timeout_is_reported(self, monkeypatch):
        import config

        _configure_console(config, vnc={"port": 5901, "timeout": 4})

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired("worker", 9)

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "error"
        assert result["message"] == "VNC capture operation timed out"

    def test_worker_error_redacts_password(self, monkeypatch):
        import config

        _configure_console(config)

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed with not-real")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _capture_vnc_console_sync

        result = _capture_vnc_console_sync("console-host")
        assert result["status"] == "error"
        assert "not-real" not in result["message"]
        assert "[redacted]" in result["message"]


class TestCaptureConsoleScreenTool:
    async def test_returns_mcp_image_content(self, monkeypatch):
        from tools.console import capture_console_screen

        async def fake_capture(server_id):
            return {
                "server_id": server_id,
                "status": "success",
                "protocol": "vnc",
                "width": 1,
                "height": 1,
                "mime_type": "image/png",
                "image": b"\x89PNG\r\n\x1a\nimage-data",
            }

        monkeypatch.setattr("tools.console._capture_vnc_console", fake_capture)

        result = await capture_console_screen("console-host")
        assert result.structured_content["status"] == "success"
        assert len(result.structured_content["input_confirmation_token"]) == 32
        assert len(result.content) == 2
        assert result.content[1].type == "image"
        assert result.content[1].mimeType == "image/png"

    async def test_returns_structured_error(self, monkeypatch):
        from tools.console import capture_console_screen

        async def fake_capture(server_id):
            return {"server_id": server_id, "status": "error", "message": "not configured"}

        monkeypatch.setattr("tools.console._capture_vnc_console", fake_capture)

        result = await capture_console_screen("console-host")
        assert result.structured_content["status"] == "error"
        assert len(result.content) == 1


class TestRunConsoleCommandSync:
    def test_rejects_empty_multiline_and_control_characters(self):
        from tools.console import _run_console_command_sync

        for command in ("", "echo one\necho two", "echo\ttab"):
            result = _run_console_command_sync("console-host", command, 1)
            assert result["status"] == "error"

    def test_command_and_password_are_not_in_argv(self, monkeypatch):
        import config

        _configure_console(config, vnc={"port": 5901, "key_delay": 0.02})

        def fake_run(args, **kwargs):
            assert args[2] == "run"
            assert "not-real" not in args
            assert "printf hello" not in args
            assert kwargs["input"] == "not-real\nprintf hello"
            assert args[-2:] == ["1.25", "0.02"]
            output_path = Path(args[5])
            PILImage.new("RGB", (800, 600), "black").save(output_path, format="PNG")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _run_console_command_sync

        result = _run_console_command_sync("console-host", "printf hello", 1.25)
        assert result["status"] == "success"
        assert result["operation"] == "run"

    def test_rejects_invalid_key_delay(self):
        import config

        _configure_console(config, vnc={"port": 5901, "key_delay": 1})
        from tools.console import _run_console_command_sync

        result = _run_console_command_sync("console-host", "true", 1)
        assert result["status"] == "error"
        assert "key delay" in result["message"]

    def test_invalid_png_preserves_sent_stage(self, monkeypatch):
        import config

        _configure_console(config)

        def fake_run(args, **_kwargs):
            Path(args[5]).write_bytes(b"not-a-png")
            return subprocess.CompletedProcess(args, 0, stdout="BM_VNC_STAGE=sent\n", stderr="")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _run_console_command_sync

        result = _run_console_command_sync("console-host", "true", 1)

        assert result["status"] == "error"
        assert result["command_sent"] is True
        assert result["input_state"] == "sent_unconfirmed"
        assert result["retry_safe"] is False

    def test_png_decode_failure_preserves_sent_stage(self, monkeypatch):
        import config

        _configure_console(config)

        def fake_run(args, **_kwargs):
            Path(args[5]).write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
            return subprocess.CompletedProcess(args, 0, stdout="BM_VNC_STAGE=sent\n", stderr="")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        from tools.console import _run_console_command_sync

        result = _run_console_command_sync("console-host", "true", 1)

        assert result["status"] == "error"
        assert result["command_sent"] is True
        assert result["retry_safe"] is False

    def test_png_read_failure_preserves_pager_input_stage(self, monkeypatch):
        import config

        _configure_console(config)

        def fake_run(args, **_kwargs):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="BM_VNC_STAGE=pager_input\n",
                stderr="",
            )

        def fail_read(_path):
            raise OSError("simulated read failure")

        monkeypatch.setattr("tools.console.subprocess.run", fake_run)
        monkeypatch.setattr("tools.console.Path.read_bytes", fail_read)
        from tools.console import _pager_action_sync

        result = _pager_action_sync("console-host", "quit", 1)

        assert result["status"] == "error"
        assert result["input_sent"] is True
        assert result["retry_safe"] is False


class TestConsoleCommandTools:
    async def test_run_returns_guarded_session_and_image(self, monkeypatch):
        from tools.console import run_console_command

        async def fake_run(server_id, command, wait_seconds):
            return {
                "server_id": server_id,
                "status": "success",
                "operation": "run",
                "image": b"\x89PNG\r\n\x1a\nimage-data",
            }

        monkeypatch.setattr("tools.console._run_console_command", fake_run)
        result = await run_console_command("console-host", "uname -a", _allow_input())

        assert result.structured_content["pager_active"] is True
        assert result.structured_content["command_sent"] is True
        assert result.structured_content["completion_confirmed"] is False
        assert result.structured_content["retry_safe"] is False
        assert result.structured_content["page"] == 1
        assert len(result.structured_content["session_id"]) == 32
        assert result.content[1].type == "image"

    async def test_second_run_is_blocked_while_pager_active(self, monkeypatch):
        from tools.console import run_console_command

        async def fake_run(*args):
            return {
                "server_id": args[0],
                "status": "success",
                "image": b"\x89PNG\r\n\x1a\nimage-data",
            }

        monkeypatch.setattr("tools.console._run_console_command", fake_run)
        await run_console_command("console-host", "one", _allow_input())
        result = await run_console_command("console-host", "two", _allow_input("second"))
        assert result.structured_content["status"] == "error"
        assert "already active" in result.structured_content["message"]

    async def test_pager_actions_require_token_and_quit_session(self, monkeypatch):
        from tools.console import console_pager_action, run_console_command

        async def fake_run(*args):
            return {
                "server_id": args[0],
                "status": "success",
                "image": b"\x89PNG\r\n\x1a\nimage-data",
            }

        async def fake_action(server_id, action, wait):
            return {
                "server_id": server_id,
                "status": "success",
                "operation": "pager",
                "image": b"\x89PNG\r\n\x1a\nimage-data",
            }

        monkeypatch.setattr("tools.console._run_console_command", fake_run)
        monkeypatch.setattr("tools.console._pager_action", fake_action)

        started = await run_console_command("console-host", "uname -a", _allow_input())
        token = started.structured_content["session_id"]

        rejected = await console_pager_action("console-host", "wrong", "next_page")
        assert rejected.structured_content["status"] == "error"

        next_page = await console_pager_action("console-host", token, "next_page")
        assert next_page.structured_content["page"] == 2
        assert next_page.structured_content["pager_active"] is True

        quit_result = await console_pager_action("console-host", token, "quit")
        assert quit_result.structured_content["pager_active"] is False

        no_session = await console_pager_action("console-host", token, "refresh")
        assert no_session.structured_content["status"] == "error"

    async def test_abandon_clears_session_without_vnc_action(self, monkeypatch):
        from tools.console import console_pager_action, run_console_command

        async def fake_run(*args):
            return {"server_id": args[0], "status": "success"}

        async def fail_action(*args):
            raise AssertionError("abandon must not send a VNC key")

        monkeypatch.setattr("tools.console._run_console_command", fake_run)
        monkeypatch.setattr("tools.console._pager_action", fail_action)

        started = await run_console_command("console-host", "true", _allow_input())
        token = started.structured_content["session_id"]
        result = await console_pager_action("console-host", token, "abandon")
        assert result.structured_content["pager_active"] is False
        assert "no VNC key" in result.structured_content["message"]

    async def test_command_requires_and_consumes_recent_screen_token(self, monkeypatch):
        from tools.console import run_console_command

        called = False

        async def fake_run(*args):
            nonlocal called
            called = True
            return {"server_id": args[0], "status": "success"}

        monkeypatch.setattr("tools.console._run_console_command", fake_run)
        missing = await run_console_command("console-host", "true", "wrong")
        assert missing.structured_content["command_sent"] is False
        assert missing.structured_content["retry_safe"] is True
        assert called is False

        token = _allow_input()
        sent = await run_console_command("console-host", "true", token)
        assert sent.structured_content["command_sent"] is True
        assert called is True

    async def test_invalid_command_does_not_consume_screen_token(self):
        from tools.console import _CONSOLE_PREFLIGHTS, run_console_command

        token = _allow_input()
        result = await run_console_command("console-host", "bad\ncommand", token)

        assert result.structured_content["command_sent"] is False
        assert result.structured_content["retry_safe"] is True
        assert _CONSOLE_PREFLIGHTS["console-host"]["token"] == token

    async def test_expired_pager_is_not_given_blind_input(self, monkeypatch):
        from tools.console import _ACTIVE_PAGERS, console_pager_action

        _ACTIVE_PAGERS["console-host"] = {
            "session_id": "old",
            "page": 1,
            "expires_at": time.monotonic() - 1,
        }

        async def fail_action(*args):
            raise AssertionError("expired session must not send input")

        monkeypatch.setattr("tools.console._pager_action", fail_action)
        result = await console_pager_action("console-host", "old", "quit")
        assert result.structured_content["status"] == "error"
        assert result.structured_content["input_sent"] is False

    async def test_expired_pager_blocks_commands_until_visually_authorized_quit(self, monkeypatch):
        from tools.console import _ACTIVE_PAGERS, console_pager_action, run_console_command

        _ACTIVE_PAGERS["console-host"] = {
            "session_id": "old",
            "page": 1,
            "expires_at": time.monotonic() - 1,
        }
        token = _allow_input()
        command_called = False

        async def fail_command(*_args):
            nonlocal command_called
            command_called = True
            raise AssertionError("an expired remote pager must block a new command")

        async def fake_action(server_id, action, _wait):
            assert action == "quit"
            return {"server_id": server_id, "status": "success", "input_sent": True}

        monkeypatch.setattr("tools.console._run_console_command", fail_command)
        monkeypatch.setattr("tools.console._pager_action", fake_action)

        blocked = await run_console_command("console-host", "true", token)
        assert blocked.structured_content["status"] == "error"
        assert blocked.structured_content["pager_session_expired"] is True
        assert command_called is False

        recovered = await console_pager_action(
            "console-host",
            "old",
            "quit",
            confirmation_token=token,
        )
        assert recovered.structured_content["status"] == "success"
        assert recovered.structured_content["pager_active"] is False
        assert "console-host" not in _ACTIVE_PAGERS


def test_worker_command_is_shell_quoted_and_forces_restricted_less():
    from tools.vnc_capture_worker import _command_line

    line = _command_line("printf '%s\\n' \"$HOME\"")
    assert "LESSSECURE=1 less -R -M" in line
    assert "</dev/null" in line
    assert "[baremetal-mcp exit=%s]" in line
    assert "'\"'\"'" in line
    assert subprocess.run(["sh", "-n", "-c", line], check=False).returncode == 0


def test_worker_types_minus_as_named_key():
    from tools.vnc_capture_worker import _type_text

    class Client:
        def __init__(self):
            self.keys = []
            self.pauses = []
            self.factory = type("Factory", (), {"force_caps": False})()

        def keyPress(self, key):
            self.keys.append(key)

        def pause(self, delay):
            self.pauses.append(delay)

    client = Client()
    _type_text(client, "a-b", 0.01)
    assert client.keys == ["a", "minus", "b"]
    assert client.pauses == [0.01, 0.01, 0.01]
    assert client.factory.force_caps is True


def test_worker_builds_ipv4_and_ipv6_addresses():
    from tools.vnc_capture_worker import _server_address

    assert _server_address("10.0.0.20", 5901) == "10.0.0.20::5901"
    assert _server_address("2001:db8::1", 5901) == "[2001:db8::1]::5901"
