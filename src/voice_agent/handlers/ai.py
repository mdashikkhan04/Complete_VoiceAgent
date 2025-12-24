"""AI processing utilities (minimal + knowledge-based support).

This module handles:
- Speech-to-text (Whisper)
- Product knowledge–based support replies
- Order intent handling (Shopify)
- Text-to-speech synthesis

REQUIREMENTS:
- OPENAI_API_KEY environment variable
"""

from __future__ import annotations

import os
import logging
import re
from typing import Optional

import httpx

# 🔹 Product knowledge loader
from voice_agent.knowledge.loader import load_product_knowledge

# 🔹 Shopify integration (already implemented)
from ..integrations import shopify as shopify_integration


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Speech-to-Text (Whisper)
# ---------------------------------------------------------------------------

async def transcribe_recording(recording_url: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set; cannot transcribe audio.")
        return ""

    audio_bytes: Optional[bytes] = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(recording_url)
            if resp.status_code == 200:
                audio_bytes = resp.content
            else:
                alt = recording_url + ".wav"
                resp2 = await client.get(alt)
                if resp2.status_code == 200:
                    audio_bytes = resp2.content
        except Exception as exc:
            logger.exception("Failed to download recording: %s", exc)
            return ""

    if not audio_bytes:
        logger.error("Could not retrieve audio from %s", recording_url)
        return ""

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("recording.wav", audio_bytes, "audio/wav")}
    data = {"model": "whisper-1"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files=files,
        )

    if resp.status_code != 200:
        logger.error("Whisper failed: %s - %s", resp.status_code, resp.text)
        return ""

    return resp.json().get("text", "")


# ---------------------------------------------------------------------------
# Knowledge-based Support Reply (NO RAG)
# ---------------------------------------------------------------------------

async def generate_support_reply(user_text: str) -> str:
    """Answer product / FAQ / pricing / refund questions
    using ONLY product_knowledge.json.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set; cannot call OpenAI.")
        return ""

    knowledge = load_product_knowledge()

    system_prompt = f"""
You are a professional voice support agent.

RULES:
- Answer ONLY using the PRODUCT KNOWLEDGE below.
- Do NOT guess or invent information.
- If the answer is not clearly available, say:
  "I’ll connect you to a human support agent."

PRODUCT KNOWLEDGE:
{knowledge}
"""

    messages = [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_text},
    ]

    payload = {
        "model": "gpt-4.1",
        "messages": messages,
        "max_tokens": 250,
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        logger.error("Chat API failed: %s - %s", resp.status_code, resp.text)
        return ""

    content = (
        resp.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    logger.info("Knowledge-based reply generated")
    return content


# ---------------------------------------------------------------------------
# Order intent detection (Shopify)
# ---------------------------------------------------------------------------

async def generate_order_reply(transcript: str) -> tuple[str, dict | None]:
    text = transcript.lower()
    order_phrases = [
        "order status",
        "where is my order",
        "track my order",
        "order number",
    ]

    if not any(p in text for p in order_phrases):
        return "", None

    order_id = None
    m = re.search(r"#\s?(\d+)", transcript)
    if m:
        order_id = m.group(1)
    else:
        m2 = re.search(r"\b(\d{5,})\b", transcript)
        if m2:
            order_id = m2.group(1)

    if not order_id:
        return "Please provide your order number so I can check it.", {}

    try:
        order = await shopify_integration.lookup_order(order_id)
    except Exception as exc:
        logger.exception("Order lookup failed: %s", exc)
        order = None

    if not order:
        return "I couldn't find that order. Please double-check the order number.", {}

    reply = (
        f"I found your order {order.get('order_number')}. "
        f"Payment status is {order.get('financial_status')}. "
        f"Fulfillment status is {order.get('fulfillment_status')}."
    )

    return reply, {"found": True, "order": order}


# ---------------------------------------------------------------------------
# Text-to-Speech (OpenAI)
# ---------------------------------------------------------------------------

async def synthesize_speech(text: str) -> bytes:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set; cannot synthesize speech.")
        return b""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": text,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        logger.error("TTS failed: %s - %s", resp.status_code, resp.text)
        return b""

    return resp.content
