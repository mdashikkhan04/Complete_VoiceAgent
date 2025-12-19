"""Pydantic schemas for webhook payloads and responses."""

from pydantic import BaseModel


class VoiceWebhook(BaseModel):
    """Minimal representation of a Twilio/VAPI voice webhook."""

    CallSid: str | None = None
    From: str | None = None
    To: str | None = None
