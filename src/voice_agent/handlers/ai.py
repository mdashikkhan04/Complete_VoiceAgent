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
