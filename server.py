#!/usr/bin/env python3
"""Launcher for the davinci MCP stdio server."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from davinci_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
