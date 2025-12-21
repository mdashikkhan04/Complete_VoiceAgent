"""Webhook endpoints for inbound events (Twilio / VAPI).

This module defines HTTP endpoints used by telephony providers to
send inbound call events to the service.
"""

from typing import Any, Dict
from fastapi import APIRouter, Request, Response

from ..handlers import voice as voice_handler
from ..handlers import ai as ai_handler

router = APIRouter()


@router.post("/inbound")
async def inbound_voice(request: Request) -> Response:
    """Receive an inbound voice call webhook and return TwiML.

    The endpoint accepts either form-encoded (Twilio) or JSON payloads.
    It delegates response construction to the handler and returns XML
    with media_type `application/xml` as required by Twilio/VAPI.
    """
    # Accept form-encoded payloads (typical for Twilio) and fallback to JSON
    content_type = request.headers.get("content-type", "")
    payload: Dict[str, Any]
    if "application/x-www-form-urlencoded" in content_type or "form-data" in content_type:
        form = await request.form()
        payload = dict(form)
    else:
        try:
            payload = await request.json()
        except Exception:
            # If parsing fails, use an empty payload (safe minimal handling)
            payload = {}

    # Delegate building TwiML to the handler
    twiml_xml = await voice_handler.handle_incoming_call(payload)

    # Return TwiML with the correct media type
    return Response(content=twiml_xml, media_type="application/xml")


@router.post("/transcribe")
async def transcribe(request: Request) -> Response:
    """Receive recording callback from Twilio and run transcription.

    Twilio will POST form-encoded values including `RecordingUrl`.
    We extract the URL and hand it off to the AI handler for Whisper
    transcription. The resulting text is logged to console.

    We return an empty TwiML response to conclude the call flow.
    """
    form = await request.form()
    recording_url = None

    # Twilio typically sends 'RecordingUrl' in the form payload
    if form:
        recording_url = form.get("RecordingUrl") or form.get("RecordingUrl0")

    # Fallback to JSON body if needed
    if not recording_url:
        try:
            data = await request.json()
            recording_url = data.get("RecordingUrl")
        except Exception:
            recording_url = None

    if not recording_url:
        # Nothing to transcribe; respond with empty TwiML
        return Response(content="<Response></Response>", media_type="application/xml")

    # Kick off transcription and log result
    transcript = await ai_handler.transcribe_recording(recording_url)

    # Log the transcript (simple console logging for now)
    print(f"Transcription result: {transcript}")

    # Send the transcript to the LLM for a brief support reply (logged only)
    ai_reply = await ai_handler.generate_support_reply(transcript)
    if ai_reply:
        print(f"AI response: {ai_reply}")

        # Build a playback URL that Twilio can fetch to play synthesized audio.
        # We URL-encode the text and point to the /voice/playback endpoint
        # implemented below in this module.
        from urllib.parse import quote_plus

        # request.base_url includes scheme://host[:port]/
        playback_url = f"{str(request.base_url).rstrip('/')}" + f"/voice/playback?text={quote_plus(ai_reply)}"

        # Return TwiML instructing Twilio to play the generated audio
        twiml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<Response>\n"
            f"  <Play>{playback_url}</Play>\n"
            "</Response>"
        )

        return Response(content=twiml, media_type="application/xml")

    # If no AI reply was generated, fall back to empty TwiML
    return Response(content="<Response></Response>", media_type="application/xml")


@router.get("/playback")
async def playback(request: Request, text: str = "") -> Response:
    """Serve synthesized speech audio for Twilio <Play>.

    Twilio will fetch this URL and expect raw audio (e.g., audio/mpeg).
    For simplicity we synthesize audio on-demand using the AI handler.
    """
    if not text:
        return Response(status_code=400, content="Missing 'text' query parameter")

    audio_bytes = await ai_handler.synthesize_speech(text)
    if not audio_bytes:
        # Return an empty 204 if synthesis failed
        return Response(status_code=204, content=b"")

    return Response(content=audio_bytes, media_type="audio/mpeg")
