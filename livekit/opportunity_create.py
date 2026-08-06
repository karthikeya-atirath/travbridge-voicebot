import os
import re
import asyncio
import requests
from datetime import datetime

from livekit.agents import function_tool, RunContext
from app_logger import applog
from chat_data_updater import ChatDataUpdater
import chat_history  # 🔹 import the module, not the variable


# ---------------------------------------------------
# CONFIGURATION: URL FROM ENV (NO HARDCODE)
# ---------------------------------------------------
opportunity_create_url = os.getenv("opportunity_create_url")

if not opportunity_create_url:
    applog.error("❌ opportunity_create_url is not set in environment variables.")
    raise RuntimeError("opportunity_create_url environment variable is required")


# ---------------------------------------------------
# ENVIRONMENT VARIABLES FOR OPPORTUNITY MANAGEMENT
# ---------------------------------------------------
opportunity_get_opp_id_url = os.getenv("OPPORTUNITY_GET_OPP_ID_URL")
opportunity_update_url = os.getenv("OPPORTUNITY_UPDATE_URL")

if not opportunity_get_opp_id_url:
    applog.error("❌ OPPORTUNITY_GET_OPP_ID_URL is not set in environment variables.")
    raise RuntimeError("OPPORTUNITY_GET_OPP_ID_URL environment variable is required")

if not opportunity_update_url:
    applog.error("❌ OPPORTUNITY_UPDATE_URL is not set in environment variables.")
    raise RuntimeError("OPPORTUNITY_UPDATE_URL environment variable is required")


# ---------------------------------------------------
# FUNCTION 1: CHECK IF OPPORTUNITY EXISTS
# ---------------------------------------------------
async def check_opportunity_id_exists(mobile_no: str, email: str):
    """
    Check if an opportunity ID exists for the given mobile number and email.
      Args:
        customer_opportunity_id (str): The ID of the opportunity to update.
        mobile (str): The mobile number of the customer.
        package_id (str): The ID of the package.
        package_name (str): The name of the package.
        email (str): The email address of the customer.
        destination (str): The destination.
        summary (str): Optional chat summary for the opportunity.
    Returns: str (opportunity ID) or None
    """
    try:
        applog.info(f"🔍 check_opportunity_id_exists: Checking mobile={mobile_no}, email={email}")

        payload = {
            "mobile": mobile_no,
            "email": email,
            "lead_source": "TravBridge",
            "division": "TCIL",
        }

        applog.info(f"📤 check_opportunity_id_exists: Payload = {payload}")
        applog.info(f"📤 check_opportunity_id_exists: URL = {opportunity_get_opp_id_url}")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(opportunity_get_opp_id_url, json=payload, timeout=30)
        )

        applog.info(f"📥 check_opportunity_id_exists: Status Code = {response.status_code}")

        if response.status_code == 200:
            try:
                data_response = response.json()
                applog.info(f"📥 check_opportunity_id_exists: Response = {data_response}")

                status_code = data_response.get("status")

                if status_code in ["success", "sucess"]:  # Handles possible typo
                    opportunity_id = data_response.get("message")
                    applog.info(f"✅ check_opportunity_id_exists: Found opportunity_id = {opportunity_id}")
                    return opportunity_id
                elif status_code in [404, "404"]:
                    applog.info("❌ check_opportunity_id_exists: No opportunity found (404)")
                    return None
                else:
                    applog.warning(f"⚠️ check_opportunity_id_exists: Unexpected status = {status_code}")
                    return None

            except ValueError as e:
                applog.error(f"❌ check_opportunity_id_exists: JSON parse error = {str(e)}")
                return None
        else:
            applog.error(f"❌ check_opportunity_id_exists: HTTP error status = {response.status_code}")
            return None

    except requests.exceptions.Timeout:
        applog.error("❌ check_opportunity_id_exists: Request timeout")
        return None
    except requests.exceptions.RequestException as e:
        applog.error(f"❌ check_opportunity_id_exists: Request error = {str(e)}")
        return None
    except Exception as e:
        applog.error(f"❌ check_opportunity_id_exists: Unexpected error = {str(e)}")
        return None


