"""AI processing utilities (minimal).

This module provides a tiny helper that downloads audio from a URL and
sends it to OpenAI's Whisper transcription endpoint and to the Chat
Completions API for simple reasoning.

REQUIREMENTS:
- Set the environment variable `OPENAI_API_KEY` with a valid key.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


async def transcribe_recording(recording_url: str) -> str:
    """Download a recording and transcribe it using OpenAI Whisper.

    Args:
        recording_url: Public URL to the recorded audio (as provided by Twilio).

    Returns:
        The transcription text (empty string on failure).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set; cannot transcribe audio.")
        return ""

    # Attempt to download the recording; try original URL, then try adding .wav
    audio_bytes: Optional[bytes] = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(recording_url)
            if resp.status_code == 200:
                audio_bytes = resp.content
            else:
                # Try appending .wav (Twilio sometimes provides a URL without extension)
                alt_url = recording_url + ".wav"
                resp2 = await client.get(alt_url)
                if resp2.status_code == 200:
                    audio_bytes = resp2.content
        except Exception as exc:  # keep broad but log
            logger.exception("Failed to download recording: %s", exc)
            return ""

    if not audio_bytes:
        logger.error("Could not retrieve audio from %s", recording_url)
        return ""

    # Send to OpenAI Whisper endpoint as multipart/form-data
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("recording.wav", audio_bytes, "audio/wav")}
    data = {"model": "whisper-1"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                data=data,
                files=files,
            )
        except Exception as exc:
            logger.exception("Failed to call OpenAI transcription endpoint: %s", exc)
            return ""

    if resp.status_code != 200:
        logger.error("OpenAI transcription failed: %s - %s", resp.status_code, resp.text)
        return ""

    try:
        body = resp.json()
        text = body.get("text", "")
        logger.info("Transcription completed: %s", text)
        return text
    except Exception as exc:
        logger.exception("Failed to parse transcription response: %s", exc)
        return ""


async def generate_support_reply(transcript: str) -> str:
    """Send a short, support-oriented prompt to OpenAI and return the reply.

    The function calls the Chat Completions API with a small system prompt
    that instructs the model to behave like a concise customer support
    assistant. The returned text is used for logging and later actions.

    Args:
        transcript: The user's transcribed speech.

    Returns:
        The assistant's textual reply, or an empty string on error.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set; cannot call OpenAI chat API.")
        return ""

    # Construct a minimal, support-focused conversation
    messages = [
        {"role": "system", "content": "You are a concise customer support assistant. Answer clearly and briefly."},
        {"role": "user", "content": transcript},
    ]

    payload = {
        "model": "gpt-4.1",
        "messages": messages,
        "max_tokens": 200,
        "temperature": 0.2,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
        except Exception as exc:
            logger.exception("Failed to call OpenAI chat completions endpoint: %s", exc)
            return ""

    if resp.status_code != 200:
        logger.error("OpenAI chat API failed: %s - %s", resp.status_code, resp.text)
        return ""

    try:
        body = resp.json()
        choice = body.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        logger.info("AI reply generated: %s", content)
        return content.strip()
    except Exception as exc:
        logger.exception("Failed to parse OpenAI chat response: %s", exc)
        return ""


# Order lookup helpers
from ..integrations import shopify as shopify_integration
import re


async def generate_order_reply(transcript: str) -> tuple[str, dict | None]:
    """Detect order-related intent and attempt to return a short reply.

    Returns a tuple (reply_text, order_info) where `order_info` is None
    when no order lookup was performed, or contains dict details when one
    occurred (possibly empty if not found).
    """
    text = transcript.lower()
    order_phrases = ["order status", "where is my order", "where's my order", "track my order", "order status for"]
    if not any(p in text for p in order_phrases):
        return "", None

    # Attempt to extract an order id (#12345 or long digit sequence)
    order_id = None
    m = re.search(r"#\s?(\d+)", transcript)
    if m:
        order_id = m.group(1)
    else:
        m2 = re.search(r"order(?: number| no\.?| #)?\s*[:#]?\s*([A-Za-z0-9\-]+)", transcript, flags=re.IGNORECASE)
        if m2:
            order_id = m2.group(1)
        else:
            m3 = re.search(r"\b(\d{5,})\b", transcript)
            if m3:
                order_id = m3.group(1)

    if not order_id:
        # Ask for order number to continue the conversation
        return "I can check that for you — could you please provide your order number?", {}

    # Try to look up the order via Shopify
    try:
        order = await shopify_integration.lookup_order(order_id)
    except Exception as exc:
        logger.exception("Order lookup failed: %s", exc)
        order = None

    if not order:
        return "I couldn't find that order in our system — could you double-check the order number?", {"searched_order": order_id, "found": False}

    # Build a short, friendly order status reply
    parts = [f"I found order {order.get('order_number') or order.get('id')}."]
    if order.get("financial_status"):
        parts.append(f"Payment status: {order.get('financial_status')}")
    if order.get("fulfillment_status"):
        parts.append(f"Fulfillment: {order.get('fulfillment_status')}")

    # Try to include shipment/tracking info if present
    tracking = []
    for f in order.get("fulfillments", []):
        if f.get("tracking_numbers"):
            tracking.extend(f.get("tracking_numbers"))

    if tracking:
        parts.append(f"Tracking: {', '.join(tracking)}")

    reply = " ".join(parts)
    return reply, {"searched_order": order_id, "found": True, "order": order}


async def synthesize_speech(text: str) -> bytes:
    """Synthesize speech for given text using OpenAI Text-to-Speech.

    Returns raw audio bytes (MP3) or empty bytes on failure.

    Note: This implementation keeps things minimal — it calls OpenAI's
    TTS endpoint and returns the audio bytes directly so a /playback
    endpoint can stream them to Twilio via <Play>.
    """
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
        # Model name may change over time; using a compact TTS-capable model
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": text,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers=headers,
                json=payload,
            )
        except Exception as exc:
            logger.exception("Failed to call OpenAI TTS endpoint: %s", exc)
            return b""

    if resp.status_code != 200:
        logger.error("OpenAI TTS failed: %s - %s", resp.status_code, resp.text)
        return b""

    # Return raw audio bytes (MP3)
    try:
        return resp.content
    except Exception as exc:
        logger.exception("Failed to read TTS response content: %s", exc)
        return b""
