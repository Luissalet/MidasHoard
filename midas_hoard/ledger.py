"""The number ledger: every number in model-written text must be accounted for.

A number is *grounded* when it matches (within a rounding-aware tolerance, magnitude only, allowing the usual
percent/fraction scaling) an observed or derived value of the evidence pack; *cited* when it appears in the text of
the thesis or of an evidence item the user wrote; *proposed* when the author marks it ``[proposed]`` (an assumption
or target, not a fact); *cited to evidence* when followed by ``[E3]`` and E3 really contains it. Anything else is
**unmatched** and reported — never silently kept. Small counts (0-10, no unit) and evidence labels are ignored.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from .hoard_link import money

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
EVIDENCE_LABEL = re.compile(r"\bE\d+\b")
LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s+")
NUMBER = re.compile(r"(?<![A-Za-z_\d.])([-−+]?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)")
TAG = re.compile(r"^[^\[\n]{0,14}\[(proposed|E\d+(?:\s*,\s*E\d+)*)\]", re.I)
UNIT_AFTER = re.compile(r"^\s?(%|bps|bp|pp|x|×|k\b|m\b|bn\b|b\b)", re.I)


def _to_float(raw: str) -> tuple[float, int]:
    """Numeric value and the number of decimals written, read with the shared amount parser (dot-decimal context: market text
    writes ``1.085`` for an exchange rate and ``1,250`` for a thousand). ``ValueError`` when it is not a number."""
    value = money.parse_amount(raw.replace("\u2212", "-"), lang="en")
    if value is None:
        raise ValueError(raw)
    number = float(value)
    digits = len(re.sub(r"\D", "", raw))
    integer_digits = len(str(int(abs(number)))) if abs(number) >= 1 else 1
    return number, max(0, digits - integer_digits)  # digits written beyond the integer part: "12.50" -> 2, "1,250" -> 0


def extract_numbers(text: str) -> list[dict[str, Any]]:
    """Every number in ``text`` with its position, decimals, unit and tag (proposed / evidence labels)."""
    scrubbed = text
    scrubbed = ISO_DATE.sub(lambda m: " " * len(m.group(0)), scrubbed)
    scrubbed = EVIDENCE_LABEL.sub(lambda m: " " * len(m.group(0)), scrubbed)
    scrubbed = LIST_MARKER.sub(lambda m: " " * len(m.group(0)), scrubbed)
    out: list[dict[str, Any]] = []
    for m in NUMBER.finditer(scrubbed):
        sign, raw = m.group(1), m.group(2)
        try:
            value, decimals = _to_float(raw)
        except ValueError:
            continue
        if sign in ("-", "−"):
            value = -value
        after = scrubbed[m.end(): m.end() + 40]
        unit_m = UNIT_AFTER.match(after)
        unit = unit_m.group(1).lower() if unit_m else ""
        tag_m = TAG.match(text[m.end(): m.end() + 40])  # original text: the scrub blanks evidence labels
        tag = tag_m.group(1) if tag_m else ""
        out.append({"text": (sign + raw + (unit if unit in ("%",) else "")).replace("−", "-"), "value": value, "decimals": decimals,
                    "unit": unit, "tag": tag.lower() if tag.lower() == "proposed" else tag.upper(), "pos": m.start()})
    return out


def dates_in(text: str) -> list[str]:
    return [m.group(0) for m in ISO_DATE.finditer(text)]


def _candidates(v: float) -> list[tuple[float, str]]:
    """The values a written number may stand for: itself, in percent, as a fraction."""
    out = [(abs(v), "exact")]
    if abs(v) <= 5:
        out.append((abs(v) * 100.0, "percent"))
    if abs(v) >= 1:
        out.append((abs(v) / 100.0, "fraction"))
    return out


def _close(x: float, decimals: int, cand: float, tol_rel: float) -> bool:
    if cand == 0:
        return x == 0
    rounded = round(cand, decimals)
    if abs(rounded - x) < 10 ** (-decimals) * 0.5 + 1e-12:
        return True
    return abs(x - cand) <= tol_rel * abs(cand)


class Ledger:
    """Built once per evidence pack; ``check(text)`` audits any number of texts against it."""

    def __init__(self, values: Iterable[dict[str, Any]], cited_texts: dict[str, str], dates: Iterable[str] = (), *, tol_rel: float = 0.01):
        self.values = [v for v in values if isinstance(v.get("value"), (int, float)) and not (isinstance(v["value"], float) and math.isnan(v["value"]))]
        self.tol_rel = tol_rel
        self.dates = set(dates)
        self.years = {d[:4] for d in self.dates}
        self.cited: dict[str, list[dict[str, Any]]] = {}
        for label, text in cited_texts.items():
            nums = extract_numbers(text)
            self.cited[label] = nums
            self.dates.update(dates_in(text))
            self.years.update(d[:4] for d in dates_in(text))
            self.years.update(str(int(n["value"])) for n in nums if 1900 <= n["value"] <= 2100 and n["decimals"] == 0)

    def _match_value(self, n: dict[str, Any]) -> Optional[dict[str, Any]]:
        x = abs(n["value"])
        for v in self.values:
            for cand, how in _candidates(float(v["value"])):
                if _close(x, n["decimals"], cand, self.tol_rel):
                    return {"label": v.get("label", ""), "value": v["value"], "kind": v.get("kind", "observed"), "source": v.get("source", ""), "scaling": how}
        return None

    def _match_cited(self, n: dict[str, Any], only: Optional[list[str]] = None) -> Optional[str]:
        x = abs(n["value"])
        for label, nums in self.cited.items():
            if only is not None and label not in only:
                continue
            for c in nums:
                for cand, _ in _candidates(c["value"]):
                    if _close(x, n["decimals"], cand, self.tol_rel):
                        return label
        return None

    def check(self, text: str) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for d in dates_in(text):
            rows.append({"text": d, "status": "date" if d in self.dates else "unmatched_date", "kind": "date"})
        for n in extract_numbers(text):
            row: dict[str, Any] = {"text": n["text"], "value": n["value"], "pos": n["pos"]}
            is_year = n["decimals"] == 0 and 1900 <= abs(n["value"]) <= 2100 and not n["unit"]
            trivial = n["decimals"] == 0 and abs(n["value"]) <= 10 and not n["unit"] and not n["tag"]
            if n["tag"] == "proposed":
                row["status"] = "proposed"
            elif n["tag"].startswith("E"):
                labels = [t.strip() for t in n["tag"].split(",")]
                hit = self._match_cited(n, labels)
                row["status"], row["cited_to"] = ("cited", labels) if hit else ("cite_mismatch", labels)
            elif is_year:
                row["status"] = "date" if str(int(abs(n["value"]))) in self.years else "unmatched"
            else:
                hit = self._match_value(n)
                if hit:
                    row["status"], row["matched"] = hit["kind"] if hit["kind"] in ("observed", "derived") else "observed", hit
                else:
                    label = self._match_cited(n)
                    if label:
                        row["status"], row["cited_to"] = "cited", [label]
                    elif trivial:
                        row["status"] = "trivial"
                    else:
                        row["status"] = "unmatched"
            rows.append(row)
        bad = [r for r in rows if r["status"] in ("unmatched", "unmatched_date", "cite_mismatch")]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"ok": not bad, "numbers": len(rows), "counts": counts, "unmatched": [{"text": r["text"], "status": r["status"]} for r in bad], "detail": rows}
