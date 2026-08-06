import os
import json
import uuid
import copy
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from app_logger import applog
from chat_data_updater import ChatDataUpdater
from dynamo_saver import DynamoSaver


def now_iso():
    # IST is UTC+5:30
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat()


# 🔹 Global pointer to the current ChatHistory instance
current_chat_history = None


class ChatHistory:
    """
    Handles FULL chat history in senior’s EXACT JSON format.
    Stores messages, events, and pushes to Elastic API.
    """

    def __init__(self, user_id="guest_user", customer_id="guest_user", device="VoiceBot", chat_channel="VoiceBot_Ezy", agent_phone=None, customer_phone=None):
        global current_chat_history  # update global reference

        self.user_id = user_id
        self.customer_id = customer_id
        self.device = device
        self.chat_channel = chat_channel

        timestamp = now_iso()
        applog.info(f"ChatHistory started | user_id={user_id}") 

        # Check if user_id is already in the correct format (phone_phone_hex)
        # Format: {number}_{number}_{8-char-hex}
        if user_id and '_' in user_id:
            parts = user_id.split('_')
            # If user_id has 3 parts and last part looks like hex (8 chars), use it directly
            if len(parts) == 4 or len(parts[2]) == 8:
                conversation_id = user_id
                applog.info("Using room name as conversationId=%s", conversation_id)
            else:
                conversation_id = ChatDataUpdater.generate_conversation_id(
                    agent_number=agent_phone,
                    caller_number=customer_phone or user_id,
                    timestamp=timestamp
                )
        else:
            conversation_id = ChatDataUpdater.generate_conversation_id(
                agent_number=agent_phone,
                caller_number=customer_phone or user_id,
                timestamp=timestamp
            )

        # Build senior JSON format
        self.doc = {
            "conversationId": conversation_id,
            "userId": user_id,
            "customerId": customer_id,
            "chat_name": "New Chat",
            "packages_saved": [],
            "conversation": [],
            "chat_started": timestamp,
            "chat_modified": timestamp,
            "is_chat_open": True,
            "chat_summary": "",
            "booking_date": None,
            "opportunity_id": "",
            "chat_model_name": "",
            "chat_model_version": "v13",
            "dataset_version": "1.5",
            "chat_channel": chat_channel,
            "chat_status": "active",
            "agent_id": "",
            "session_status": "with_bot",
            "customer_first_name": "",
            "customer_last_name": "",
            "customer_email": "",
            "customer_phone": "",
            "device": device,
            "opportunity_status": "",
            "attended_agent_list": [],
            "is_utm_user": "regular",
            "is_with_agent": False,
            "events": [],
            "handoff_history": [],
            "opportunity_payload": {},
            "opportunity_response": {},
        }

        # Prepare local file directories (text log only, no JSON conversations)
        self.base_log_dir = os.path.join(os.getcwd(), "chat_logs")
        os.makedirs(self.base_log_dir, exist_ok=True)

        self.text_log_path = os.path.join(self.base_log_dir, "chat_history.log")

        applog.info(f"ChatHistory started | conversationId={conversation_id}")

        # set global reference so other modules can access current chat
        current_chat_history = self

    # ---------------------------------------------------------
    # MESSAGE HANDLING
    # ---------------------------------------------------------

    def add_message(self, role: str, text: str):
        if not text:
            return

        ts = now_iso()
        msg = {
            "message_id": str(uuid.uuid4()),
            "chat_id": self.doc["conversationId"],
            "role": role,
            "content": text,
            "type": "str",
            "chat_time": ts,
            "modified_time": ts,
            "rating": None,
            "sequence_id": None,
            "prompt_message": None,
        }

        self.doc["conversation"].append(msg)
        self.doc["chat_modified"] = ts

        # Text log
        with open(self.text_log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [{role.upper()}] {text}\n")

        applog.info(f"ChatHistory added {role} message")
        self.send_to_dynamo()
        self.send_to_api()

    def add_function_call(self, name: str, arguments: str):
        """Record when the agent calls a tool/function."""
        ts = now_iso()
        msg = {
            "message_id": str(uuid.uuid4()),
            "chat_id": self.doc["conversationId"],
            "role": "function_call",
            "content": f"{name}({arguments})",
            "name": name,
            "arguments": arguments,
            "type": "function_call",
            "chat_time": ts,
            "modified_time": ts,
            "rating": None,
            "sequence_id": None,
            "prompt_message": None,
        }
        self.doc["conversation"].append(msg)
        self.doc["chat_modified"] = ts

        with open(self.text_log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [FUNCTION_CALL] {name}({arguments})\n")

        applog.info(f"ChatHistory added function_call: {name}")
        self.send_to_dynamo()
        self.send_to_api()

    def add_function_call_output(self, name: str, output: str, tool_call_id: str = None):
        """Record the output returned by a tool/function."""
        ts = now_iso()
        msg = {
            "message_id": str(uuid.uuid4()),
            "chat_id": self.doc["conversationId"],
            "role": "function_call_output",
            "content": output,
            "name": name,
            "tool_call_id": tool_call_id,
            "type": "function_call_output",
            "chat_time": ts,
            "modified_time": ts,
            "rating": None,
            "sequence_id": None,
            "prompt_message": None,
        }
        self.doc["conversation"].append(msg)
        self.doc["chat_modified"] = ts

        with open(self.text_log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [FUNCTION_CALL_OUTPUT] {name}: {output}\n")

        applog.info(f"ChatHistory added function_call_output: {name}")
        self.send_to_dynamo()
        self.send_to_api()

    # ---------------------------------------------------------
    # EVENTS
    # ---------------------------------------------------------

    def add_event(self, event_type: str, reason: str = ""):
        evt = {
            "event_type": event_type,
            "timestamp": now_iso(),
            "reason": reason,
            "metadata": {},
        }
        self.doc["events"].append(evt)
        self.doc["chat_modified"] = now_iso()

        applog.info(f"ChatHistory added event={event_type}")
        self.send_to_dynamo()
        self.send_to_api()

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    def set_summary(self, summary_text: str):
        self.doc["chat_summary"] = summary_text
        self.doc["chat_modified"] = now_iso()

    # ---------------------------------------------------------
    # OPPORTUNITY DATA
    # ---------------------------------------------------------
    def set_opportunity_id(self, opportunity_id: str):
        """Set the opportunity ID for this chat"""
        self.doc["opportunity_id"] = opportunity_id
        self.doc["chat_modified"] = now_iso()
        applog.info(f"ChatHistory set opportunity_id={opportunity_id}")

    def set_opportunity_payload(self, payload: Dict[str, Any]):
        """Store the opportunity request payload"""
        self.doc["opportunity_payload"] = payload
        self.doc["chat_modified"] = now_iso()
        applog.info("ChatHistory set opportunity_payload")

    def set_opportunity_response(self, response: Dict[str, Any]):
        """Store the opportunity API response"""
        self.doc["opportunity_response"] = response
        self.doc["chat_modified"] = now_iso()
        applog.info("ChatHistory set opportunity_response")

    # ---------------------------------------------------------
    # GET STRUCTURED CONVERSATION
    # ---------------------------------------------------------
    def get_conversation(self):
        """
        Return the full conversation list that will go into the
        'conversation' field when saving/sending to Elastic.
        """
        return self.doc.get("conversation", [])

    # ---------------------------------------------------------
    # SAVE JSON FILE
    # ---------------------------------------------------------

    def save_json(self):
        # JSON file saving disabled — data is persisted via DynamoDB and API
        applog.info("ChatHistory save_json() skipped (local JSON saving disabled)")

    # ---------------------------------------------------------
    # SEND TO API
    # ---------------------------------------------------------

    def send_to_api(self):
        """Send updated chat data to the API endpoint in the background."""
        doc_copy = copy.deepcopy(self.doc)
        def _send():
            ChatDataUpdater.send_to_api(doc_copy)
        threading.Thread(target=_send, daemon=True).start()

    # ---------------------------------------------------------
    # SEND TO DYNAMODB
    # ---------------------------------------------------------

    def send_to_dynamo(self):
        """Save the conversation document to DynamoDB in the background."""
        doc_copy = copy.deepcopy(self.doc)
        def _save():
            DynamoSaver.save_conversation(doc_copy)
        threading.Thread(target=_save, daemon=True).start()

    # ---------------------------------------------------------
    # TEXT VERSION FOR SUMMARY GENERATION
    # ---------------------------------------------------------

    def get_plain_text(self):
        lines = []
        for msg in self.doc["conversation"]:
            lines.append(f"{msg['role']}: {msg['content']}")
        return "\n".join(lines)
