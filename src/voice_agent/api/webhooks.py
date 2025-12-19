"""Webhook endpoints (Twilio / VAPI) - placeholders."""

from fastapi import APIRouter
from ..schemas import VoiceWebhook

router = APIRouter()


@router.post("/webhook/voice")
async def voice_webhook(payload: VoiceWebhook):
    """Placeholder endpoint for voice webhook events."""
    return {"status": "received"}
