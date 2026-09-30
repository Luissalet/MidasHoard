"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5192


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(raw: str, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def _float(raw: str, default: float, low: float, high: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


@dataclass
class Config:
    """Everything the process needs before the database exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    http_timeout_s: float = 20.0
    cache_ttl_s: int = 6 * 3600  # on-disk HTTP response cache lifetime
    offline: bool = False  # never touch the network (tests, flights): providers answer provider_unavailable

    @property
    def db_path(self) -> Path:
        return self.data_dir / "midas.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "mcp-token"

    @property
    def url_path(self) -> Path:
        return self.data_dir / "url"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def backend_json_path(self) -> Path:
        return self.data_dir / "backend.json"

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = _env("MIDAS_DATA_DIR")
        port = _int(_env("MIDAS_PORT") or _env("PORT") or str(DEFAULT_PORT), DEFAULT_PORT, 1, 65535)
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=port,
            port_strict=_env("PORT_STRICT") == "1",
            allowed_hosts=parse_allowed_hosts(_env("MIDAS_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
            http_timeout_s=_float(_env("MIDAS_HTTP_TIMEOUT_S"), 20.0, 2.0, 120.0),
            cache_ttl_s=_int(_env("MIDAS_CACHE_TTL_S"), 6 * 3600, 0, 30 * 86400),
            offline=_env("MIDAS_OFFLINE") == "1",
        )