# ---------------------------------------------------
# FUNCTION 2: UPDATE EXISTING OPPORTUNITY
# ---------------------------------------------------
async def update_opportunity(
    customer_opportunity_id: str,
    mobile: str,
    package_id: str,
    package_name: str,
    email: str,
    destination: str,
    summary: str = "",
    product: str = "ChatBot-Product Unknown",
):
    try:
        applog.info(f"🔄 update_opportunity: Updating opportunity_id={customer_opportunity_id}")

        payload = {
            "opp_id": customer_opportunity_id,
            "mobile": mobile,
            "product": product,
            "package_id": package_id,
            "package_name": package_name,
            "device": "Mobile",
            "summary": summary,
            "email": email,
            "destination": destination,
            "lead_source": "TravBridge",
            "division": "TCIL",
        }

        applog.info(f"📤 update_opportunity: Payload = {payload}")
        applog.info(f"📤 update_opportunity: URL = {opportunity_update_url}")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(opportunity_update_url, json=payload, timeout=30)
        )

        applog.info(f"📥 update_opportunity: Status Code = {response.status_code}")

        if response.status_code == 200:
            try:
                data_response = response.json()
                applog.info(f"📥 update_opportunity: Response = {data_response}")

                status = str(data_response.get("status", "")).lower()
                if status in ["200", "success"]:
                    message = data_response.get("message", "")
                    applog.info(f"✅ update_opportunity: Success - {message}")

                    match = re.search(r"Opportunity\s+(\d+)", message)
                    opportunity_id = match.group(1) if match else customer_opportunity_id

                    return {
                        "status": "success",
                        "opportunity_id": opportunity_id,
                        "message": message,
                        "payload": payload,  # 🔹 Return exact payload sent
                        "response": data_response  # 🔹 Return exact response received
                    }
                else:
                    applog.warning(f"⚠️ update_opportunity: Failed with status = {status}")
                    return {
                        "status": "failed",
                        "opportunity_id": customer_opportunity_id,
                        "message": f"Update failed with status: {status}",
                        "payload": payload,
                        "response": data_response
                    }

            except ValueError as e:
                applog.error(f"❌ update_opportunity: JSON parse error = {str(e)}")
                return {
                    "status": "failed",
                    "opportunity_id": customer_opportunity_id,
                    "message": f"JSON parse error: {str(e)}",
                    "payload": payload,
                    "response": response.text
                }
        else:
            applog.error(f"❌ update_opportunity: HTTP error status = {response.status_code}")
            return {
                "status": "failed",
                "opportunity_id": customer_opportunity_id,
                "message": f"HTTP error: {response.status_code}",
                "payload": payload,
                "response": response.text
            }

    except requests.exceptions.Timeout:
        applog.error("❌ update_opportunity: Request timeout")
        return {
            "status": "failed",
            "opportunity_id": customer_opportunity_id,
            "message": "Request timeout",
            "payload": payload,
            "response": None
        }
    except requests.exceptions.RequestException as e:
        applog.error(f"❌ update_opportunity: Request error = {str(e)}")
        return {
            "status": "failed",
            "opportunity_id": customer_opportunity_id,
            "message": f"Request error: {str(e)}",
            "payload": payload,
            "response": None
        }
    except Exception as e:
        applog.error(f"❌ update_opportunity: Unexpected error = {str(e)}")
        return {
            "status": "failed",
            "opportunity_id": customer_opportunity_id,
            "message": f"Unexpected error: {str(e)}",
            "payload": payload if 'payload' in locals() else None,
            "response": None
        }


