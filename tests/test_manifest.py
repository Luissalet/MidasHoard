"""faustus-plugin.json, the README and the docs stay in sync with the code."""

import json
import re
from pathlib import Path

from midas_hoard import SERVICE
from midas_hoard.agent_tools import TOOLS
from midas_hoard.config import DEFAULT_PORT

ROOT = Path(__file__).resolve().parent.parent
BANNED = ("chatgpt", "claude", "openai", "anthropic", "lm studio", "odysseus", "gemini", "copilot", "bloomberg", "tradingview", "yahoo finance")
FINAL_TOOLS = {
    "midas_status", "market_providers", "market_search", "market_fetch", "market_series", "market_compare", "snapshots_list", "thesis_create",
    "thesis_get", "thesis_list", "thesis_update", "thesis_evidence_add", "thesis_check", "strategy_validate", "strategy_save", "strategies_list",
    "backtest_run", "backtest_validate", "experiments_list", "committee_run", "portfolio_set", "portfolio_analyze", "report_export",
}


def test_manifest_matches_code():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "midas" and manifest["name"] == "Midas's Hoard"
    assert manifest["app"]["health"]["expect"]["service"] == SERVICE == "midas-hoard"
    assert manifest["app"]["url_default"].endswith(f":{DEFAULT_PORT}") and DEFAULT_PORT == 5192
    assert manifest["app"]["launch_hint"]["env"]["PORT_STRICT"] == "1"


def test_manifest_has_only_allowed_top_level_keys():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= {"schema", "id", "name", "purpose", "capabilities", "placeholders", "defaults", "app", "mcp", "notes"}
    assert manifest["mcp"]["env"].keys() >= {"MIDAS_URL", "MIDAS_TOKEN_FILE"}


def test_tool_names_are_the_contract():
    assert {t.name for t in TOOLS} == FINAL_TOOLS and len(TOOLS) == 23


def test_readmes_and_api_doc_list_every_tool():
    for name in ("README.md", "README.es.md", "docs/API.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for tool in TOOLS:
            assert f"`{tool.name}`" in text, (name, tool.name)


def test_no_other_products_in_docs_manifest_or_code():
    files = [ROOT / "faustus-plugin.json", ROOT / "README.md", ROOT / "README.es.md", ROOT / "docs" / "API.md", ROOT / "pyproject.toml"]
    files += [p for p in (ROOT / "midas_hoard").rglob("*.py") if "hoard_link" not in p.parts]
    files += [p for p in (ROOT / "client" / "src").rglob("*") if p.is_file()]
    files += [ROOT / "mcp_server.py", *(ROOT / "scripts").glob("*.py")]
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for word in BANNED:
            if word == "yahoo finance":
                continue  # the yahoo provider is an optional, unofficial source that is credited by name
            assert not re.search(rf"\b{re.escape(word)}\b", text), (path, word)


def test_license_and_readme_credit_the_data_providers():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for credit in ("FRED", "European Central Bank", "CoinGecko", "Stooq"):
        assert credit in readme
    assert "Luis María Salete Cuartero" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "not advice" in readme.lower()
