"""Tests for FastMCP module-import startup behavior."""

import importlib.util
from pathlib import Path


def test_fastmcp_module_import_loads_configuration(monkeypatch):
    import config

    calls = []
    monkeypatch.setattr(config, "_load_config", lambda: calls.append(True))
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("baremetal_mcp_startup_test", main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert calls == [True]