# ---------------------------------------------------
# FUNCTION 3: ORCHESTRATION - CREATE OR UPDATE
# ---------------------------------------------------
async def create_or_update_opportunity(
    first_name: str,
    last_name: str,
    mobile: str,
    package_id: str,
    package_name: str,
    email: str,
    destination: str,
    summary: str = "",
    product: str = "",
):
    try:
        applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        applog.info("🚀 create_or_update_opportunity: STARTING WORKFLOW")
        applog.info(f"📞 Mobile: {mobile}, 📧 Email: {email}")
        applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        existing_opportunity_id = await check_opportunity_id_exists(mobile, email)

        if existing_opportunity_id:
            applog.info(f"✅ Found existing opportunity_id: {existing_opportunity_id}")
            applog.info("🔄 STEP 2A: Updating existing opportunity...")

            update_result = await update_opportunity(
                customer_opportunity_id=existing_opportunity_id,
                mobile=mobile,
                package_id=package_id,
                package_name=package_name,
                email=email,
                destination=destination,
                summary=summary,
                product= product,
            )

            if update_result and update_result.get("status") == "success":
                applog.info("✅ Opportunity updated successfully")
                applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return {
                    "action": "updated",
                    "opportunity_id": update_result.get("opportunity_id", existing_opportunity_id),
                    "status": "success",
                    "message": update_result.get("message", "Opportunity updated successfully"),
                    "payload": update_result.get("payload", {}),  # 🔹 Exact payload sent
                    "response": update_result.get("response", {})  # 🔹 Exact response received
                }
            else:
                applog.error("❌ Failed to update opportunity")
                applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return {
                    "action": "update_failed",
                    "opportunity_id": existing_opportunity_id,
                    "status": "failed",
                    "message": update_result.get("message", "Failed to update opportunity") if update_result else "Failed to update opportunity",
                    "payload": update_result.get("payload", {}) if update_result else {},
                    "response": update_result.get("response", {}) if update_result else {}
                }

        else:
            applog.info("❌ No existing opportunity found")
            applog.info("🆕 STEP 2B: Creating new opportunity...")

            opp = OpportunityCreate(
                first_name=first_name,
                last_name=last_name,
                mobile=mobile,
                package_id=package_id,
                package_name=package_name,
                summary=summary,
                email=email,
                destination=destination,
                product=product
            )

            loop = asyncio.get_event_loop()
            opp_result = await loop.run_in_executor(None, opp.create_opportunity)

            applog.info(f"📌 Raw Create Opportunity Result: {opp_result}")

            # Extract payload that was sent (stored in opp object)
            create_payload = {
                "key": "VHJhdkJyaWRnZQ==",
                "product": product,
                "lead_source": "TravBridge",
                "division": "TCIL",
                "campaign_id": "1881",
                "utmcmd": "cpc",
                "first_name": first_name,
                "last_name": last_name,
                "mobile": mobile,
                "package_id": package_id,
                "package_name": package_name,
                "summary": summary,
                "email": email,
                "destination": destination,
            }

            response_body = opp_result.get("response", {})
            message_text = response_body.get("message", "")

            opportunity_id = (
                response_body.get("opportunityId")
                or response_body.get("oppId")
                or response_body.get("lead_id")
                or response_body.get("id")
            )

            if not opportunity_id and message_text:
                match = re.search(r"Opportunity\s+(\d+)", message_text, re.IGNORECASE)
                if match:
                    opportunity_id = match.group(1)

            if opportunity_id:
                applog.info(f"✅ Opportunity created successfully | ID: {opportunity_id}")
                applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return {
                    "action": "created",
                    "opportunity_id": opportunity_id,
                    "status": "success",
                    "message": message_text or "Opportunity created successfully",
                    "payload": create_payload,  # 🔹 Exact payload sent
                    "response": response_body  # 🔹 Exact response received
                }
            else:
                applog.warning("⚠️ Opportunity creation attempted but NO VALID ID extracted!")
                applog.warning(f"Full response for debugging: {opp_result}")
                applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return {
                    "action": "create_failed",
                    "opportunity_id": None,
                    "status": "failed",
                    "message": "Opportunity creation failed: No valid opportunity ID received from server",
                    "payload": create_payload,
                    "response": response_body
                }

    except Exception as e:
        applog.error(f"❌ create_or_update_opportunity: Critical error = {str(e)}")
        import traceback
        applog.error(traceback.format_exc())
        applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return {
            "action": "error",
            "opportunity_id": None,
            "status": "failed",
            "message": f"Error in create_or_update workflow: {str(e)}",
            "payload": {},
            "response": {}
        }


