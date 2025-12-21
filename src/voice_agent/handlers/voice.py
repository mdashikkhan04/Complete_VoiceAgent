"""Voice call handling logic.

This module contains a minimal handler for inbound calls. It intentionally
keeps behavior simple: it replies with a message and then records the
caller, posting the recording to `/voice/transcribe` for later processing.
No AI/STT/TTS logic is performed here.
"""
from typing import Any, Dict


async def handle_incoming_call(payload: Dict[str, Any]) -> str:
    """Build a basic TwiML response for an inbound call that records speech.

    The response speaks a short prompt and then uses Twilio's <Record>
    verb to capture the caller's voice. Twilio will POST the recorded audio
    to the provided `action` URL (we set it to `/voice/transcribe`).

    Args:
        payload: The webhook payload sent by the telephony provider.

    Returns:
        A TwiML XML string as required by Twilio/VAPI.
    """
    # Static message for now. Use a clear, polite voice message.
    message = "Thanks for calling support. Please say how I can help you."

    # TwiML with <Record> that posts the recording to /voice/transcribe
    twiml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<Response>\n"
        f"  <Say voice=\"alice\">{message}</Say>\n"
        # Record for up to 30 seconds; Twilio will POST RecordingUrl to action
        "  <Record action=\"/voice/transcribe\" method=\"POST\" maxLength=\"30\" playBeep=\"false\" />\n"
        "</Response>"
    )

    return twiml
