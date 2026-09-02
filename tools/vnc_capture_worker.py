#!/usr/bin/env python3
"""One-shot vncdotool worker used by the BMC console tools."""

import sys
from pathlib import Path
import time

_PAGER_KEYS = {
    "next_page": "space",
    "previous_page": "b",
    "first_page": "g",
    "last_page": "G",
    "quit": "q",
    "interrupt": "ctrl-c",
}


def _server_address(host: str, port: int) -> str:
    """Build vncdotool's explicit-port address syntax."""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}::{port}"


def _shell_quote(value: str) -> str:
    """Quote a string for one POSIX shell argument."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _command_line(command: str) -> str:
    """Run a non-interactive command inside a forced, restricted less pager."""
    quoted_command = _shell_quote(command)
    return (
        "if command -v less >/dev/null 2>&1; then "
        "{ sh -c " + quoted_command + "; __bm_rc=$?; printf '\\n[baremetal-mcp exit=%s]\\n' \"$__bm_rc\"; } "
        "</dev/null 2>&1 | LESSSECURE=1 less -R -M; "
        "else printf '\\n[baremetal-mcp error: less is required]\\n'; fi"
    )


def _type_text(client, value: str, delay: float) -> None:
    """Type text through RFB without using the remote clipboard."""
    # iDRAC's virtual keyboard needs explicit Shift events for uppercase and
    # symbols such as >, &, $, braces, and pipe.
    client.factory.force_caps = True
    for char in value:
        client.keyPress("minus" if char == "-" else char)
        if delay:
            client.pause(delay)


def _capture(client, output: Path, wait: float) -> None:
    if wait:
        client.pause(wait)
    client.captureScreen(output, format="PNG")


def main() -> int:
    """Perform one VNC operation, reading sensitive input from stdin."""
    if len(sys.argv) < 6:
        print(
            "usage: vnc_capture_worker.py MODE HOST PORT OUTPUT TIMEOUT [MODE_ARGS]",
            file=sys.stderr,
        )
        return 2

    mode, host, port_raw, output_raw, timeout_raw, *mode_args = sys.argv[1:]
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        print("VNC password was not provided", file=sys.stderr)
        return 2

    if mode == "capture":
        if mode_args:
            print("capture mode takes no additional arguments", file=sys.stderr)
            return 2
        command = None
        wait = 0.0
        key = None
        key_delay = 0.0
    elif mode == "run":
        if len(mode_args) != 2:
            print("run mode requires WAIT and KEY_DELAY", file=sys.stderr)
            return 2
        command = sys.stdin.read().rstrip("\r\n")
        if not command:
            print("Console command was not provided", file=sys.stderr)
            return 2
        wait = float(mode_args[0])
        key_delay = float(mode_args[1])
        key = None
    elif mode == "pager":
        if len(mode_args) != 2 or mode_args[0] not in {*_PAGER_KEYS, "refresh"}:
            print("pager mode requires a supported ACTION and WAIT", file=sys.stderr)
            return 2
        command = None
        key = _PAGER_KEYS.get(mode_args[0])
        wait = float(mode_args[1])
        key_delay = 0.0
    else:
        print(f"Unsupported VNC worker mode: {mode}", file=sys.stderr)
        return 2

    from vncdotool import api

    try:
        with api.connect(
            _server_address(host, int(port_raw)),
            password=password,
            timeout=float(timeout_raw),
        ) as client:
            if mode == "run":
                print("BM_VNC_STAGE=typing", flush=True)
                _type_text(client, _command_line(command), key_delay)
                client.keyPress("enter")
                print("BM_VNC_STAGE=sent", flush=True)
            elif key is not None:
                print("BM_VNC_STAGE=pager_input", flush=True)
                client.keyPress(key)
            _capture(client, Path(output_raw), wait)
        # disconnect() is scheduled on Twisted's reactor. Give it time to send
        # a clean close before shutdown(); otherwise some iDRAC versions retain
        # a ghost VNC session until VNCServer.Timeout and make the next session
        # read-only.
        time.sleep(0.5)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        api.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
