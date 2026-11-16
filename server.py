#!/usr/bin/env python3
"""Start the deepseek-worker MCP server on stdin and stdout."""

import sys

from deepseek_worker.mcp_server import main

if __name__ == "__main__":
    sys.exit(main())
