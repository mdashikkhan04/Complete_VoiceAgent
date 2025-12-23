"""Webhook endpoints for inbound events (Twilio / VAPI).

This module defines HTTP endpoints used by telephony providers to
send inbound call events to the service.
"""

from typing import Any, Dict
from fastapi import APIRouter, Request, Response
import os
import logging
import json

from ..handlers import voice as voice_handler
from ..handlers import ai as ai_handler
from ..integrations import chatwoot as chatwoot_integration

logger = logging.getLogger(__name__)

# Feature flag to optionally disable legacy Twilio-specific routes.
# Set DISABLE_TWILIO_ROUTES=1 in the environment to disable.
DISABLE_TWILIO_ROUTES = os.getenv("DISABLE_TWILIO_ROUTES") == "1"

# Primary router that is included under /voice
router = APIRouter()

# New VAPI router for vendor-agnostic events (mounted under /vapi in main)
vapi_router = APIRouter(prefix="/vapi")


@router.post("/inbound", deprecated=True)
async def inbound_voice(request: Request) -> Response:
    """Receive an inbound voice call webhook and return TwiML.

    Deprecated Twilio-style inbound webhook. When `DISABLE_TWILIO_ROUTES` is set
    the route will respond with a 410 and a minimal XML payload. Otherwise the
    legacy behavior is preserved for backward compatibility.
    """
    if DISABLE_TWILIO_ROUTES:
        logger.info("Inbound Twilio route called while disabled by DISABLE_TWILIO_ROUTES flag")
        return Response(content="<Response></Response>", media_type="application/xml", status_code=410)

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

    # Create a Chatwoot conversation for this call (best-effort)
    call_sid = payload.get("CallSid") or payload.get("CallSid0") or payload.get("callSid")
    caller = payload.get("From") or payload.get("FromNumber") or payload.get("caller")
    if call_sid:
        try:
            # Create conversation and log a call-started message
            conv_id = await chatwoot_integration.create_conversation(call_sid, caller)
            if conv_id:
                await chatwoot_integration.add_message_for_call(call_sid, f"Call started from {caller or 'unknown'}", message_type="incoming")
        except Exception as exc:
            print(f"Chatwoot create_conversation error: {exc}")

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

    # Determine transcript source: prefer `SpeechResult` from a <Gather>,
    # otherwise fall back to a recording URL transcription.
    transcript = None
    speech_result = form.get("SpeechResult")
    if speech_result:
        transcript = speech_result
    else:
        # Fallback to recording-based transcription if recording_url exists
        if recording_url:
            transcript = await ai_handler.transcribe_recording(recording_url)

    # If we still have no transcript, respond with empty TwiML and log an end event
    call_sid = form.get("CallSid") or form.get("CallSid0") or form.get("callSid")
    if not transcript:
        if call_sid:
            try:
                await chatwoot_integration.end_conversation(call_sid, reason="silence")
            except Exception as exc:
                print(f"Chatwoot end_conversation error (silence): {exc}")
        return Response(content="<Response></Response>", media_type="application/xml")

    # Log the transcript (simple console logging for now)
    print(f"Transcription result: {transcript}")

    # Log user transcript to Chatwoot (best-effort)
    if call_sid:
        try:
            await chatwoot_integration.add_message_for_call(call_sid, f"User: {transcript}", message_type="incoming")
        except Exception as exc:
            print(f"Chatwoot add_message_for_call error (user): {exc}")

    # Simple stop-intent detection (keep logic minimal as requested)
    import re
    transcript_lower = transcript.lower().strip()

    # Escalation detection: user asks to speak to a human
    escalation_phrases = [
        "talk to agent",
        "talk to a human",
        "talk to human",
        "human",
        "real person",
        "customer support",
        "representative",
        "agent",
    ]
    escalation_detected = any(p in transcript_lower for p in escalation_phrases)

    if escalation_detected:
        # Log escalation to Chatwoot (best-effort)
        if call_sid:
            try:
                await chatwoot_integration.add_message_for_call(call_sid, "User requested escalation to human agent", message_type="incoming")
                await chatwoot_integration.add_message_for_call(call_sid, "Agent: Escalation requested; transferring to human agent", message_type="outgoing")
            except Exception as exc:
                print(f"Chatwoot escalation log error: {exc}")

        # Respond with TwiML that speaks and dials a placeholder number.
        # Replace the placeholder with your real support phone number when ready.
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            '  <Say>Connecting you to a human agent. Please hold.</Say>\n'
            '  <Dial>\n'
            '    <Number>+15555551234</Number>\n'
            '  </Dial>\n'
            '</Response>'
        )
        return Response(content=twiml, media_type="application/xml")

    # Multi-word phrases are checked using `in`; short words use word-boundary regex
    stop_multi = ["that's all", "thats all", "nothing else", "that's it", "thats it", "goodbye"]
    stop_single = {"no", "bye", "nope"}

    stop_detected = any(phrase in transcript_lower for phrase in stop_multi) or any(re.search(rf"\b{w}\b", transcript_lower) for w in stop_single)

    if stop_detected:
        # Speak a short goodbye and hang up; log the end to Chatwoot
        if call_sid:
            try:
                await chatwoot_integration.add_message_for_call(call_sid, "Call ended: user ended", message_type="outgoing")
                await chatwoot_integration.end_conversation(call_sid, reason="user ended")
            except Exception as exc:
                print(f"Chatwoot end_conversation error (user ended): {exc}")

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            '  <Say>Goodbye. Have a great day.</Say>\n'
            '  <Hangup/>\n'
            '</Response>'
        )
        return Response(content=twiml, media_type="application/xml")

    # First, check for order-related intent and attempt a lookup
    order_reply, order_info = await ai_handler.generate_order_reply(transcript)
    ai_reply = ""
    if order_reply:
        ai_reply = order_reply
        print(f"Order reply: {ai_reply}")

        # Log order lookup result to Chatwoot
        if call_sid:
            try:
                if order_info is None:
                    # No lookup performed (shouldn't happen here), just note it
                    await chatwoot_integration.add_message_for_call(call_sid, "Order lookup: N/A", message_type="outgoing")
                elif order_info == {}:
                    await chatwoot_integration.add_message_for_call(call_sid, "Agent: Asked customer for order number", message_type="outgoing")
                elif not order_info.get("found"):
                    await chatwoot_integration.add_message_for_call(call_sid, f"Agent: Order lookup attempted for {order_info.get('searched_order')}, not found", message_type="outgoing")
                else:
                    order = order_info.get("order") or {}
                    await chatwoot_integration.add_message_for_call(
                        call_sid,
                        f"Agent: Order lookup result for {order_info.get('searched_order')}: status={order.get('financial_status')}, fulfillment={order.get('fulfillment_status')}",
                        message_type="outgoing",
                    )
            except Exception as exc:
                print(f"Chatwoot add_message_for_call error (order): {exc}")

    # If no order reply, fall back to general LLM reply
    if not ai_reply:
        ai_reply = await ai_handler.generate_support_reply(transcript)

    if ai_reply:
        print(f"AI response: {ai_reply}")

        # Log AI reply to Chatwoot (best-effort)
        if call_sid:
            try:
                await chatwoot_integration.add_message_for_call(call_sid, f"Agent: {ai_reply}", message_type="outgoing")
            except Exception as exc:
                print(f"Chatwoot add_message_for_call error (ai): {exc}")

        # Build a playback URL that Twilio can fetch to play synthesized audio.
        # We URL-encode the text and point to the /voice/playback endpoint
        # implemented below in this module.
        from urllib.parse import quote_plus

        # request.base_url includes scheme://host[:port]/
        playback_url = f"{str(request.base_url).rstrip('/')}" + f"/voice/playback?text={quote_plus(ai_reply)}"

        # Build TwiML: play the AI reply, then ask a short follow-up inside a <Gather>
        # that posts back to /voice/transcribe so we can continue the conversation.
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            f'  <Play>{playback_url}</Play>\n'
            '  <Gather input="speech" action="/voice/transcribe" method="POST" timeout="3">\n'
            '    <Say>Is there anything else I can help you with?</Say>\n'
            '  </Gather>\n'
            '</Response>'
        )

        return Response(content=twiml, media_type="application/xml")

    # If no AI reply was generated, fall back to empty TwiML
    return Response(content="<Response></Response>", media_type="application/xml")


