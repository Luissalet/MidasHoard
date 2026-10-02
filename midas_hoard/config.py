"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .hoard_link.appconfig import AppPaths, env_flag, env_float, env_int, env_str
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5192


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
    def paths(self) -> AppPaths:
        """The shared data-folder layout (``midas.db``, ``mcp-token``, ``url``, ``logs/``, ``backend.json``)."""
        return AppPaths("midas", REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def db_path(self) -> Path:
        return self.paths.db_path

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def url_path(self) -> Path:
        return self.paths.url_path

    @property
    def logs_dir(self) -> Path:
        return self.paths.logs_dir

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
        return self.paths.backend_json_path

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = env_str("MIDAS_DATA_DIR") or ""
        port = env_int("MIDAS_PORT", "PORT", default=DEFAULT_PORT)
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        timeout = env_float("MIDAS_HTTP_TIMEOUT_S", default=20.0)
        ttl = env_int("MIDAS_CACHE_TTL_S", default=6 * 3600)
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=port,
            port_strict=env_flag("PORT_STRICT"),
            allowed_hosts=parse_allowed_hosts(env_str("MIDAS_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
            http_timeout_s=timeout if 2.0 <= timeout <= 120.0 else 20.0,
            cache_ttl_s=ttl if 0 <= ttl <= 30 * 86400 else 6 * 3600,
            offline=env_flag("MIDAS_OFFLINE"),
        )
