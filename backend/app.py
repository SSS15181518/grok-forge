"""Grok Forge backend.

Flow for POST /api/generate:
  1. Deterministic pass: try built-in whole-line patterns (no LLM needed).
  2. Retrieve the k most similar stored examples (few-shot).
  3. Ask the LLM for several candidates (strict / balanced / flexible).
  4. Verify every candidate with the grok engine against ALL sample lines.
  5. Send failing candidates back to the LLM with the exact failures (repair loop).
  6. Rank verified candidates and return them with extracted fields.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from grok_engine import PatternLibrary, evaluate, library_matches
from llm import LLMError, chat_json, load_providers
from retrieval import ExampleStore

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LIB = PatternLibrary(ROOT / "data" / "patterns")
STORE = ExampleStore(ROOT / "data" / "examples.jsonl")
CONCEPTS = (ROOT / "data" / "concepts.md").read_text(encoding="utf-8")
PROVIDERS = load_providers()
TOP_K = int(os.getenv("FEW_SHOT_K", "8"))
REPAIR_ROUNDS = int(os.getenv("REPAIR_ROUNDS", "2"))
MAX_LINES = 200
ES_URL = os.getenv("ES_URL")  # optional: validate with Elasticsearch (same engine as Logstash)

app = FastAPI(title="Grok Forge")


# ----------------------------------------------------------------- models
class GenerateIn(BaseModel):
    logs: str = Field(..., description="Sample log lines, one per line")
    hint: str = ""
    num_candidates: int = Field(4, ge=1, le=8)


class TestIn(BaseModel):
    logs: str
    pattern: str
    pattern_definitions: dict[str, str] = {}


class ExampleIn(BaseModel):
    logs: str
    pattern: str
    pattern_definitions: dict[str, str] = {}
    notes: str = ""


def split_lines(text: str) -> list[str]:
    lines = [l.rstrip("\r") for l in text.splitlines() if l.strip()]
    if not lines:
        raise HTTPException(400, "Paste at least one log line.")
    return lines[:MAX_LINES]


# ----------------------------------------------------------------- prompts
SYSTEM = f"""You are a senior Logstash engineer who writes grok patterns.
{CONCEPTS}

Respond with JSON only, in exactly this shape:
{{"candidates": [{{"name": "short label", "style": "strict|balanced|flexible",
  "pattern": "grok pattern", "pattern_definitions": {{"NAME": "regex"}},
  "explanation": "one or two sentences: what each part captures and when to prefer this candidate"}}]}}
