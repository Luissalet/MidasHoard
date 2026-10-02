"""`python -m midas_hoard` — run the app with uvicorn on 127.0.0.1 (the shared Hoard Link launcher)."""

from __future__ import annotations

from .hoard_link.service import run_main


def main() -> int:
    return run_main(service="midas-hoard", package="midas_hoard", default_port=5192, app_factory="midas_hoard.main:create_app",
                    data_dir_env="MIDAS_DATA_DIR", port_env="MIDAS_PORT", open_browser_default=False, title="Midas's Hoard")


if __name__ == "__main__":
    raise SystemExit(main())