@router.get("/playback", deprecated=True)
async def playback(request: Request, text: str = "") -> Response:
    """Serve synthesized speech audio for Twilio <Play>.

    Deprecated Twilio-style /playback endpoint. If Twilio routes are disabled,
    this will return 410; otherwise it continues to synthesize audio as before.
    """
    if DISABLE_TWILIO_ROUTES:
        logger.info("Playback Twilio route called while disabled by DISABLE_TWILIO_ROUTES flag")
        return Response(status_code=410, content=b"")

    if not text:
        return Response(status_code=400, content="Missing 'text' query parameter")

    audio_bytes = await ai_handler.synthesize_speech(text)
    if not audio_bytes:
        # Return an empty 204 if synthesis failed
        return Response(status_code=204, content=b"")

    return Response(content=audio_bytes, media_type="audio/mpeg")


# -----------------------------------------
# VAPI router: vendor-agnostic event ingestion
# -----------------------------------------
@vapi_router.post("/events")
async def vapi_events(request: Request) -> Response:
    """Receive VAPI events as JSON and route them to existing logic.

    Supported event types (minimal):
      - call_started: create conversation and return an initial assistant prompt
      - user_message: handle transcript, run AI/order/escalation logic, and return assistant replies
      - call_ended: log and end conversation

    The endpoint is defensive: missing fields won't crash the app and
    responses are returned as simple JSON payloads that VAPI can act on.
    """
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        logger.warning("VAPI event received but JSON parsing failed; raw body length=%d", len(raw))
        logger.debug("Raw VAPI body: %s", raw)
        return Response(content='{"status":"received"}', media_type="application/json")

    # Normalize event type
    event_type = (body.get("type") or body.get("event") or "").lower()
    logger.info("VAPI event received: %s", event_type)

    # --- call_started ---
    if event_type == "call_started":
        call_id = body.get("call_id") or body.get("callSid") or body.get("CallSid") or body.get("id")
        phone = body.get("from") or body.get("caller") or body.get("phone")
        try:
            if call_id:
                conv_id = await chatwoot_integration.create_conversation(call_id, phone)
                if conv_id:
                    await chatwoot_integration.add_message_for_call(call_id, f"Call started via VAPI from {phone or 'unknown'}", message_type="incoming")
            logger.info("VAPI call_started handled for call_id=%s", call_id)
        except Exception as exc:
            logger.exception("Error handling VAPI call_started: %s", exc)

        return Response(content=json.dumps({"type": "assistant_response", "text": "Thanks for calling support. How can I help you today?"}), media_type="application/json")

    # --- user_message ---
    if event_type == "user_message":
        call_id = body.get("call_id") or body.get("callSid") or body.get("CallSid") or body.get("id")
        transcript = body.get("text") or body.get("transcript") or body.get("speech") or ""
        transcript = transcript.strip()

        # Log incoming user message to Chatwoot
        if call_id and transcript:
            try:
                await chatwoot_integration.add_message_for_call(call_id, f"User: {transcript}", message_type="incoming")
            except Exception as exc:
                logger.exception("Chatwoot log error for user_message: %s", exc)

        # Detect simple stop intents
        import re
        t_lower = transcript.lower()
        stop_multi = ["that's all", "thats all", "nothing else", "that's it", "thats it", "goodbye"]
        stop_single = {"no", "bye", "nope"}
        stop_detected = bool(transcript) and (any(phrase in t_lower for phrase in stop_multi) or any(re.search(rf"\b{w}\b", t_lower) for w in stop_single))

        if stop_detected:
            # Log and end
            if call_id:
                try:
                    await chatwoot_integration.add_message_for_call(call_id, "Call ended: user ended", message_type="outgoing")
                    await chatwoot_integration.end_conversation(call_id, reason="user ended")
                except Exception as exc:
                    logger.exception("Chatwoot end_conversation error (user ended): %s", exc)

            return Response(content=json.dumps({"type": "end_call", "reason": "user_ended"}), media_type="application/json")

        # Detect escalation intent
        escalation_phrases = ["talk to agent", "talk to a human", "talk to human", "human", "real person", "customer support", "representative", "agent"]
        escalation_detected = bool(transcript) and any(p in t_lower for p in escalation_phrases)
        if escalation_detected:
            if call_id:
                try:
                    await chatwoot_integration.add_message_for_call(call_id, "User requested escalation to human agent", message_type="incoming")
                    await chatwoot_integration.add_message_for_call(call_id, "Agent: Escalation requested; transferring to human agent", message_type="outgoing")
                except Exception as exc:
                    logger.exception("Chatwoot escalation log error: %s", exc)

            return Response(content=json.dumps({"type": "handoff", "reason": "user_requested_human"}), media_type="application/json")

        # Detect order intent and attempt lookup via existing helper
        try:
            order_reply, order_info = await ai_handler.generate_order_reply(transcript)
        except Exception as exc:
            logger.exception("Order intent/lookup failed: %s", exc)
            order_reply, order_info = "", None

        ai_reply = ""
        if order_reply:
            ai_reply = order_reply
            # Log order lookup to Chatwoot
            if call_id:
                try:
                    if order_info == {}:
                        await chatwoot_integration.add_message_for_call(call_id, "Agent: Asked customer for order number", message_type="outgoing")
                    elif order_info and not order_info.get("found"):
                        await chatwoot_integration.add_message_for_call(call_id, f"Agent: Order lookup attempted for {order_info.get('searched_order')}, not found", message_type="outgoing")
                    elif order_info and order_info.get("found"):
                        order = order_info.get("order") or {}
                        await chatwoot_integration.add_message_for_call(call_id, f"Agent: Order lookup result for {order_info.get('searched_order')}: status={order.get('financial_status')}, fulfillment={order.get('fulfillment_status')}", message_type="outgoing")
                except Exception as exc:
                    logger.exception("Chatwoot add_message_for_call error (order): %s", exc)

        # Otherwise use the general LLM reply
        if not ai_reply:
            try:
                ai_reply = await ai_handler.generate_support_reply(transcript)
            except Exception as exc:
                logger.exception("Support reply generation failed: %s", exc)
                ai_reply = "Sorry, I had trouble processing that. Could you repeat?"

        # Log AI reply to Chatwoot
        if call_id:
            try:
                await chatwoot_integration.add_message_for_call(call_id, f"Agent: {ai_reply}", message_type="outgoing")
            except Exception as exc:
                logger.exception("Chatwoot add_message_for_call error (ai): %s", exc)

        # Return assistant response for VAPI to speak back
        return Response(content=json.dumps({"type": "assistant_response", "text": ai_reply}), media_type="application/json")

    # --- call_ended ---
    if event_type == "call_ended":
        call_id = body.get("call_id") or body.get("callSid") or body.get("CallSid") or body.get("id")
        if call_id:
            try:
                await chatwoot_integration.add_message_for_call(call_id, "Call ended via VAPI", message_type="outgoing")
                await chatwoot_integration.end_conversation(call_id, reason="vapi call ended")
            except Exception as exc:
                logger.exception("Chatwoot end_conversation error (vapi): %s", exc)

        return Response(content=json.dumps({"status": "ok"}), media_type="application/json")

    # Unknown / unhandled events
    logger.info("VAPI event type not handled: %s", event_type)
    return Response(content=json.dumps({"status": "received"}), media_type="application/json")
