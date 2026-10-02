"""Stdio MCP bridge for Midas's Hoard.

It never opens the database: every tool call is proxied to the running app (`POST /api/agent/call`) with the
Bearer token from `<DATA_DIR>/mcp-token`. The tool list comes from `GET /api/agent/tools` (refreshed while the
bridge runs), so the bridge and the app can never disagree. When nothing answers, the bridge starts the app
itself (`python -m midas_hoard`, detached, on the port of MIDAS_URL) and waits for it;
MIDAS_BRIDGE_AUTOSTART=0 turns that off. The bridge itself is the shared catalogue bridge of Hoard Link.
"""

from __future__ import annotations

import sys

from midas_hoard.hoard_link.bridge import CatalogBridge


def main() -> int:
    CatalogBridge(app="midas", service="midas-hoard", package="midas_hoard", default_port=5192,
                  data_dir_env="MIDAS_DATA_DIR", title="Midas's Hoard", root=__file__).run_bridge()
    return 0


if __name__ == "__main__":
    sys.exit(main())
