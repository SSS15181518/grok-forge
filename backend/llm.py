"""Minimal client for any OpenAI-compatible chat API (Groq, Cerebras, OpenRouter,
NVIDIA NIM, Together, ...). Providers are tried in order; a 429/5xx/timeout on
one falls through to the next, so free-tier rate limits rarely stop you.
"""
from __future__ import annotations

import json
import os
import re

import httpx


class LLMError(Exception):
    pass


def load_providers() -> list[dict]:
    """Read LLM_1_*, LLM_2_*, ... from the environment (see .env.example)."""
    providers = []
    for i in range(1, 10):
        base = os.getenv(f"LLM_{i}_BASE_URL")
        if not base:
            continue
        providers.append({
            "name": os.getenv(f"LLM_{i}_NAME", f"provider-{i}"),
            "base_url": base.rstrip("/"),
            "api_key": os.getenv(f"LLM_{i}_API_KEY", ""),
            "model": os.getenv(f"LLM_{i}_MODEL"),
            "json_mode": os.getenv(f"LLM_{i}_JSON_MODE", "true").lower() == "true",
        })
    return providers


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


async def chat_json(messages: list[dict], providers: list[dict], temperature: float = 0.2,
                    max_tokens: int = 4000) -> tuple[dict, str]:
    """Return (parsed JSON, provider name). Raises LLMError if every provider fails."""
    if not providers:
        raise LLMError("No LLM provider configured. Copy .env.example to .env and add an API key.")
    errors = []
    async with httpx.AsyncClient(timeout=90) as client:
        for p in providers:
            body = {"model": p["model"], "messages": messages, "temperature": temperature,
                    "max_tokens": max_tokens}
            if p["json_mode"]:
                body["response_format"] = {"type": "json_object"}
            try:
                r = await client.post(f"{p['base_url']}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {p['api_key']}"})
                if r.status_code != 200:
                    errors.append(f"{p['name']}: HTTP {r.status_code} {r.text[:200]}")
                    continue
                content = r.json()["choices"][0]["message"]["content"] or ""
                return _extract_json(content), p["name"]
            except (httpx.HTTPError, KeyError, ValueError) as e:
                errors.append(f"{p['name']}: {type(e).__name__}: {e}")
    raise LLMError("All LLM providers failed: " + " | ".join(errors))
