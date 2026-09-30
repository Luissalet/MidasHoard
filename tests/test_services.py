from __future__ import annotations

import sqlite3

import pytest

from conftest import T0, make_config, tool
from midas_hoard.config import Config, DEFAULT_PORT
from midas_hoard.db import Database
from midas_hoard.errors import MidasError
from midas_hoard.services import Services


def test_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MIDAS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("MIDAS_PORT", "6123")
    monkeypatch.setenv("PORT_STRICT", "1")
    monkeypatch.setenv("MIDAS_OFFLINE", "1")
    monkeypatch.setenv("MIDAS_ALLOWED_HOSTS", "midas.local")
    c = Config.from_env()
    assert c.port == 6123 and c.port_strict and c.offline and c.data_dir_configured
    assert c.db_path == tmp_path / "d" / "midas.db" and "midas.local" in c.allowed_hosts
    monkeypatch.delenv("MIDAS_PORT")
    monkeypatch.delenv("MIDAS_DATA_DIR")
    assert Config.from_env().port == DEFAULT_PORT == 5192


def test_database_migrations_are_idempotent_and_wal(tmp_path):
    db = Database(tmp_path / "x.db")
    v = db.version()
    db.close()
    db2 = Database(tmp_path / "x.db")
    assert db2.version() == v >= 3
    assert db2.one("PRAGMA journal_mode")["journal_mode"].lower() == "wal"
    tables = {r["name"] for r in db2.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "snapshots", "symbols", "theses", "thesis_evidence", "thesis_checks", "committee_runs", "strategies", "experiments", "portfolios"} <= tables
    db2.set_setting("k", "v")
    assert db2.get_setting("k") == "v" and db2.get_setting("none", "d") == "d"
    db2.close()


def test_transaction_rolls_back(tmp_path):
    db = Database(tmp_path / "x.db")
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("INSERT INTO settings(key, value) VALUES ('a', '1')")
            raise RuntimeError("boom")
    assert db.get_setting("a") is None
    db.close()


def test_seeded_symbols_cover_every_provider(svc):
    providers = {r["provider"] for r in svc.db.query("SELECT DISTINCT provider FROM symbols")}
    assert {"stooq", "fred", "ecb", "coingecko", "fake"} <= providers
    assert svc.counts()["symbols"] >= 40


def test_settings_persist_language_and_offline(config, link):
    a = Services(config, link=link, clock_fn=lambda: T0)
    a.update_settings({"language": "en", "offline": True})
    a.stop()
    b = Services(config, link=link, clock_fn=lambda: T0)
    assert b.get_settings()["language"] == "en" and b.http.offline is True
    b.stop()


def test_backend_json_is_written(svc):
    svc.update_settings({"backend": {"url": "http://127.0.0.1:1234"}})
    assert svc.get_settings()["backend"]["url"] == "http://127.0.0.1:1234"


def test_counts_grow(svc, fetch):
    before = svc.counts()["snapshots"]
    fetch()
    assert svc.counts()["snapshots"] == before + 1


def test_family_ref_evidence_is_validated(svc, fetch):
    fetch()
    t = tool(svc, "thesis_create", title="Has family ref", claim="claim text", rival="A rival that also fits the facts.", assets=["fake.up"], as_of="2023-06-30")
    ok = tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="family_ref", ref="hoard://vitruvius/tokens/abc", title="Design tokens")
    assert ok["added"]["kind"] == "family_ref"
    with pytest.raises(MidasError):
        tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="family_ref", ref="not a ref")
