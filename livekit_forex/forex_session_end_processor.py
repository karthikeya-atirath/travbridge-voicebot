"""
forex_session_end_processor.py

Forex-specific session-end handler.
Extracts forex lead data from the conversation using Gemini,
then saves to DynamoDB and ElasticSearch.
No travel CRM call — forex lead data is captured via forex_tools.
"""

import os
import re
import json
import asyncio
import traceback

from google import genai
from google.genai import types

from app_logger import applog


_EXTRACTION_PROMPT_TEMPLATE = """\
You are a Thomas Cook Forex CRM data extractor.
From the conversation below, extract these fields if clearly mentioned or strongly implied.
Return ONLY valid JSON. Use null for missing/uncertain values.

Fields:
- customer_name
- mobile_number
- email
- transaction_type (Buy/Sell/Reload/Remittance/Unknown)
- currency (e.g. USD, EUR, GBP)
- amount (numeric string)
- destination_country
- beneficiary_country
- travel_date
- purpose (tourism/education/employment/immigration/business/medical/maintenance/other)
- current_city
- lead_status (open/converted/callback_required/branch_required/not_interested/payment_pending)
- payment_mode
- payment_link_sent (true/false)
- rate (numeric string)
- total_payable_amount (numeric string)
- nearest_branch
- callback_time
- tcs_applicable (true/false)
- remarks
- summary (1-2 sentences describing the enquiry and outcome)

Conversation:
{conversation_text}
"""


def _build_conversation_text(doc: dict) -> str:
    lines = []
    for msg in doc.get("conversation", [])[-40:]:
        role = msg.get("role", "unknown")
        if role in ("function_call", "function_call_output"):
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            lines.append(f"[TOOL {role.upper()}] {name}: {content}")
        else:
            content = msg.get("content", "").strip()
            if content:
                lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


async def _extract_with_llm(conversation_text: str) -> dict:
    defaults = {
        "customer_name": None, "mobile_number": None, "email": None,
        "transaction_type": None, "currency": None, "amount": None,
        "destination_country": None, "beneficiary_country": None,
        "travel_date": None, "purpose": None, "current_city": None,
        "lead_status": None, "payment_mode": None, "payment_link_sent": None,
        "rate": None, "total_payable_amount": None, "nearest_branch": None,
        "callback_time": None, "tcs_applicable": None, "remarks": None, "summary": None,
    }

    if not conversation_text.strip():
        applog.info("[FOREX SESSION END] Empty conversation — skipping LLM extraction")
        return defaults

    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(conversation_text=conversation_text)

    try:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "asvamultiplayer")
        client = genai.Client(vertexai=True, project=project_id, location="asia-south1")

        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=800,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

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
            applog.warning("[FOREX SESSION END] LLM returned empty — using defaults")
            return defaults

        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        extracted = json.loads(raw)
        applog.info("[FOREX SESSION END] LLM extracted:\n" + json.dumps(extracted, indent=2))
        return extracted

    except Exception as e:
        applog.warning(f"[FOREX SESSION END] LLM extraction failed, using defaults: {e}")
        return defaults


async def process_session_end(chat_history_obj, customer_id: str, opp_tool_calls="all") -> None:
    """
    Forex session-end processor.
    Extracts forex fields from conversation, saves summary, pushes to DynamoDB + ElasticSearch.
    Does NOT call the travel CRM — forex lead data captured via forex_tools.
    """
    if chat_history_obj is None or not getattr(chat_history_obj, "doc", None):
        applog.warning("[FOREX SESSION END] No chat_history_obj — aborting")
        return

    doc = chat_history_obj.doc
    applog.info("[FOREX SESSION END] Starting session-end processing")

    # 1. Build conversation text
    conversation_text = _build_conversation_text(doc)

    # 2. Extract forex fields via LLM
    extracted = await _extract_with_llm(conversation_text)

    # 3. Merge LLM-extracted data with tool-captured lead data
    tool_lead = doc.get("forex_lead", {})

    # Tool data takes priority over LLM extraction (tools ran during call)
    merged = {
        "customer_name":        tool_lead.get("customer_name") or extracted.get("customer_name") or "",
        "mobile_number":        customer_id,
        "email":                doc.get("customer_email") or extracted.get("email") or "",
        "transaction_type":     tool_lead.get("transaction_type") or extracted.get("transaction_type") or "",
        "currency":             tool_lead.get("currency") or extracted.get("currency") or "",
        "amount":               tool_lead.get("amount") or extracted.get("amount") or "",
        "destination_country":  tool_lead.get("destination_country") or extracted.get("destination_country") or "",
        "beneficiary_country":  tool_lead.get("beneficiary_country") or extracted.get("beneficiary_country") or "",
        "travel_date":          tool_lead.get("travel_date") or extracted.get("travel_date") or "",
        "purpose":              tool_lead.get("purpose") or extracted.get("purpose") or "",
        "current_city":         tool_lead.get("current_city") or extracted.get("current_city") or "",
        "lead_status":          tool_lead.get("lead_status") or extracted.get("lead_status") or "open",
        "payment_mode":         tool_lead.get("payment_mode") or extracted.get("payment_mode") or "",
        "payment_link_sent":    tool_lead.get("payment_link_sent") or extracted.get("payment_link_sent") or False,
        "rate":                 tool_lead.get("rate") or extracted.get("rate") or "",
        "total_payable_amount": tool_lead.get("total_payable_amount") or extracted.get("total_payable_amount") or "",
        "nearest_branch":       tool_lead.get("nearest_branch") or extracted.get("nearest_branch") or "",
        "callback_time":        tool_lead.get("callback_time") or extracted.get("callback_time") or "",
        "tcs_applicable":       tool_lead.get("tcs_applicable") or extracted.get("tcs_applicable") or False,
        "remarks":              tool_lead.get("remarks") or extracted.get("remarks") or "",
    }

    summary = extracted.get("summary") or (
        f"Forex {merged['transaction_type']} enquiry for {merged['currency']} {merged['amount']}. "
        f"Status: {merged['lead_status']}."
    )

    # 4. Update doc with merged data
    doc["chat_summary"] = summary
    doc["forex_lead_summary"] = merged
    doc["customer_phone"] = customer_id
    if doc.get("customer_first_name") == "" and merged.get("customer_name"):
        parts = merged["customer_name"].split()
        doc["customer_first_name"] = parts[0]
        doc["customer_last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
    if not doc.get("customer_email") and merged.get("email"):
        doc["customer_email"] = merged["email"]

    applog.info(f"[FOREX SESSION END] Summary: {summary}")

    # 5. Save JSON
    try:
        chat_history_obj.save_json()
        applog.info("[FOREX SESSION END] ✓ JSON saved")
    except Exception as e:
        applog.error(f"[FOREX SESSION END] ✗ save_json failed: {e}")

    # 6. Save to DynamoDB
    try:
        chat_history_obj.send_to_dynamo()
        applog.info("[FOREX SESSION END] ✓ Sent to DynamoDB")
    except Exception as e:
        applog.error(f"[FOREX SESSION END] ✗ send_to_dynamo failed: {e}")

    # 7. Send to ElasticSearch API
    try:
        chat_history_obj.send_to_api()
        applog.info("[FOREX SESSION END] ✓ Sent to ElasticSearch API")
    except Exception as e:
        applog.error(f"[FOREX SESSION END] ✗ send_to_api failed: {e}")
