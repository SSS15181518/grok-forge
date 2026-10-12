# Grok Forge

Paste sample log lines and get several grok patterns, each one **tested against every line** before it is shown, with the extracted fields highlighted in place.

## How it works

```
Browser (frontend/index.html)
   |  POST /api/generate  { logs, hint }
   v
FastAPI backend
   1. Built-in pass   - tries whole-line patterns (COMBINEDAPACHELOG, SYSLOGLINE, ...)   no LLM needed
   2. Retrieval       - picks the 8 stored examples most similar in *shape* to your lines
   3. LLM             - asks for strict / balanced / flexible candidates (OpenAI-compatible API)
   4. Verify          - a real grok engine runs every candidate on every line
   5. Repair loop     - failing candidates go back to the LLM with the exact failing lines (2 rounds)
   6. Rank            - match rate, fields, typed fields, anchoring, coverage, few GREEDYDATA
   v
Candidates + fields per line + ready-to-paste Logstash config
```

Why this design:

- **The LLM proposes, the grok engine decides.** LLMs write plausible grok that is often subtly wrong: unescaped brackets, WORD on dotted values, DATA at the end. Nothing reaches the user without passing a real match test.
- **Retrieval instead of stuffing.** With hundreds of examples, sending all of them on every call burns free-tier rate limits and dilutes the prompt. Only the closest few are sent. Log "shape" matching (digits become 0, words become a) finds same-format examples without an embedding API.
- **Provider fallback.** Several free providers are configured in order; a 429 on one moves to the next.
- **It learns.** "Save as example" stores verified patterns, so future suggestions for similar logs improve. Only patterns that match all samples can be saved.

## Setup

```bash
pip install -r requirements.txt
python scripts/fetch_patterns.py      # official ECS grok patterns (already included in data/patterns)
python scripts/seed_examples.py       # starter examples (already included in data/examples.jsonl)
cp .env.example .env                  # then add at least one API key
cd backend && uvicorn app:app --reload
# open http://localhost:8000
```

Docker: `docker build -t grok-forge . && docker run -p 8000:8000 --env-file .env grok-forge`

## Choosing the model

Use a hosted open-weight model through a free tier. The defaults in `.env.example`:

| Order | Provider | Model | Why |
|---|---|---|---|
| 1 | Groq | `openai/gpt-oss-120b` | Apache-2.0 open-weight, strong at regex and structured output, very fast; free tier is rate-limited, no card |
| 2 | Cerebras | `gpt-oss-120b` | Same model, separate free allowance - a natural fallback |
| 3 | OpenRouter | a `:free` model | Last-resort fallback with a daily request cap |

Other good open-weight choices if available on your provider: Qwen3 (235B or Coder variants), Kimi K2, DeepSeek V3. Model ids and free-tier limits change, so check each provider's models page. Free tiers are for development and personal use; free-tier traffic generally comes with no data-privacy guarantees, so don't paste logs containing secrets or personal data, or redact them first.

## Adding your own examples

`data/examples.jsonl` holds one JSON object per line:

```json
{"logs": ["sample line 1", "sample line 2"], "pattern": "^%{TIMESTAMP_ISO8601:timestamp} ...$", "pattern_definitions": {}, "notes": "optional hint for the model"}
```

Add examples in bulk by appending lines, then restart (or use the Save button in the UI). Variety beats volume: one or two examples per log *format* is ideal. Then run `python scripts/validate_examples.py` to make sure every example actually matches (a wrong example teaches the model wrong habits). The grok rules given to the model live in `data/concepts.md`; edit them to enforce your team's field-naming conventions.

## Accuracy note

`backend/grok_engine.py` expands grok patterns and matches them with Python's `regex` module, which supports the Oniguruma features the official patterns use (all 362 shipped patterns compile). Rare edge cases can still differ from Logstash's Joni engine. For exact parity set `ES_URL` (and `ES_API_KEY`) in `.env`: every passing candidate is then also run through Elasticsearch's `_ingest/pipeline/_simulate`, which uses the same engine as Logstash, and the result is shown as an extra badge.

## API

- `GET  /api/health` - configured models, example and pattern counts
- `POST /api/generate` - `{ "logs": "...", "hint": "", "num_candidates": 4 }`
- `POST /api/test` - `{ "logs": "...", "pattern": "...", "pattern_definitions": {} }`
- `POST /api/examples` - save a verified pattern as a few-shot example