# ---------------------------------------------------
# CORE CLASS FOR TRAVEL REQUEST CREATION
# ---------------------------------------------------
class OpportunityCreate:
    def __init__(
        self,
        first_name: str,
        last_name: str,
        mobile: str,
        package_id: str,
        package_name: str,
        summary: str,
        email: str,
        destination: str,
        product: str
    ) -> None:
        self.first_name = first_name
        self.last_name = last_name
        self.mobile = mobile
        self.package_id = package_id
        self.package_name = package_name
        self.summary = summary
        self.email = email
        self.destination = destination
        self.product = product if product else "ChatBot-Product Unknown"


    def create_opportunity(self):
        payload = {
            "key": "VHJhdkJyaWRnZQ==",
            "product": self.product,
            "lead_source": "TravBridge",
            "division": "TCIL",
            "campaign_id": "1881",
            "utmcmd": "cpc",
            "first_name": self.first_name,
            "last_name": self.last_name,
            "mobile": self.mobile,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "summary": self.summary,
            "email": self.email,
            "destination": self.destination,
        }

        applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        applog.info("📤 PREPARING TO SEND OPPORTUNITY REQUEST")
        applog.info(f"➡️ FINAL PAYLOAD SENT: {payload}")
        applog.info(f"➡️ POST URL: {opportunity_create_url}")
        applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        try:
            response = requests.post(opportunity_create_url, json=payload, timeout=30)

            applog.info("📥 RESPONSE RECEIVED")
            applog.info(f"⬅️ HTTP Status: {response.status_code}")

            try:
                response_json = response.json()
                applog.info("⬅️ Response Payload (JSON):")
                applog.info(response_json)
            except Exception:
                applog.info("⬅️ Response Payload (RAW):")
                applog.info(response.text)
                response_json = {"status": "unknown", "raw": response.text}

            applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            return {
                "status": response.status_code,
                "response": response_json,
            }

        except requests.exceptions.RequestException as e:
            applog.error("❌ NETWORK ERROR WHILE CREATING OPPORTUNITY")
            applog.error(str(e))
            return {"status": "failed", "error": str(e)}


