import uuid
from datetime import datetime
from typing import Dict, Any
import requests
from configuration.config_env import elastic_search_url
from app_logger import applog


class ChatDataUpdater:
    """Updates chat data JSON with new parameters and generates unique conversation IDs."""

    # URL is now taken from environment (dev/prod)
   

    if not elastic_search_url:
        # Fail fast if env is not configured correctly
        raise RuntimeError(
            "ELASTIC_SEARCH_URL_SAVE_CONVERSATION is not set in environment variables"
        )

    @staticmethod
    def generate_conversation_id(agent_number: str = None, caller_number: str = None, timestamp: str = None) -> str:
        """
        Generate a unique conversation ID with agent number, caller number, and random suffix.

        Format: {agent_number}_{caller_number}_{random_hex}
        Example: 00912263103314_00919121846692_03210bf8
        """
        # Use default values if not provided
        if not agent_number:
            agent_number = "0000000000000"
        if not caller_number:
            caller_number = "0000000000000"

        # Generate random hex (8 characters)
        random_part = uuid.uuid4().hex[:8]

        conversation_id = f"{agent_number}_{caller_number}_{random_part}"
        applog.info("Generated conversationId=%s", conversation_id)

        return conversation_id

    @staticmethod
    def update_chat_data(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update chat data with new parameters.

        Args:
            params: Parameters to update
                - conversationId: (optional) if provided, this will be used as-is
                - userId: Mobile number
                - opportunityId: Opportunity ID
                - chatStarted: Creation timestamp (ISO format)
                - chatChannel: Chat channel name (e.g., "VoiceBot", "ChatBot")
                - customerId: Customer ID
                - customerEmail: Customer email
                - customerFirstName: Customer first name
                - customerLastName: Customer last name
                - customerPhone: Customer phone
                - device: Device identifier
                - conversation: FULL chat history list (optional)
                - opportunity_payload: Opportunity request payload (optional)
                - opportunity_response: Opportunity API response (optional)

        Returns:
            Updated chat data dictionary
        """
        base_data = {
            "conversationId": "20251116165136-3f9b1c2d4e5f",
            "userId": "guest_b2d2f42f-6cd2-4a88-8412-5d67a7777038",
            "agent_id": "",
            "attended_agent_list": [],
            "booking_date": "",
            "chat_channel": "ChatBot",
            "chat_model_name": "",
            "chat_model_version": "v13",
            "chat_modified": "2025-11-16T16:51:42.905996",
            "chat_name": "New Chat",
            "chat_started": "2025-11-16T16:51:36.727253",
            "chat_status": "active",
            "chat_summary": "",
            "conversation": [],  # will be overridden if params["conversation"] is provided
            "customerId": "guest_user",
            "customer_email": "",
            "customer_first_name": "",
            "customer_last_name": "",
            "customer_phone": "",
            "dataset_version": "1.5",
            "device": "Others",
            "events": [
                {
                    "event_type": "user_left",
                    "metadata": {},
                    "reason": "User left the chat",
                    "timestamp": "2025-11-16T16:51:42.905679",
                }
            ],
            "handoff_history": [],
            "is_chat_open": True,
            "is_utm_user": "regular",
            "is_with_agent": False,
            "opportunity_id": "",
            "opportunity_status": "",
            "packages_saved": [],
            "session_status": "with_bot",
            "opportunity_payload": {},
            "opportunity_response": {},
        }

        timestamp = params.get("chatStarted") or datetime.now().isoformat()
        updated_data = base_data.copy()

        # 🔹 If conversationId is provided, reuse it (so it matches ChatHistory)
        conversation_id = params.get("conversationId")
        if conversation_id:
            updated_data["conversationId"] = conversation_id
        else:
            # Extract agent and caller numbers from params
            agent_number = params.get("agentPhone") or params.get("agentNumber")
            caller_number = params.get("customerPhone") or params.get("userId")
            updated_data["conversationId"] = ChatDataUpdater.generate_conversation_id(
                agent_number=agent_number,
                caller_number=caller_number,
                timestamp=timestamp
            )
        applog.info(
            "Updating chat data | conversationId=%s | provided_conversationId=%s",
            updated_data["conversationId"],
            bool(conversation_id),
        )

        # Update fields
        updated_data["userId"] = params.get("userId") or base_data.get("userId")
        updated_data["chat_channel"] = params.get("chatChannel") or base_data.get(
            "chat_channel"
        )
        updated_data["chat_started"] = timestamp
        updated_data["opportunity_id"] = params.get(
            "opportunityId", base_data.get("opportunity_id", "")
        )
        updated_data["customerId"] = params.get("customerId") or base_data.get(
            "customerId"
        )
        updated_data["chat_modified"] = datetime.now().isoformat()

        # 🔹 Copy conversation if caller passed it
        if "conversation" in params and params["conversation"]:
            updated_data["conversation"] = params["conversation"]
            applog.info(
                "Attached conversation to chat data | messages=%s",
                len(params["conversation"]),
            )

        # 🔹 Copy opportunity_payload and opportunity_response if provided
        if "opportunity_payload" in params and params["opportunity_payload"]:
            updated_data["opportunity_payload"] = params["opportunity_payload"]
            applog.info("Attached opportunity_payload to chat data")

        if "opportunity_response" in params and params["opportunity_response"]:
            updated_data["opportunity_response"] = params["opportunity_response"]
            applog.info("Attached opportunity_response to chat data")

        applog.info(
            "Chat data populated | userId=%s | opportunityId=%s | chatChannel=%s",
            updated_data["userId"],
            updated_data.get("opportunity_id"),
            updated_data.get("chat_channel"),
        )

        return updated_data

    @staticmethod
    def send_to_api(data: Dict[str, Any], url: str = None) -> bool:
        """
        Send updated chat data to the API endpoint.

        Args:
            data: Chat data dictionary
            url: API endpoint URL (uses default if not provided)

        Returns:
            True if successful, False otherwise
        """
        api_url = url or elastic_search_url

        applog.info(
            "Sending chat data to API | url=%s | conversationId=%s",
            api_url,
            data.get("conversationId"),
        )

        try:
            response = requests.post(
                api_url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code in [200, 201]:
                applog.info(
                    "Chat data sent successfully | url=%s | status=%s",
                    api_url,
                    response.status_code,
                )
                return True
            else:
                applog.error(
                    "Failed to send chat data | url=%s | status=%s | response=%s",
                    api_url,
                    response.status_code,
                    response.text,
                )
                return False

        except requests.exceptions.RequestException as e:
            applog.error(
                "Error sending chat data request | url=%s | error=%s", api_url, str(e)
            )
            return False
