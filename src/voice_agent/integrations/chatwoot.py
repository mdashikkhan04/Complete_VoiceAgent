"""Minimal Chatwoot integration.

Expose simple helpers to create a conversation for a call and append
messages to that conversation. Uses environment variables (see README)
for configuration and keeps an in-memory mapping of CallSid -> conversation_id.

This implementation is intentionally minimal and tolerant of missing
configuration: if Chatwoot is not configured, functions are no-ops.
"""

from typing import Optional, Dict
import os
import asyncio

try:
    import httpx
except Exception:
    httpx = None  # If httpx is not available, we no-op


CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")

# In-memory mapping: CallSid -> conversation_id
_call_map: Dict[str, int] = {}


def _configured() -> bool:
    return all([CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN, CHATWOOT_ACCOUNT_ID, CHATWOOT_INBOX_ID, httpx])


async def _client():
    return httpx.AsyncClient(timeout=10)


async def create_conversation(call_sid: str, phone: Optional[str] = None) -> Optional[int]:
    """Create a Chatwoot contact and conversation for this call.

    Returns the conversation id if created, otherwise None.
    """
    if not _configured():
        print("Chatwoot not configured; skipping create_conversation")
        return None

    headers = {"api_access_token": CHATWOOT_API_TOKEN}

    contact_id = None
    async with httpx.AsyncClient(timeout=10) as client:
        # Attempt to create a contact (best-effort)
        contact_payload = {"name": phone or "Caller", "phone_number": phone, "identifier": phone}
        try:
            resp = await client.post(
                f"{CHATWOOT_BASE_URL.rstrip('/')}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts",
                json=contact_payload,
                headers=headers,
            )
        except Exception as exc:  # network issue
            print(f"Chatwoot contact create failed: {exc}")
            resp = None

        if resp and resp.status_code in (200, 201):
            contact_id = resp.json().get("id")
        else:
            # Try to search for existing contact by phone
            try:
                resp2 = await client.get(
                    f"{CHATWOOT_BASE_URL.rstrip('/')}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/search",
                    params={"query": phone},
                    headers=headers,
                )
                if resp2 and resp2.status_code == 200:
                    items = resp2.json().get("payload") or resp2.json()
                    # payload may be a list or dict; pick first id if present
                    if isinstance(items, list) and items:
                        contact_id = items[0].get("id")
            except Exception as exc:
                print(f"Chatwoot contact search failed: {exc}")

        if not contact_id:
            print("Chatwoot: could not obtain contact_id; conversation will not be created")
            return None

        # Create conversation
        conv_payload = {"inbox_id": CHATWOOT_INBOX_ID, "contact_id": contact_id}
        try:
            resp3 = await client.post(
                f"{CHATWOOT_BASE_URL.rstrip('/')}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations",
                json=conv_payload,
                headers=headers,
            )
            if resp3.status_code in (200, 201):
                conv_id = resp3.json().get("id")
                if conv_id and call_sid:
                    _call_map[call_sid] = conv_id
                return conv_id
            else:
                print(f"Chatwoot conversation create failed: {resp3.status_code} {resp3.text}")
        except Exception as exc:
            print(f"Chatwoot conversation create failed: {exc}")

    return None


async def add_message(conversation_id: int, content: str, message_type: str = "outgoing") -> bool:
    """Append a message to a Chatwoot conversation.

    message_type: 'incoming' when from user, 'outgoing' when from agent.
    Returns True if successful, False otherwise.
    """
    if not _configured():
        print("Chatwoot not configured; skipping add_message")
        return False

    headers = {"api_access_token": CHATWOOT_API_TOKEN}
    payload = {"content": content, "message_type": message_type}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{CHATWOOT_BASE_URL.rstrip('/')}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages",
                json=payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                return True
            else:
                print(f"Chatwoot add_message failed: {resp.status_code} {resp.text}")
        except Exception as exc:
            print(f"Chatwoot add_message exception: {exc}")

    return False


async def add_message_for_call(call_sid: str, content: str, message_type: str = "outgoing") -> bool:
    """Convenience: resolve conversation id from CallSid and add a message."""
    conv_id = _call_map.get(call_sid)
    if not conv_id:
        print(f"Chatwoot: no conversation found for CallSid {call_sid}")
        return False
    return await add_message(conv_id, content, message_type)


async def end_conversation(call_sid: str, reason: str = "ended") -> None:
    """Log an end-of-call message in the conversation (best-effort)."""
    conv_id = _call_map.get(call_sid)
    if not conv_id:
        return
    await add_message(conv_id, f"Call ended: {reason}", message_type="outgoing")
    # Optionally remove mapping to free memory
    _call_map.pop(call_sid, None)