# ---------------------------------------------------
# FUNCTION TOOL (USED BY AI + FRONTEND) - FIXED VERSION
# ---------------------------------------------------
@function_tool
async def create_opportunity_tool(
    ctx: RunContext,
    first_name: str,
    last_name: str,
    mobile: str,
    package_id: str,
    package_name: str,
    summary: str,
    email: str,
    destination: str,
    product: str
) -> dict:

    applog.info("🧭 Triggered: create_opportunity_tool()")
    applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # ── Log function call to chat history ─────────────────────────────────
    import json as _json
    _ct_args = _json.dumps({
        "first_name": first_name, "last_name": last_name, "mobile": mobile,
        "package_id": package_id, "package_name": package_name,
        "email": email, "destination": destination, "summary": summary, "product": product,
    })
    if chat_history.current_chat_history:
        chat_history.current_chat_history.add_function_call("create_opportunity_tool", _ct_args)
    # ──────────────────────────────────────────────────────────────────────

    opp_result = await create_or_update_opportunity(
        first_name=first_name,
        last_name=last_name,
        mobile=mobile,
        package_id=package_id,
        package_name=package_name,
        email=email,
        destination=destination,
        summary=summary,
        product = product
    )

    applog.info("📌 Opportunity Workflow Result:")
    applog.info(opp_result)

    # --- CRITICAL FIX: Do NOT fallback to fake "UNKNOWN_OPPORTUNITY" ---
    opportunity_id = opp_result.get("opportunity_id")  # Can be None!
    action_taken = opp_result.get("action", "unknown")
    workflow_status = opp_result.get("status", "unknown")

    applog.info(f"📌 Opportunity Workflow Action: {action_taken}")
    applog.info(f"📌 Opportunity Workflow Status: {workflow_status}")
    applog.info(f"📌 Opportunity ID from workflow: {opportunity_id}")

    # Save to chat history — only real IDs or explicitly None
    if chat_history.current_chat_history is not None:
        ch_doc = chat_history.current_chat_history.doc

        if opportunity_id:
            ch_doc["opportunity_id"] = opportunity_id
            applog.info(f"✅ Saved real opportunity_id={opportunity_id} to chat history")
        else:
            ch_doc["opportunity_id"] = None
            applog.warning("⚠️ No valid opportunity_id received — saved as None in chat history")

        # Store EXACT opportunity payload and response from API
        ch_doc["opportunity_payload"] = opp_result.get("payload", {})
        ch_doc["opportunity_response"] = opp_result.get("response", {})
        applog.info(f"✅ Saved EXACT opportunity_payload and opportunity_response to chat history")
    else:
        applog.warning("⚠️ chat_history.current_chat_history is None - cannot save opportunity_id")

    updated_data = {}

    if chat_history.current_chat_history is not None:
        ch = chat_history.current_chat_history
        doc = ch.doc

        conversation_id = doc.get("conversationId")
        conversation_list = doc.get("conversation", [])

        applog.info(
            f"📒 Using existing conversationId={conversation_id}, "
            f"messages={len(conversation_list)}"
        )

        update_params = {
            "conversationId": conversation_id,
            "userId": mobile or doc.get("userId", ""),
            "opportunityId": opportunity_id,  # Can be None → backend should handle it
            "chatStarted": doc.get("chat_started"),
            "chatChannel": doc.get("chat_channel", "VoiceBot"),
            "device": doc.get("device", "VoiceBot"),
            "customerId": doc.get("customerId", "guest_user"),
            "customer_email": doc.get("customer_email", ""),
            "customer_first_name": doc.get("customer_first_name", ""),
            "customer_last_name": doc.get("customer_last_name", ""),
            "customer_phone": doc.get("customer_phone", ""),
            "conversation": conversation_list,
            "opportunity_payload": doc.get("opportunity_payload", {}),
            "opportunity_response": doc.get("opportunity_response", {}),
        }

        applog.info("📤 Updating Chat Data with (from ChatHistory):")
        applog.info(update_params)

        updater = ChatDataUpdater()
        updated_data = updater.update_chat_data(update_params)

        applog.info("📥 Chat Data Updated:")
        applog.info(updated_data)

        try:
            updater.send_to_api(updated_data)
            applog.info("📤 Chat Data (with conversation) sent to API successfully")
        except Exception as e:
            applog.error(f"❌ Failed to send chat data with conversation: {str(e)}")

    else:
        applog.warning("⚠️ chat_history.current_chat_history is None → sending minimal payload")
        timestamp = datetime.now().isoformat()
        update_params = {
            "userId": mobile,
            "opportunityId": opportunity_id,  # Still pass None if no ID
            "chatStarted": timestamp,
            "chatChannel": "VoiceBot",
        }

        updater = ChatDataUpdater()
        updated_data = updater.update_chat_data(update_params)

        applog.info("📥 Chat Data Updated (fallback):")
        applog.info(updated_data)

        try:
            updater.send_to_api(updated_data)
            applog.info("📤 Fallback Chat Data sent to API successfully")
        except Exception as e:
            applog.error(f"❌ Failed to send fallback chat data: {str(e)}")

    applog.info("🧭 Completed create_opportunity_tool()")
    applog.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    _ct_result = {
        "opportunity_result": opp_result,
        "opportunity_id": opportunity_id,
        "action_taken": action_taken,
        "chat_update": updated_data,
    }
    # Log output to chat history
    if chat_history.current_chat_history:
        _out = f"action={action_taken}, opportunity_id={opportunity_id}, status={opp_result.get('status')}"
        chat_history.current_chat_history.add_function_call_output("create_opportunity_tool", _out)

    return _ct_result