"""
Thin wrapper around a locally-running Ollama server (https://ollama.com).

Nothing here talks to the cloud — OLLAMA_BASE_URL defaults to
http://localhost:11434, so all of this only works if you have Ollama
installed and running on the same machine (or reachable on your network),
with the relevant models pulled, e.g.:

    ollama pull llama3.1        # text chat / analytics
    ollama pull llava           # vision / screenshot extraction

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


def _chat(messages: list[dict], model: str) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    try:
        resp = requests.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
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

    instructions = (
        "You are looking at a screenshot of a brokerage app, bank app, or "
        "portfolio tracker. Extract every cash balance and every investment "
        "holding (stock, ETF, bond, or crypto) visible in the image.\n\n"
        "Respond with ONLY valid JSON — no markdown code fences, no commentary "
        "before or after — in exactly this shape:\n"
        '{"cash_accounts": [{"name": "string", "currency": "USD", "balance": 0.0}], '
        '"holdings": [{"ticker": "string", "name": "string", '
        '"asset_type": "stock|etf|bond|crypto|other", "quantity": 0.0, '
        '"avg_cost": 0.0, "currency": "USD"}]}\n\n'
        "Rules:\n"
        "- ticker should be the trading symbol if visible (e.g. AAPL), otherwise "
        "your best guess from the name.\n"
        "- avg_cost is the average price paid per unit, if shown; if only current "
        "price is visible, use that as a best-effort estimate.\n"
        "- If you're unsure of an exact number, give your best-effort reading "
        "rather than omitting the row entirely.\n"
        "- If there are no cash balances visible, return an empty list for "
        "cash_accounts. If there are no holdings visible, return an empty list "
        "for holdings."
    )

    messages = [{"role": "user", "content": instructions, "images": [b64_image]}]
    raw = _chat(messages, settings.OLLAMA_VISION_MODEL)
    return _parse_json_response(raw)


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
            "extracted. Try a clearer/cropped screenshot, or a different "
            "vision model (e.g. llava, qwen2.5-vl, llama3.2-vision)."
        ) from exc

    if not isinstance(data, dict):
        raise OllamaError("The model didn't return the expected JSON structure.")

    data.setdefault("cash_accounts", [])
    data.setdefault("holdings", [])
    return data
