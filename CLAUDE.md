# CLAUDE.md

Read `AGENTS.md` for complete project documentation — architecture, tools, configuration, and operational guidance.

This file contains Claude Code-specific notes only.

## Skills

`skills/update-dell-firmware/SKILL.md` — guided workflow for Dell firmware updates using the tool chain: `get_vendor` → `list_isos`/`dell_list_url` → `dell_update_firmware`.
`skills/junos-switch/SKILL.md` — guided workflow for querying Junos switches via `junos_run_command`.
`SKILLS.md` — check firmware versions workflow: when to use `get_system_info` vs `get_firmware_inventory`.
