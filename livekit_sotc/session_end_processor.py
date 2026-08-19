"""
session_end_processor.py  (SOTC edition)

Standalone session-end handler.
Extracts CRM data from the conversation using Google Gen AI (Vertex AI) directly,
then creates or updates an opportunity via the CRM API.

Usage:
    from session_end_processor import process_session_end
    await process_session_end(chat_history_obj, customer_id)
"""

import os
import re
import json
import asyncio
import traceback

from google import genai
from google.genai import types

from app_logger import applog
from opportunity_create import create_or_update_opportunity


# ─────────────────────────────────────────────
#  pkgSubtypeId → product name mapping
# ─────────────────────────────────────────────
_PKG_SUBTYPE_MAP = {
    1: "DOM Holiday GIT",
    2: "DOM Holiday FIT",
    3: "INT Holiday GIT",
    4: "INT Holiday FIT",
}

_EXTRACTION_PROMPT_TEMPLATE = """\
You are a travel CRM data extractor.
From the conversation below, extract these fields if clearly mentioned or strongly implied.
Return **only** valid JSON. Use null for missing/uncertain values.

Fields:
- first_name
- last_name
- email
- destination
- package_name
- pkgSubtypeId (integer 1-4, determine using the rules below):
    1 = Domestic destination + Group Tour (GIT)
    2 = Domestic destination + Customized/Independent Tour (FIT)
    3 = International destination + Group Tour (GIT)
    4 = International destination + Customized/Independent Tour (FIT)
  How to determine:
    - If a tool call output contains "pkgSubtypeId: <number>", use that value directly.
    - Otherwise infer from context:
      • Domestic = destinations within India (Goa, Kerala, Rajasthan, Kashmir, etc.)
      • International = destinations outside India (Thailand, Dubai, Europe, etc.)
      • Group Tour / GIT = fixed itinerary, group travel
      • Customized Tour / FIT = flexible, independent travel
    - If you know the destination but CANNOT determine GIT vs FIT, default to GIT:
      • Domestic destination + unknown tour type → use 1 (DOM Holiday GIT)
      • International destination + unknown tour type → use 3 (INT Holiday GIT)
    - Use null only if the destination itself is completely unclear.
- summary (1-2 sentences describing the enquiry)
- package_id (look for packageId in tool call outputs)
Conversation:
{conversation_text}
"""


def _build_conversation_text(doc: dict) -> str:
    """Build a plain-text conversation string including tool call context."""
    lines = []
    for msg in doc.get("conversation", [])[-40:]:
        role = msg.get("role", "unknown")
        if role in ("function_call", "function_call_output"):
            # Include tool calls as context labelled clearly
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            lines.append(f"[TOOL {role.upper()}] {name}: {content}")
        else:
            content = msg.get("content", "").strip()
            if content:
                lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


async def _extract_with_llm(conversation_text: str) -> dict:
    """
    Call Gemini 3.5 Flash via google-genai (Vertex AI) to extract CRM fields.
    Returns extracted dict; falls back to all-None defaults on any error.
    """
    defaults = {
        "first_name": None,
        "last_name": None,
        "email": None,
        "destination": None,
        "package_name": None,
        "pkgSubtypeId": None,
        "summary": None,
    }

    if not conversation_text.strip():
        applog.info("[SESSION END] Empty conversation — skipping LLM extraction")
        return defaults

    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(conversation_text=conversation_text)

    try:
        client = genai.Client(
            vertexai=False,
            api_key="DummyAPIKey",
            http_options=types.HttpOptions(base_url="http://10.160.0.6:8000")
        )

        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=500,
                # Disable thinking mode — it causes response.text to be None
                # on Vertex AI because the output lands in a ThinkingPart,
                # not a TextPart.
            ),
        )

        # response.text can still be None (safety filters, empty parts);
        # iterate all parts to find the first non-empty text part.
        raw = response.text
        if raw is None:
            try:
                for part in response.candidates[0].content.parts or []:
                    if getattr(part, "text", None):
                        raw = part.text
                        break
            except (AttributeError, IndexError, TypeError):
                raw = None

        if not raw:
            applog.warning("[SESSION END] LLM returned empty/None text — using defaults")
            return defaults

        raw = raw.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        extracted = json.loads(raw)
        applog.info("[SESSION END] LLM extracted:\n" + json.dumps(extracted, indent=2))
        return extracted

    except Exception as e:
        applog.warning(f"[SESSION END] LLM extraction failed, using defaults: {e}")
        return defaults


def _map_product(pkgSubtypeId) -> str:
    if pkgSubtypeId is None:
        return "ChatBot-Product Unknown"
    try:
        return _PKG_SUBTYPE_MAP.get(int(pkgSubtypeId), "ChatBot-Product Unknown")
    except (ValueError, TypeError):
        return "ChatBot-Product Unknown"


