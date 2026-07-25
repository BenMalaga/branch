"""Check the assistant's arithmetic against what the tools actually returned.

The agent writes prose. Prose is where a language model is most likely to round,
paraphrase, or quietly invent a figure that no tool ever computed. The audit
trail already records every tool run, so the answer can be checked against it
mechanically, with no second model call and no API key. That matters: it means
even a small local model gets checked, and the check itself can never hallucinate.

Numbers are matched against three legitimate origins: values the tools returned,
values the user supplied in the question, and counts of features produced. A
number that matches none of those is not proof of a lie (it may be a simple sum
the model did in its head) but it is the thing a reader should look at first, so
it is surfaced rather than buried.
"""
from __future__ import annotations

import re

# 1,234.56 / $450000 / 12.5% / 3.2k / 1.4M, with the decoration attached
NUMBER = re.compile(r"[-+]?\$?\d[\d,]*\.?\d*\s*(?:%|k\b|m\b|million\b|bn\b|billion\b)?",
                    re.IGNORECASE)

# Numbers that carry no claim, so flagging them would only create noise.
ORDINALS = re.compile(r"\b(first|second|third|step|top)\s*$", re.IGNORECASE)


def parse_number(raw: str) -> float | None:
    """Turn a written number into a comparable float, honoring its suffix."""
    t = raw.strip().lower().replace("$", "").replace(",", "").strip()
    mult = 1.0
    if t.endswith("%"):
        t = t[:-1].strip()
    elif t.endswith(("bn", "billion")):
        t = re.sub(r"(bn|billion)$", "", t).strip()
        mult = 1e9
    elif t.endswith(("m", "million")):
        t = re.sub(r"(m|million)$", "", t).strip()
        mult = 1e6
    elif t.endswith("k"):
        t = t[:-1].strip()
        mult = 1e3
    try:
        return float(t) * mult
    except ValueError:
        return None


def _walk(value, out: list[float], depth: int = 0) -> None:
    """Collect every number anywhere inside a tool result or recipe."""
    if depth > 6:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, str):
        n = parse_number(value)
        if n is not None:
            out.append(n)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in ("geometry", "coordinates", "bbox"):
                continue        # coordinates are not claims about the world
            _walk(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:200]:
            _walk(v, out, depth + 1)


def known_values(steps: list, question: str = "") -> list[float]:
    """Every number the answer is entitled to use."""
    out: list[float] = []
    for st in steps or []:
        _walk(st.get("recipe"), out)
        _walk(st.get("params"), out)
        result = st.get("result")
        if isinstance(result, dict):
            feats = result.get("features")
            if isinstance(feats, list):
                out.append(float(len(feats)))       # "237 restaurants"
                for f in feats[:200]:
                    _walk(f.get("properties"), out, 3)
            else:
                _walk(result, out)
        st_count = st.get("feature_count")
        if isinstance(st_count, (int, float)):
            out.append(float(st_count))
    for raw in NUMBER.findall(question or ""):
        n = parse_number(raw)
        if n is not None:
            out.append(n)
    return out


def matches(value: float, pool: list[float]) -> bool:
    """Does this number correspond to something a tool actually produced?

    Rounding in prose is legitimate ("about 1.2 million" for 1,234,567), so the
    comparison is relative, with an absolute floor for small counts.
    """
    for known in pool:
        if known == value:
            return True
        scale = max(abs(known), abs(value), 1.0)
        if abs(known - value) <= max(0.02 * scale, 0.5):
            return True
        # a percentage quoted from a fraction, or the other way round
        if known and abs(known * 100.0 - value) <= max(0.02 * abs(value), 0.5):
            return True
    return False


def verify(answer: str, steps: list, question: str = "") -> dict:
    """Trace each number in the answer back to a tool run.

    Returns the count traced, the count checked, and any orphans with the phrase
    they appeared in, so a reader can go straight to the doubtful sentence.
    """
    pool = known_values(steps, question)
    checked, traced, orphans = 0, 0, []
    for m in NUMBER.finditer(answer or ""):
        raw = m.group(0).strip()
        value = parse_number(raw)
        if value is None:
            continue
        before = answer[max(0, m.start() - 12):m.start()]
        if ORDINALS.search(before):
            continue                      # "step 2", "the first 3"
        if 1900 <= value <= 2100 and float(value).is_integer() and "%" not in raw:
            continue                      # a year is not a measurement
        checked += 1
        if matches(value, pool):
            traced += 1
        else:
            start = max(0, m.start() - 45)
            end = min(len(answer), m.end() + 45)
            orphans.append({"value": raw, "phrase": answer[start:end].strip()})
    return {"checked": checked, "traced": traced, "orphans": orphans[:6],
            "clean": checked > 0 and not orphans}
