"""Duplicates by mechanism, not only by spec hash.

Two experiments with different names, different families and different parameters can still be the same idea. The spec of
each is reduced to name-free features (indicator kinds, rule leaves with indicator names replaced by their kind, universe
symbols) and compared with the Jaccard overlap of each part. The free text (name, family, note) is compared on top of that:
with embeddings when an embedder is available (Hoard Link), otherwise with a TF-IDF cosine over the experiments log
(standard library only). The result is a risk level, never a block.

``mechanism_score`` alone drives the *count* of variants in the multiple-testing correction, so that count is reproducible;
the text similarity only raises the displayed risk.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Callable, Optional

W_INDICATORS, W_RULES, W_UNIVERSE = 0.30, 0.50, 0.20
HIGH_MECHANISM = 0.75
MEDIUM_MECHANISM = 0.45
TEXT_TFIDF = 0.80
TEXT_EMBEDDING = 0.88
LEVELS = ("low", "medium", "high")
METHOD = ("mechanism: Jaccard overlap of indicator kinds (30 %), rule leaves compared by indicator kind (50 %) and universe symbols (20 %); "
          f"high from {HIGH_MECHANISM}, medium from {MEDIUM_MECHANISM}; the similarity of names and notes (embeddings if available, else TF-IDF) raises the level but never lowers it")

STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or the to with without de del el la las los un una y o en con sin por para que se "
    "su sus al es strategy estrategia test prueba spec backtest run".split())
TOKEN_RE = re.compile(r"[a-zà-ÿ][a-z0-9à-ÿ]+")


# ------------------------------------------------------------------------------------------ features
def _operand(value: Any, ind_kind: dict[str, str]) -> str:
    if isinstance(value, bool):
        return "#"
    if isinstance(value, (int, float)):
        return "#"
    if isinstance(value, str) and value in ind_kind:
        return f"ind({ind_kind[value]})"
    return f"field({value})"


def _leaves(node: Any, role: str, ind_kind: dict[str, str], negated: bool, out: set[str]) -> None:
    if not isinstance(node, dict):
        return
    for key in ("all", "any"):
        if key in node:
            for child in node[key]:
                _leaves(child, role, ind_kind, negated, out)
            return
    if "not" in node:
        _leaves(node["not"], role, ind_kind, not negated, out)
        return
    if "op" in node:
        out.add(f"{role}:{'!' if negated else ''}{_operand(node.get('left'), ind_kind)} {node['op']} {_operand(node.get('right'), ind_kind)}")


def spec_features(spec: dict[str, Any], symbol_of: Callable[[str], str] = lambda s: s) -> dict[str, Any]:
    """Name-free features of a spec: kinds of indicators, rule leaves, universe symbols, and the parameters kept apart."""
    inds = spec.get("indicators") or {}
    ind_kind: dict[str, str] = {}
    ind_tokens: set[str] = set()
    params: dict[str, Any] = {}
    for name, d in inds.items():
        if not isinstance(d, dict):
            continue
        src = d.get("source", "$asset")
        kind = f"{d.get('type')}:{d.get('field', 'close')}:{'asset' if src == '$asset' else 'cross'}"
        ind_kind[name] = kind
        ind_tokens.add(kind)
        for k, v in d.items():
            if k not in ("type", "field", "source"):
                params[f"{d.get('type')}.{k}"] = v
    rules: set[str] = set()
    _leaves(spec.get("entry"), "entry", ind_kind, False, rules)
    if "exit" in spec:
        _leaves(spec["exit"], "exit", ind_kind, False, rules)
    else:
        rules.add("exit:implicit")
    if spec.get("allow_short"):
        rules.add("short")
    universe = {str(symbol_of(s)).lower() for s in spec.get("universe") or []}
    parts = [str(spec.get("name") or "")]
    if spec.get("family") and spec["family"] != spec.get("name"):
        parts.append(str(spec["family"]))
    parts.append(str(spec.get("note") or ""))
    text = " ".join(p for p in parts if p)
    return {"indicators": ind_tokens, "rules": rules, "universe": universe, "params": params, "text": text}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def mechanism_score(fa: dict[str, Any], fb: dict[str, Any]) -> dict[str, Any]:
    ji, jr, ju = jaccard(fa["indicators"], fb["indicators"]), jaccard(fa["rules"], fb["rules"]), jaccard(fa["universe"], fb["universe"])
    score = round(W_INDICATORS * ji + W_RULES * jr + W_UNIVERSE * ju, 4)
    why: list[str] = []
    shared_ind = sorted(fa["indicators"] & fb["indicators"])
    if shared_ind:
        why.append(f"indicators overlap {ji * 100:.0f} % ({', '.join(i.split(':')[0] for i in shared_ind)})")
    if jr >= 0.999:
        why.append("entry/exit rules are identical once the indicators are compared by kind, not by name")
    elif jr > 0:
        why.append(f"entry/exit rules overlap {jr * 100:.0f} %")
    if ju >= 0.999:
        why.append("same universe")
    elif ju > 0:
        why.append(f"universe overlap {ju * 100:.0f} %")
    elif score >= HIGH_MECHANISM:
        why.append("same logic on a different universe: the same hypothesis tried again")
    diffs = [f"{k} {fa['params'][k]} vs {fb['params'][k]}" for k in sorted(set(fa["params"]) & set(fb["params"])) if fa["params"][k] != fb["params"][k]]
    if diffs and score >= MEDIUM_MECHANISM:
        why.append("only parameters differ: " + "; ".join(diffs[:3]))
    return {"score": score, "indicators": round(ji, 4), "rules": round(jr, 4), "universe": round(ju, 4), "why": why}


# ------------------------------------------------------------------------------------------ text
def tokens(text: str) -> list[str]:
    raw = re.sub(r"[_\-/.]+", " ", (text or "").lower())
    return [t for t in TOKEN_RE.findall(raw) if t not in STOPWORDS]


class TfIdf:
    """Smoothed TF-IDF over a corpus of short texts; cosine between any two of them (or a new one)."""

    def __init__(self, corpus: list[str]):
        docs = [set(tokens(t)) for t in corpus]
        self.n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(d)
        self.idf = {t: math.log((self.n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.default_idf = math.log(self.n + 1) + 1.0

    def vector(self, text: str) -> dict[str, float]:
        tf = Counter(tokens(text))
        vec = {t: c * self.idf.get(t, self.default_idf) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return float(sum(v * b.get(t, 0.0) for t, v in a.items()))


def cosine_dense(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return float(sum(x * y for x, y in zip(a, b)) / (na * nb)) if na and nb else 0.0


def text_scores(query: str, others: list[str], embedder: Optional[Callable[[list[str]], list[list[float]]]] = None) -> tuple[list[Optional[float]], str]:
    """Similarity of ``query`` to each text of ``others`` (None where a text has fewer than 2 informative words) and the method used."""
    usable = [len(tokens(t)) >= 2 for t in others]
    if len(tokens(query)) < 2 or not any(usable):
        return [None] * len(others), "none"
    if embedder is not None:
        try:
            idx = [i for i, u in enumerate(usable) if u]
            vecs = embedder([query] + [others[i] for i in idx])
            if len(vecs) == len(idx) + 1:
                out: list[Optional[float]] = [None] * len(others)
                for k, i in enumerate(idx):
                    out[i] = cosine_dense(vecs[0], vecs[k + 1])
                return out, "embedding"
        except Exception:  # noqa: BLE001 — an embedder that is down must never break registering an experiment
            pass
    model = TfIdf([query, *others])
    qv = model.vector(query)
    return [model.cosine(qv, model.vector(t)) if u else None for t, u in zip(others, usable)], "tfidf"


def level_for(mech: float, text: Optional[float], method: str) -> str:
    threshold = TEXT_EMBEDDING if method == "embedding" else TEXT_TFIDF
    text_hit = text is not None and text >= threshold
    if mech >= HIGH_MECHANISM or (mech >= MEDIUM_MECHANISM and text_hit):
        return "high"
    if mech >= MEDIUM_MECHANISM or text_hit:
        return "medium"
    return "low"


def risk_of(similar: list[dict[str, Any]]) -> str:
    best = 0
    for s in similar:
        best = max(best, LEVELS.index(s["level"]))
    return LEVELS[best]


def find_similar(spec_features_: dict[str, Any], candidates: list[dict[str, Any]], *, embedder: Optional[Callable[[list[str]], list[list[float]]]] = None,
                 limit: int = 5) -> dict[str, Any]:
    """Compare one spec with candidate experiments (dicts with ``features`` and the identifying fields); return the risk and the closest ones."""
    scores, method = text_scores(spec_features_["text"], [c["features"]["text"] for c in candidates], embedder)
    found = []
    for c, ts in zip(candidates, scores):
        m = mechanism_score(spec_features_, c["features"])
        level = level_for(m["score"], ts, method)
        if level == "low":
            continue
        why = list(m["why"])
        if ts is not None and ts >= (TEXT_EMBEDDING if method == "embedding" else TEXT_TFIDF):
            why.append(f"descriptions are similar ({method} cosine {ts:.2f})")
        found.append({"experiment": c["id"], "name": c["name"], "family": c["family"], "spec_hash": c["spec_hash"], "level": level,
                      "mechanism_score": m["score"], "text_score": None if ts is None else round(ts, 4), "text_method": method, "why": why})
    found.sort(key=lambda x: (-LEVELS.index(x["level"]), -x["mechanism_score"], -(x["text_score"] or 0.0)))
    return {"level": risk_of(found), "similar": found[:limit], "similar_total": len(found), "text_method": method,
            "method": METHOD,
            "note": "A high risk does not block anything: it is shown in the report and the experiments list, and the variants it implies are counted in the multiple-testing correction."}
