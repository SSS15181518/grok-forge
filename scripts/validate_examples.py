"""Check that every example in data/examples.jsonl matches all of its own log lines.
Run this after adding examples in bulk: a wrong example teaches the model wrong habits."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from grok_engine import PatternLibrary, evaluate  # noqa: E402

lib = PatternLibrary(ROOT / "data" / "patterns")
bad = 0
for n, raw in enumerate((ROOT / "data" / "examples.jsonl").read_text(encoding="utf-8").splitlines(), 1):
    if not raw.strip():
        continue
    ex = json.loads(raw)
    logs = ex.get("logs") or [ex.get("log", "")]
    ev = evaluate(ex["pattern"], logs, lib, ex.get("pattern_definitions"))
    if ev["metrics"]["match_rate"] < 1.0:
        bad += 1
        failing = [r["line"] for r in ev["results"] if not r["matched"]]
        print(f"line {n}: {ev.get('error') or 'does not match: ' + repr(failing[:2])}")
print(f"{bad} broken example(s)" if bad else "all examples match")
sys.exit(1 if bad else 0)
