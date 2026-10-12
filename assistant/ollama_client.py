"""
Thin wrapper around a locally-running Ollama server (https://ollama.com).

Nothing here talks to the cloud — OLLAMA_BASE_URL defaults to
http://localhost:11434, so all of this only works if you have Ollama
installed and running on the same machine (or reachable on your network),
with the relevant models pulled, e.g.:

    ollama pull llama3.1        # text chat / analytics
    ollama pull qwen3-vl:8b     # vision / screenshot extraction

If Ollama isn't reachable, functions raise OllamaError with a friendly
message rather than a raw connection traceback, so views can catch it and
show the user something useful.
"""

from __future__ import annotations

import base64
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when Ollama can't be reached or returns something unusable."""


def _chat(messages: list[dict], model: str, schema: dict | None = None, timeout: int = 120) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    if schema is not None:
        # Structured output: Ollama constrains the reply to this JSON schema.
        payload["format"] = schema
        payload["options"] = {"temperature": 0}
    try:
        resp = requests.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Ollama request failed")
        raise OllamaError(
            f"Couldn't reach Ollama at {settings.OLLAMA_BASE_URL}. "
            f"Is `ollama serve` running, and is the model ('{model}') pulled? ({exc})"
        ) from exc

    try:
        data = resp.json()
        return data.get("message", {}).get("content", "")
    except (ValueError, KeyError) as exc:
        logger.exception("Unexpected Ollama response shape")
        raise OllamaError("Ollama returned an unexpected response.") from exc


def ask_portfolio_question(question: str, context_json: str) -> str:
    """Ask the chat model a question, grounded in the user's portfolio data."""
    system_prompt = (
        "You are a careful portfolio analytics assistant embedded in a personal "
        "finance dashboard. You are NOT a licensed financial advisor and must not "
        "give definitive buy/sell recommendations or predict future prices. You can: "
        "point out concentration risk, diversification gaps, notable gains/losses, "
        "and answer questions about the data provided. Be concise (a few short "
        "paragraphs or a short list, not an essay). If the data doesn't cover "
        "something asked, say so plainly instead of guessing."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Portfolio data (JSON):\n{context_json}\n\nQuestion: {question}"},
    ]
    return _chat(messages, settings.OLLAMA_CHAT_MODEL)


def extract_portfolio_from_screenshot(image_bytes: bytes) -> dict:
    """Use a vision model to pull cash balances + holdings out of a screenshot.

    Returns a dict: {"cash_accounts": [...], "holdings": [...]} — always both
    keys present (possibly empty lists), never raises on empty results, but
    raises OllamaError if Ollama is unreachable or the response isn't
    parseable JSON.
    """
    b64_image = base64.b64encode(image_bytes).decode("ascii")
    messages = [{"role": "user", "content": EXTRACTION_PROMPT, "images": [b64_image]}]
    # Generous timeout: the first call also loads the vision model into memory.
    raw = _chat(messages, settings.OLLAMA_VISION_MODEL, schema=EXTRACTION_SCHEMA, timeout=300)
    data = _parse_json_response(raw)
    data["holdings"] = [h for h in data["holdings"] if isinstance(h, dict) and (h.get("name") or h.get("ticker") or h.get("isin"))]
    data["cash_accounts"] = [c for c in data["cash_accounts"] if isinstance(c, dict)]
    return data


_NUMBER_OR_NULL = {"type": ["number", "null"]}
_STRING_OR_NULL = {"type": ["string", "null"]}

# Only what is literally printed on screen. Identifying the security and
# checking prices is done afterwards against market data (market_check.py),
# so the model never has to guess a ticker.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "holdings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ticker": _STRING_OR_NULL,
                    "isin": _STRING_OR_NULL,
                    "quantity": _NUMBER_OR_NULL,
                    "price": _NUMBER_OR_NULL,
                    "market_value": _NUMBER_OR_NULL,
                    "avg_cost": _NUMBER_OR_NULL,
                    "currency": _STRING_OR_NULL,
                },
                "required": ["name", "ticker", "isin", "quantity", "price", "market_value", "avg_cost", "currency"],
            },
        },
        "cash_accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "balance": _NUMBER_OR_NULL,
                    "currency": _STRING_OR_NULL,
                },
                "required": ["name", "balance", "currency"],
            },
        },
    },
    "required": ["holdings", "cash_accounts"],
}

EXTRACTION_PROMPT = """\
This is a screenshot of a bank, broker or portfolio app, or a list of investments.
Transcribe every investment position (one per row: stock, ETF, fund, bond, crypto)
and every cash balance you can see. Copy values exactly as printed; do not calculate,
convert or guess anything.

For each position:
- name: the security name exactly as shown.
- ticker: the trading symbol ONLY if it is printed (e.g. "AAPL", "VWCE"); otherwise null.
- isin: the 12-character ISIN ONLY if printed (e.g. "IE00BK5BQT80"); otherwise null.
- quantity: number of shares/units held (Quantità, Qty, Shares, Units, Pezzi).
- price: current price per unit, if shown (Prezzo, Last, Price, Quotazione).
- market_value: current total value of the position, if shown (Controvalore, Valore, Value).
- avg_cost: average purchase price per unit ONLY if explicitly labelled as such
  (Prezzo medio, PMC, Avg cost, Carico); otherwise null. Never put the current price here.
- currency: the 3-letter currency shown for that row (EUR, USD, GBP...), or null.

For each cash balance: name (account label as shown), balance, currency.

Numbers: output plain numbers. "1.234,56" means 1234.56; "1,234.56" means 1234.56.
Use null for anything not visible. Skip totals, headers and summary rows.
Return empty lists if there are no positions or no cash balances.
"""


def _parse_json_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Ollama vision response wasn't valid JSON: %s", raw)
        raise OllamaError(
            "The model's response wasn't valid JSON, so nothing could be "
            "extracted. Try a clearer/cropped screenshot, or a vision model that "
            "supports structured output well (e.g. qwen3-vl:8b or qwen2.5vl:7b; set "
            "OLLAMA_VISION_MODEL in .env)."
        ) from exc

    if not isinstance(data, dict):
        raise OllamaError("The model didn't return the expected JSON structure.")

    data.setdefault("cash_accounts", [])
    data.setdefault("holdings", [])
    return data
