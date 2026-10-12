"""Pick the most relevant few-shot examples for a set of log lines.

Sending hundreds of examples on every request wastes tokens, hits free-tier
rate limits, and actually lowers quality. Instead we compare the *shape* of the
user's lines with each stored example and send only the top-k closest ones.
No embedding API needed: shape similarity works very well for log formats.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_SHAPE_RULES = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ISO>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|CRIT(?:ICAL)?|FATAL|SEVERE)\b", re.I), "<LVL>"),
    (re.compile(r"\d+"), "0"),
    (re.compile(r"[A-Za-z_]+"), "a"),
]


def shape(line: str) -> str:
    for rx, rep in _SHAPE_RULES:
        line = rx.sub(rep, line)
    return line


def _ngrams(s: str, n: int = 3) -> set[str]:
    return {s[i:i + n] for i in range(max(len(s) - n + 1, 1))}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_]{3,}", s.lower()))


def _sim(a_shape: set, a_tok: set, b_shape: set, b_tok: set) -> float:
    js = len(a_shape & b_shape) / (len(a_shape | b_shape) or 1)
    jt = len(a_tok & b_tok) / (len(a_tok | b_tok) or 1)
    return 0.75 * js + 0.25 * jt


class ExampleStore:
    """Examples live in a JSONL file: one {"logs": [...], "pattern": ..., ...} per line."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.examples: list[dict] = []
        self._index: list[tuple[set, set]] = []
        self.reload()

    def reload(self):
        self.examples, self._index = [], []
        if not self.path.exists():
            return
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("//"):
                self._add_to_index(json.loads(raw))

    def _add_to_index(self, ex: dict):
        logs = ex.get("logs") or [ex.get("log", "")]
        ex["logs"] = logs
        joined = "\n".join(logs)
        self.examples.append(ex)
        self._index.append((_ngrams(shape(joined)), _tokens(joined)))

    def add(self, ex: dict):
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            self._add_to_index(ex)

    def top_k(self, lines: list[str], k: int = 8) -> list[dict]:
        joined = "\n".join(lines[:20])
        q_shape, q_tok = _ngrams(shape(joined)), _tokens(joined)
        scored = sorted(
            ((_sim(q_shape, q_tok, s, t), i) for i, (s, t) in enumerate(self._index)),
            reverse=True,
        )
        return [self.examples[i] for _, i in scored[:k]]