Inside JSON strings, escape backslashes (\\\\[ in JSON becomes \\[ in the pattern)."""


def fewshot_block(examples: list[dict]) -> str:
    parts = []
    for ex in examples:
        logs = "\n".join(ex["logs"][:3])
        defs = f"\npattern_definitions: {json.dumps(ex['pattern_definitions'])}" if ex.get("pattern_definitions") else ""
        notes = f"\nnote: {ex['notes']}" if ex.get("notes") else ""
        parts.append(f"LOG:\n{logs}\nGROK:\n{ex['pattern']}{defs}{notes}")
    return "\n\n---\n\n".join(parts)


def generate_messages(lines: list[str], hint: str, n: int) -> list[dict]:
    examples = STORE.top_k(lines, TOP_K)
    sample = "\n".join(lines[:40])
    user = (
        f"Verified examples of similar logs and their correct grok patterns:\n\n{fewshot_block(examples)}\n\n"
        f"=====\nNow write {n} DIFFERENT candidate grok patterns for these {len(lines)} sample lines. "
        "Each must match every line. Vary them meaningfully: 'strict' extracts every useful field with precise "
        "patterns and types; 'balanced' captures the important fields; 'flexible' tolerates variations "
        "(optional parts, \\s+) at the cost of detail.\n"
        + (f"User hint: {hint}\n" if hint else "")
        + f"\nSAMPLE LINES:\n{sample}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def repair_messages(lines: list[str], failed: list[dict]) -> list[dict]:
    report = []
    for c in failed:
        bad = [r["line"] for r in c["results"] if not r["matched"]][:5]
        reason = c["error"] or ("did not match:\n" + "\n".join(bad))
        report.append(f"PATTERN:\n{c['pattern']}\nDEFINITIONS: {json.dumps(c.get('pattern_definitions') or {})}\nPROBLEM: {reason}")
    user = (
        "These grok candidates were tested with a real grok engine and FAILED. Fix each one so it matches ALL sample "
        "lines. Think about escaping, variable spacing, optional parts and wrong pattern choices (e.g. WORD on "
        "dotted or dashed values).\n\n" + "\n\n---\n\n".join(report)
        + "\n\nSAMPLE LINES:\n" + "\n".join(lines[:40])
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ----------------------------------------------------------------- verification
def verify(c: dict, lines: list[str]) -> dict:
    defs = c.get("pattern_definitions") or {}
    ev = evaluate(c.get("pattern", ""), lines, LIB, defs)
    return {**c, "pattern_definitions": defs, **ev}


def dedupe(cands: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cands:
        first = next((r for r in c["results"] if r["matched"]), None)
        key = c["pattern"] if not first else json.dumps(sorted(first["fields"].items()), default=str)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


async def es_check(pattern: str, defs: dict, lines: list[str]) -> dict | None:
    """Optional second opinion using Elasticsearch's grok (Joni, like Logstash)."""
    if not ES_URL:
        return None
    body = {"pipeline": {"processors": [{"grok": {"field": "message", "patterns": [pattern],
                                                   "pattern_definitions": defs, "ecs_compatibility": "v1"}}]},
            "docs": [{"_source": {"message": l}} for l in lines]}
    try:
        async with httpx.AsyncClient(timeout=20, verify=os.getenv("ES_VERIFY_TLS", "true") == "true") as cl:
            r = await cl.post(f"{ES_URL.rstrip('/')}/_ingest/pipeline/_simulate", json=body,
                              headers={"Authorization": f"ApiKey {os.getenv('ES_API_KEY', '')}"} if os.getenv("ES_API_KEY") else {})
        docs = r.json().get("docs", [])
        ok = sum(1 for d in docs if "error" not in d)
        return {"matched": ok, "total": len(lines)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def logstash_snippet(pattern: str, defs: dict) -> str:
    q = "'" if '"' in pattern and "'" not in pattern else '"'
    lines = ["filter {", "  grok {"]
    if defs:
        lines.append("    pattern_definitions => {")
        for k, v in defs.items():
            vq = "'" if '"' in v and "'" not in v else '"'
            lines.append(f'      "{k}" => {vq}{v}{vq}')
        lines.append("    }")
    lines.append(f'    match => {{ "message" => {q}{pattern}{q} }}')
    lines += ["  }", "}"]
    return "\n".join(lines)


# ----------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/health")
def health():
    return {"providers": [{"name": p["name"], "model": p["model"]} for p in PROVIDERS],
            "examples": len(STORE.examples), "patterns": len(LIB.patterns), "es_validation": bool(ES_URL)}


@app.post("/api/generate")
async def generate(inp: GenerateIn):
    lines = split_lines(inp.logs)
    candidates = library_matches(lines, LIB)
    for c in candidates:
        c["source"] = "pattern library"
    notes, provider = [], None

    try:
        data, provider = await chat_json(generate_messages(lines, inp.hint, inp.num_candidates), PROVIDERS)
        llm_cands = [verify(c, lines) for c in data.get("candidates", []) if c.get("pattern")]
        for rnd in range(REPAIR_ROUNDS):
            failed = [c for c in llm_cands if c["metrics"]["match_rate"] < 1.0]
            if not failed:
                break
            fixed, _ = await chat_json(repair_messages(lines, failed), PROVIDERS)
            repaired = [verify({**c, "repaired": rnd + 1}, lines) for c in fixed.get("candidates", []) if c.get("pattern")]
            llm_cands = [c for c in llm_cands if c["metrics"]["match_rate"] == 1.0] + repaired
        for c in llm_cands:
            c["source"] = f"LLM ({provider})"
        candidates += llm_cands
    except LLMError as e:
        notes.append(str(e))

    candidates = dedupe(candidates)
    candidates.sort(key=lambda c: c["metrics"]["score"], reverse=True)
    for c in candidates:
        c["logstash"] = logstash_snippet(c["pattern"], c.get("pattern_definitions") or {})
        c["es_check"] = await es_check(c["pattern"], c.get("pattern_definitions") or {}, lines) if c["metrics"]["match_rate"] == 1.0 else None
    return {"lines": len(lines), "provider": provider, "notes": notes, "candidates": candidates}


@app.post("/api/test")
async def test(inp: TestIn):
    lines = split_lines(inp.logs)
    c = verify({"pattern": inp.pattern, "pattern_definitions": inp.pattern_definitions}, lines)
    c["logstash"] = logstash_snippet(inp.pattern, inp.pattern_definitions)
    c["es_check"] = await es_check(inp.pattern, inp.pattern_definitions, lines)
    return c


@app.post("/api/examples")
def add_example(inp: ExampleIn):
    lines = split_lines(inp.logs)
    ev = evaluate(inp.pattern, lines, LIB, inp.pattern_definitions)
    if ev["metrics"]["match_rate"] < 1.0:
        raise HTTPException(400, "Only patterns that match every sample line can be saved as examples.")
    STORE.add({"logs": lines[:5], "pattern": inp.pattern,
               "pattern_definitions": inp.pattern_definitions, "notes": inp.notes})
    return {"saved": True, "examples": len(STORE.examples)}
