"""A Python implementation of Logstash grok pattern expansion and matching.

It loads the official pattern files from logstash-patterns-core (data/patterns),
expands %{NAME:field:type} recursively, and matches with the `regex` module,
which supports the Oniguruma features grok patterns rely on (atomic groups,
possessive quantifiers, lookbehind, named groups). For byte-for-byte fidelity
with Logstash you can additionally enable Elasticsearch validation (es_validate),
which uses the same Joni engine as Logstash.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import regex

GROK_REF = regex.compile(r"%\{(\w+)(?::([^:}]+))?(?::(\w+))?\}")
# (?<name>...) but not lookbehinds (?<= / (?<!
NAMED_GROUP = regex.compile(r"\(\?<(?![=!])([^>]+)>")
INT_TYPES = {"int", "integer", "long"}
FLOAT_TYPES = {"float", "double"}
MATCH_TIMEOUT_S = 0.5


class GrokError(Exception):
    pass


class PatternLibrary:
    def __init__(self, patterns_dir: Path):
        self.patterns: dict[str, str] = {}
        for f in sorted(Path(patterns_dir).glob("*")):
            if not f.is_file():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name, _, definition = line.partition(" ")
                if name and definition:
                    self.patterns[name] = definition.strip()
        if not self.patterns:
            raise RuntimeError(f"No grok patterns found in {patterns_dir}. Run scripts/fetch_patterns.py")


@dataclass
class CompiledGrok:
    pattern: str
    rx: regex.Pattern
    groups: dict[str, tuple[str, str | None]]  # group name -> (field, type)


@dataclass
class LineResult:
    line: str
    matched: bool
    fields: dict = field(default_factory=dict)
    spans: list = field(default_factory=list)  # [start, end, field]
    error: str | None = None


def compile_grok(pattern: str, library: PatternLibrary, extra: dict | None = None) -> CompiledGrok:
    defs = {**library.patterns, **(extra or {})}
    groups: dict[str, tuple[str, str | None]] = {}
    counter = [0]

    def new_group(field_name: str, ftype: str | None) -> str:
        counter[0] += 1
        g = f"g{counter[0]}"
        groups[g] = (field_name, ftype)
        return g

    def expand(text: str, depth: int) -> str:
        if depth > 40:
            raise GrokError("Pattern nesting too deep (recursive pattern definition?)")

        def repl(m: regex.Match) -> str:
            name, fname, ftype = m.group(1), m.group(2), m.group(3)
            if name not in defs:
                raise GrokError(f"Unknown grok pattern %{{{name}}}")
            inner = expand(defs[name], depth + 1)
            if fname:
                return f"(?P<{new_group(fname, ftype)}>{inner})"
            return f"(?:{inner})"

        text = GROK_REF.sub(repl, text)
        # Oniguruma-style named captures, including Logstash field refs like (?<[a][b]>...)
        return NAMED_GROUP.sub(lambda m: f"(?P<{new_group(m.group(1), None)}>", text)

    expanded = expand(pattern, 0)
    try:
        rx = regex.compile(expanded, regex.V0)
    except regex.error as e:
        raise GrokError(f"Invalid regex: {e}") from e
    return CompiledGrok(pattern, rx, groups)


def _convert(value: str, ftype: str | None):
    try:
        if ftype in INT_TYPES:
            return int(value)
        if ftype in FLOAT_TYPES:
            return float(value)
    except ValueError:
        pass
    return value


def match_lines(cg: CompiledGrok, lines: list[str]) -> list[LineResult]:
    out = []
    for line in lines:
        try:
            m = cg.rx.search(line, timeout=MATCH_TIMEOUT_S)
        except TimeoutError:
            out.append(LineResult(line, False, error="timeout (catastrophic backtracking)"))
            continue
        if not m:
            out.append(LineResult(line, False))
            continue
        fields, spans = {}, []
        for g, (fname, ftype) in cg.groups.items():
            v = m.group(g)
            if v is None:
                continue
            if fname not in fields:  # first capture wins, like grok's default
                fields[fname] = _convert(v, ftype)
            s, e = m.span(g)
            spans.append([s, e, fname])
        out.append(LineResult(line, True, fields, _outermost(spans)))
    return out


def _outermost(spans):
    """Keep only spans not contained in another span, for highlighting."""
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    keep, end = [], -1
    for s in spans:
        if s[0] >= end and s[1] > s[0]:
            keep.append(s)
            end = s[1]
    return keep


def score(pattern: str, results: list[LineResult]) -> dict:
    """Heuristic quality score: match rate first, then structure and specificity."""
    total = len(results) or 1
    matched = [r for r in results if r.matched]
    rate = len(matched) / total
    greedy = len(regex.findall(r"%\{(?:GREEDYDATA|DATA)\b", pattern))
    anchored = pattern.startswith("^") and pattern.rstrip().endswith("$")
    nfields = max((len(r.fields) for r in matched), default=0)
    typed = len(regex.findall(r"%\{\w+:[^:}]+:(?:int|integer|long|float|double)\}", pattern))
    coverage = 0.0
    if matched:
        cov = []
        for r in matched:
            covered = sum(e - s for s, e, _ in r.spans)
            cov.append(covered / max(len(r.line), 1))
        coverage = sum(cov) / len(cov)
    s = rate * 100 + min(nfields, 15) * 2 + typed * 2 + (6 if anchored else 0) - greedy * 4 + coverage * 10
    return {
        "match_rate": round(rate, 3), "matched": len(matched), "total": len(results),
        "fields": nfields, "typed_fields": typed, "greedy_parts": greedy,
        "anchored": anchored, "coverage": round(coverage, 3), "score": round(s, 1),
    }


def evaluate(pattern: str, lines: list[str], library: PatternLibrary, extra: dict | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        cg = compile_grok(pattern, library, extra)
    except GrokError as e:
        return {"ok": False, "error": str(e), "results": [], "metrics": {"match_rate": 0, "score": -100}}
    results = match_lines(cg, lines)
    ms = (time.perf_counter() - t0) * 1000
    metrics = score(pattern, results)
    metrics["ms"] = round(ms, 2)
    return {
        "ok": True, "error": None, "metrics": metrics,
        "results": [r.__dict__ for r in results],
    }


# Full-line patterns worth trying deterministically before asking an LLM.
FULL_LINE_HINTS = ("LOG", "LINE", "MESSAGE", "EVENT", "ACCESS", "SYSLOG")


def library_matches(lines: list[str], library: PatternLibrary, min_coverage: float = 0.6,
                    min_fields: int = 4, limit: int = 5) -> list[dict]:
    """Try built-in whole-line patterns (COMBINEDAPACHELOG, SYSLOGLINE, ...) against the samples."""
    found = []
    for name in library.patterns:
        if not any(h in name for h in FULL_LINE_HINTS):
            continue
        ev = evaluate(f"%{{{name}}}", lines, library)
        m = ev["metrics"]
        # min_fields stops generic patterns (e.g. SYSLOGLINE) "matching" unrelated formats with one big free-text field
        if ev["ok"] and m["match_rate"] == 1.0 and m["coverage"] >= min_coverage and m["fields"] >= min_fields:
            found.append({"name": f"Built-in %{{{name}}}", "pattern": f"%{{{name}}}", "pattern_definitions": {},
                          "style": "built-in", "explanation": f"The standard {name} pattern from logstash-patterns-core matches every sample.",
                          **ev})
    found.sort(key=lambda c: (-c["metrics"]["coverage"], -c["metrics"]["fields"]))
    return found[:limit]
