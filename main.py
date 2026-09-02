#!/usr/bin/env python3
"""
MCP Redfish Server - Entry point.

Run with:
    fastmcp run -t stdio ./main.py
    fastmcp run --port 5004 --host 127.0.0.1 -t streamable-http ./main.py
"""

from config import mcp, _load_config, logger

# ``fastmcp run main.py`` imports this module and extracts ``mcp`` without
# calling ``main()``.  Load configuration before registering resources so the
# advertised CLI path has the same state as the console-script entry point.
_load_config()

# Import resources to register them
import resources  # noqa: E402,F401

# Import all tools to register them with the MCP server
import tools  # noqa: E402,F401


def main():
    """Start the MCP Redfish server."""
    logger.info("Starting Simplified Redfish MCP Server")
    logger.info("MCP tools are registered dynamically from the tools package")
    _load_config()
    mcp.run()


if __name__ == "__main__":
    main()
