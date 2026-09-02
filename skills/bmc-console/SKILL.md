---
name: bmc-console
description: Inspect BMC consoles and run explicitly authorized commands on one or more hosts through SOL or VNC, including guarded fallback and long-output handling.
---

# Work with BMC Consoles

Resolve the exact `server_id` values with `list_hosts`, `list_hosts_by_lab`, or `list_hosts_by_tag`. Keep the resolved target list visible before sending input.

For observation only, use `capture_console_screen`. Describe only the current framebuffer and distinguish an active prompt or error from old terminal scrollback. Capture again before concluding that a blank, stale, or changing screen is stuck.

For commands, require user authorization for the command and all targets. Commands must be single-line and non-interactive. Never include credentials or other secrets because console input is visible and may enter shell history.

Use `run_console_command_batch(server_ids, command)` first as its default dry run for both single-host and multi-host SOL/VSP work. After reviewing the resolved hosts, execute with `dry_run=false` and `confirm_command` exactly equal to `command`. For a long fleet operation, use `start_console_command_batch` with the same guard and poll `get_operation`. Do not recreate serial transport, authentication, marker, cleanup, concurrency, or retry logic in an ad hoc script.

Require the selected targets to resolve explicit YAML connection settings: `serial_console.transport` and `serial_console.port` for SOL/VSP, or `vnc.port` and `vnc.key_delay` for VNC. Usernames, BMC passwords, and the separate `vnc_password` must come from the secrets mapping or its credential profile; never substitute vendor defaults.

Review every per-host result and report:

- selected transport and failure phase;
- whether the command was sent;
- exit code and captured output, when confirmed;
- truncation and incomplete/ambiguous state;
- the tool's `retry_safe` decision.

Treat `command_sent=true` without a confirmed exit marker as an unknown outcome, not a failure safe to retry. Retry or fall back only when `retry_safe=true`, unless the user explicitly authorizes repeating a possibly completed command.

For a retry-safe serial failure, use VNC only when it is configured. Before every command, call `capture_console_screen`, inspect that newly returned image, and visibly confirm a focused shell prompt. Only then pass that capture's `input_confirmation_token` as `confirmation_token` to `run_console_command(server_id, command, confirmation_token, wait_seconds)`. The token is one-use: obtain and inspect a fresh capture for each subsequent command, even on the same host.

Inspect every returned page with `console_pager_action`, record the visible exit marker, and close the tracked pager. If a pager session expires, call `get_console_session_status(server_id)`, then make and inspect a fresh post-expiry `capture_console_screen`. Pass that new token to `console_pager_action` for one recovery, quit, interrupt, or visually justified abandon action; status lookup alone never authorizes more input. VNC is visual evidence, so do not invent text that is not legible. Do not change BMC console security or encryption settings to make a transport work.
