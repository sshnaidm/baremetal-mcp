# Portable Agent Skills

The repository ships model-neutral [Agent Skills](https://agentskills.io/specification). Each skill is a directory containing a `SKILL.md` with standard YAML frontmatter (`name` and `description`) followed by Markdown instructions. No client-specific metadata is required.

## Available skills

- [`bmc-console`](skills/bmc-console/SKILL.md) — inspect BMC consoles and run explicitly authorized single-host or batch commands through SOL or VNC.
- [`boot-image-network-inventory`](skills/boot-image-network-inventory/SKILL.md) — boot an exact ISO, wait for its shell, collect network data, and save a searchable snapshot.
- [`collect-network-inventory`](skills/collect-network-inventory/SKILL.md) — collect or refresh network snapshots on systems that are already running.
- [`export-dell-hardware-inventory`](skills/export-dell-hardware-inventory/SKILL.md) — export validated Dell hardware XML and a manifest for one or more hosts.
- [`inspect-firmware`](skills/inspect-firmware/SKILL.md) — choose the appropriate Redfish tool for firmware checks and comparisons.
- [`update-dell-firmware`](skills/update-dell-firmware/SKILL.md) — submit an explicitly requested Dell firmware update with guarded reboot handling.
- [`junos-switch`](skills/junos-switch/SKILL.md) — query Juniper Junos switches.
- [`dell-switch`](skills/dell-switch/SKILL.md) — run read-only queries or confirmed unrestricted Dell SmartFabric OS10 command sequences.