async def process_session_end(chat_history_obj, customer_id: str, opp_tool_calls="all") -> None:
    """
    Standalone session-end processor.

    Args:
        chat_history_obj: A ChatHistory instance. Its .doc is read and updated
                          in-place; save_json() and send_to_api() are called on
                          success so the caller does not need to do anything extra.
        customer_id:      The customer's mobile number extracted from the room name.
        opp_tool_calls:   "all" to always call CRM, or a list of tool names —
                          CRM is only called if at least one matching function_call
                          is found in the conversation.
    """
    if chat_history_obj is None or not getattr(chat_history_obj, "doc", None):
        applog.warning("[SESSION END] No chat_history_obj available — aborting")
        return

    doc = chat_history_obj.doc
    existing_opp_id = doc.get("opportunity_id")
    applog.info(f"[SESSION END] Starting | existing_opportunity_id={existing_opp_id}")

    # ── 1. Build conversation text ──────────────────────────────────────────
    conversation_text = _build_conversation_text(doc)

    # ── 2. Extract CRM fields via LLM ──────────────────────────────────────
    extracted = await _extract_with_llm(conversation_text)

    # ── 3. Determine mobile ─────────────────────────────────────────────────
    mobile = customer_id
    if not re.match(r"^\d{10}$", mobile):
        applog.warning(f"[SESSION END] customer_id '{mobile}' is not 10 digits — using as-is")

    # ── 4. Map product name ─────────────────────────────────────────────────
    product_name = _map_product(extracted.get("pkgSubtypeId"))

    # ── 5. Always capture summary and payload ──────────────────────────────
    doc["chat_summary"] = extracted.get("summary") or "Travel enquiry from voice session"
    
    # Pre-build the default creation payload so it is captured regardless of CRM gate
    doc["opportunity_payload"] = {
        "key": "VHJhdkJyaWRnZQ==",
        "product": product_name,
        "lead_source": "TravBridge",
        "division": "SOTC",
        "campaign_id": "1889",
        "utmcmd": "cpc",
        "first_name": extracted.get("first_name") or "",
        "last_name": extracted.get("last_name") or "",
        "mobile": mobile,
        "package_id": extracted.get("package_id") or "",
        "package_name": extracted.get("package_name") or "",
        "summary": doc["chat_summary"],
        "email": extracted.get("email") or "",
        "destination": extracted.get("destination") or "Others",
    }

    # ── 6. Check opp_tool_calls gate ───────────────────────────────────────
    should_call_crm = True
    if isinstance(opp_tool_calls, list) and opp_tool_calls:
        conv_tools = {m.get("name") for m in doc.get("conversation", []) if m.get("role") == "function_call" and m.get("name")}
        matched = conv_tools & set(opp_tool_calls)
        if not matched:
            should_call_crm = False
            applog.info(f"[SESSION END] Skipping CRM — no matching tools. Required: {opp_tool_calls}, found: {conv_tools}")
        else:
            applog.info(f"[SESSION END] CRM gate passed — matched tools: {matched}")

    # ── 7. Create or update opportunity ────────────────────────────────────
    if not should_call_crm:
        applog.info("[SESSION END] Opp gate: skipping CRM call, will still save JSON/DynamoDB")
        try:
            chat_history_obj.save_json()
        except Exception:
            pass
    else:
        try:
            applog.info(f"[SESSION END] Calling create_or_update_opportunity for mobile={mobile}")
            result = await create_or_update_opportunity(
                first_name=extracted.get("first_name") or "",
                last_name=extracted.get("last_name") or "",
                mobile=mobile,
                package_id=extracted.get("package_id") or "",
                package_name=extracted.get("package_name") or "",
                email=extracted.get("email") or "",
                destination=extracted.get("destination") or "Others",
                summary=extracted.get("summary") or "Travel enquiry from voice session",
                product=product_name,
            )

            action = "created"
            if isinstance(result, dict) and "action" in result:
                action = result["action"]

            # Update payload if it's an update action
            if action in ("updated", "update_failed"):
                doc["opportunity_payload"] = {
                    "opp_id": result.get("opportunity_id", "") if isinstance(result, dict) else "",
                    "mobile": mobile,
                    "product": product_name,
                    "package_id": extracted.get("package_id") or "",
                    "package_name": extracted.get("package_name") or "",
                    "device": "Mobile",
                    "summary": doc["chat_summary"],
                    "email": extracted.get("email") or "",
                    "destination": extracted.get("destination") or "Others",
                    "lead_source": "TravBridge",
                    "division": "SOTC",
                }

            doc["opportunity_response"] = result
            applog.info(f"[SESSION END] ✅ Saved opportunity_payload (action={action})")

            # Persist on success
            if isinstance(result, dict) and result.get("status") == "success":
                opp_id = result.get("opportunity_id")
                if opp_id:
                    doc["opportunity_id"] = opp_id
                    if extracted.get("first_name"):
                        doc["customer_first_name"] = extracted["first_name"]
                    if extracted.get("last_name"):
                        doc["customer_last_name"] = extracted["last_name"]
                    if extracted.get("email"):
                        doc["customer_email"] = extracted["email"]
                    if mobile:
                        doc["customer_phone"] = mobile

                    applog.info(f"[SESSION END] ✓ Opportunity {action}: {opp_id}")

                    try:
                        chat_history_obj.save_json()
                        applog.info("[SESSION END] ✓ JSON saved")
                    except Exception as e:
                        applog.error(f"[SESSION END] ✗ save_json failed: {e}")
            else:
                applog.error(f"[SESSION END] ✗ Opportunity creation/update failed: {result}")
                try:
                    chat_history_obj.save_json()
                except Exception:
                    pass

        except Exception as e:
            applog.error(f"[SESSION END] ✗ Critical error: {e}")
            applog.error(traceback.format_exc())
            try:
                chat_history_obj.save_json()
            except Exception:
                pass

    # ── 8. Always save to DynamoDB and Elastic Search ─────────
    try:
        chat_history_obj.send_to_dynamo()
        applog.info("[SESSION END] ✓ Sent to DynamoDB")
    except Exception as e:
        applog.error(f"[SESSION END] ✗ send_to_dynamo failed: {e}")

    try:
        chat_history_obj.send_to_api()
        applog.info("[SESSION END] ✓ Sent to Elastic Search API")
    except Exception as e:
        applog.error(f"[SESSION END] ✗ send_to_api failed: {e}")
