import os
import random
import asyncio
import time
from dotenv import load_dotenv

# Environment: set APP_ENV=dev or APP_ENV=prod (defaults to prod)
APP_ENV = os.environ.get("APP_ENV", "dev")
load_dotenv(f".env_{APP_ENV}", override=True)
print(f"[ENV] Loaded .env_{APP_ENV}")


BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:8000")

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, room_io, TurnHandlingOptions, inference
from livekit.plugins import google, silero, deepgram, sarvam
from google.genai.types import HttpOptions, ThinkingConfig
from speech_tuner import attach_speech_tuner
from interruption_guard import attach_interruption_guard
from tools import (
    get_travel_package,
    get_all_bogo_packages,
    get_fare_calendar,
    search_packages_by_name,
    get_package_pricing,
)
from opportunity_create import create_opportunity_tool
from prompts import get_agent_config
from chat_history import ChatHistory
from session_end_processor import process_session_end
from app_logger import applog
from google_tools import get_destination_info, get_destination_food, get_destination_weather, get_destination_sightseeing, get_destination_activities, get_destination_visa_info, get_destination_hotels, recommend_destinations, get_destination_flights, create_custom_itinerary, update_custom_itinerary


# Messages used for re-engaging the user during long silence
REPROMPT_MESSAGES = [
    "Are you still there? I'm happy to continue helping with your travel plans...",
    "No rush at all! Just let me know when you're ready to keep going.",
    "Still with me? We were looking at some great options earlier...",
    "Everything okay? I'm right here whenever you're ready to continue.",
    "Just checking in — shall we keep exploring travel ideas?",
    "I'm here! Ready to pick up where we left off whenever you are.",
]

class Assistant(agents.Agent):
    def __init__(self, full_instructions: str) -> None:
        super().__init__(
            instructions=full_instructions,
        )

    async def on_user_turn_completed(
        self,
        turn_ctx: agents.ChatContext,
        new_message: agents.llm.ChatMessage,
    ) -> None | agents.llm.LLMStream:
        return None


server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    user_id = "guest_user"
    customer_id = "guest_user"
    device = "VoiceBot"

    # Extract customer ID (mobile number) and app_id from room name
    app_id = "unknown"
    try:
        if ctx.room and ctx.room.name:
            user_id = ctx.room.name
            parts = user_id.strip().split("_")
            app_id = parts[0] if parts else "unknown"
            customer_id = parts[1][-10:] if len(parts) > 1 else "guest_user"
            print(f"[CUSTOMER ID EXTRACTED] {customer_id}  [APP_ID] {app_id}")
    except Exception as e:
        print(f"[CUSTOMER ID EXTRACTION FAILED] {e}")
        customer_id = "guest_user"

    # Fetch agent config from bridge (prompt + opp_tool_calls)
    try:
        full_instructions, opp_tool_calls, custom_prompt = await get_agent_config(app_id, customer_id)
        if custom_prompt:
            print(f"[CONFIG] Using custom prompt ({len(custom_prompt)} chars)")
        print(f"[CONFIG] opp_tool_calls={opp_tool_calls}")
    except Exception as e:
        print(f"[CONFIG] Failed to fetch agent config: {e}")
        # Fallback to something if API is completely unreachable
        full_instructions = "You are a travel assistant."
        custom_prompt = ""
        opp_tool_calls = "all"

    # ────────────────────────────────────────────────
    #               Session Configuration
    # ────────────────────────────────────────────────
    session = AgentSession(
        stt=deepgram.STT(
            model="nova-2",
            language="hi",
            interim_results=True
        ),
        llm=google.LLM(
            model="gemini-3.5-flash-lite",
            vertexai=False,
            api_key="DummyAPIKey",
            http_options=HttpOptions(base_url="http://10.160.0.6:8000"),
            temperature=0.5,
        ),
        tts=sarvam.TTS(
            model="bulbul:v3",
            speaker="ritu",
            speech_sample_rate=8000,
            pace=1.0,
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(
                version="v1-mini"
            ),
            endpointing={
                "mode": "fixed",
                "min_delay": 0.3,
                "max_delay": 2.5,
            },
            interruption = {
                "mode": "vad",  # no LiveKit Cloud -> "adaptive" isn't usable
                "min_words": 2,  # ignore 0-1 word blips (STT-based, local)
                "discard_audio_if_uninterruptible": False,  # keep STT running during non-interruptible speech (e.g. the fixed greeting / reprompts)
                "false_interruption_timeout": 1.2,
                "resume_false_interruption": True,
            },
            preemptive_generation={
                "preemptive_tts": False,
            },
        ),
        vad=silero.VAD.load(
            activation_threshold=0.55,
            min_silence_duration=0.6,
            min_speech_duration=0.15,
            prefix_padding_duration=0.15,
            sample_rate=16000,
            force_cpu=True,
        ),
        tools=[
            get_travel_package, 
            get_fare_calendar,
            get_all_bogo_packages,
            search_packages_by_name,
            get_package_pricing,
            get_destination_info,
            get_destination_food,
            get_destination_weather,
            get_destination_sightseeing,
            get_destination_activities,
            get_destination_visa_info,
            get_destination_hotels,
            recommend_destinations,
        ],
        user_away_timeout=20.0,
    )

    chat_history = ChatHistory(
        user_id=user_id,
        customer_id=customer_id,
        device=device,
        chat_channel="VoiceBot"
    )

    # ────────────────────────────────────────────────
    #         Variables for away/re-prompt logic
    # ────────────────────────────────────────────────
    away_prompt_task: asyncio.Task | None = None
    last_prompt_time = 0.0
    REPROMPT_INTERVAL = 35.0
    MAX_REPROMPTS = 5
    reprompt_count = 0
    is_agent_speaking = False
                                                     
    # ────────────────────────────────────────────────
    #                Event Handlers
    # ────────────────────────────────────────────────

    @session.on("conversation_item_added")
    def on_msg(event):
        item = event.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)

        # Only capture user/assistant text messages.
        # Tool call inputs and outputs are captured directly inside each
        # @function_tool in tools.py via current_chat_history.
        if role in ("user", "assistant") and text:
            chat_history.add_message(role, text)

    # ────────────────────────────────────────────────
    #   Session close → create/update opportunity
    # ────────────────────────────────────────────────

    # Store task reference to prevent garbage collection
    opportunity_task = None

    @session.on("close")
    def on_close(_event):
        nonlocal opportunity_task
        # Guard: livekit can fire "close" more than once (participant disconnect + teardown)
        if opportunity_task is not None and not opportunity_task.done():
            applog.info("[ON_CLOSE] process_session_end already running — skipping duplicate")
            return
                                                           
        applog.info("[ON_CLOSE] Session close event triggered")
        chat_history.add_event("user_left", "Session closed")

        applog.info("[ON_CLOSE] Launching process_session_end...")
        opportunity_task = asyncio.create_task(
            process_session_end(
                chat_history_obj=chat_history,
                customer_id=customer_id,
                opp_tool_calls=opp_tool_calls,
            )
        )

        def on_task_done(task):
            try:
                task.result()
                applog.info("[ON_CLOSE] process_session_end completed successfully")
            except Exception as e:
                applog.error(f"[ON_CLOSE] process_session_end failed: {e}")

        opportunity_task.add_done_callback(on_task_done)

    # ────────────────────────────────────────────────
    #         Agent / User state handlers
    # ────────────────────────────────────────────────

    @session.on("agent_state_changed")
    def on_agent_state_changed(event):
        nonlocal is_agent_speaking
        old = getattr(event, 'old_state', "unknown")
        new = getattr(event, 'new_state', "unknown")
        print(f"[AGENT STATE] {old} → {new}")

        if new == "speaking":
            is_agent_speaking = True
        elif new in ("idle", "listening", "thinking"):
            is_agent_speaking = False

    @session.on("user_state_changed")
    def on_user_state_changed(event):
        asyncio.create_task(_handle_user_state_changed(event))

    async def _handle_user_state_changed(event):
        nonlocal away_prompt_task, last_prompt_time, reprompt_count

        old = getattr(event, 'old_state', "unknown")
        new = getattr(event, 'new_state', "unknown")
        print(f"[USER STATE] {old} → {new}")

        if new == "away":
            print("[AWAY DETECTED] Starting re-prompt loop")
            reprompt_count = 0  # Reset counter for this silence period
            last_prompt_time = time.time() - REPROMPT_INTERVAL + 5.0
            if away_prompt_task is None or away_prompt_task.done():
                away_prompt_task = asyncio.create_task(_re_prompt_while_away())

        elif old == "away" and new != "away":
            print("[USER BACK]")
            if away_prompt_task and not away_prompt_task.done():
                away_prompt_task.cancel()
                try:
                    await away_prompt_task
                except asyncio.CancelledError:
                    pass
            away_prompt_task = None
            last_prompt_time = 0.0
            reprompt_count = 0
                                          
    async def _re_prompt_while_away():
        nonlocal last_prompt_time, reprompt_count
        try:
            while True:
                await asyncio.sleep(1.0)
                now = time.time()
                if (now - last_prompt_time >= REPROMPT_INTERVAL) and not is_agent_speaking:
                    reprompt_count += 1
                    if reprompt_count > MAX_REPROMPTS:
                        print(f"[RE-PROMPT] Max reprompts ({MAX_REPROMPTS}) exceeded — disconnecting call")
                        try:
                            await session.say(
                                "I'm not getting any response from your end. It seems like you may not be available right now. I'll go ahead and end this call. Feel free to call us back anytime. Goodbye!",
                                allow_interruptions=False,
                            )
                            await asyncio.sleep(3.0)
                        except Exception:
                            pass
                        await ctx.room.disconnect()
                        return
                    print(f"[RE-PROMPT] Sending message ({reprompt_count}/{MAX_REPROMPTS})")
                    message = random.choice(REPROMPT_MESSAGES)
                    try:
                        await session.say(message, allow_interruptions=True)
                        last_prompt_time = now
                    except Exception as e:
                        print(f"[RE-PROMPT ERROR] {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[RE-PROMPT LOOP ERROR] {e}")

    last_activity_time = time.time()

    @session.on("conversation_item_added")
    def reset_silence_timer(event):
        nonlocal last_activity_time
        if getattr(event.item, "role", None) in ("user", "assistant"):
            last_activity_time = time.time()

    async def silence_monitor():
        while True:
            await asyncio.sleep(4)
            idle = time.time() - last_activity_time
            print(f"[SILENCE] {idle:.1f}s")

    # ────────────────────────────────────────────────
    #   Speech-type detection & dynamic STT/TTS tuning
    #   Every 5 turns, classify the customer's recent speech
    #   pattern and retune STT/TTS via speech_tuner.apply_category.
    # ────────────────────────────────────────────────
    # attach_speech_tuner(session, session_label=customer_id)

    attach_interruption_guard(session, session_label=customer_id)
    
    # ────────────────────────────────────────────────
    #               Start the session
    # ────────────────────────────────────────────────
    await session.start(
        room=ctx.room,
        agent=Assistant(full_instructions=full_instructions),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
    
            ),
        ),
    )

    asyncio.create_task(silence_monitor())

    # Provide an initial opening reply
    if custom_prompt:
        # Outbound: custom prompt from dashboard → let LLM generate
        await session.generate_reply(
            instructions=custom_prompt,
            allow_interruptions=False,
        )
    else:
        # Inbound: speak exact greeting verbatim via TTS
        await session.say(
            "Hi, I am Tacy, your AI Destination Expert. आप मुझसे English और Hindi दोनों में बात कर सकते हैं। How can I help you plan an amazing trip today?",
            allow_interruptions=False,
        )


if __name__ == "__main__":
    from livekit.agents import WorkerOptions

    # AGENT_NAME unset (e.g. in .env_dev) -> automatic dispatch, so the
    # LiveKit Agents Playground can join without an explicit dispatch request.
    # AGENT_NAME set (e.g. in .env_prod) -> explicit dispatch, required by
    # c-zen-bridge/telephone.py which calls RoomAgentDispatch(agent_name=...).
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=my_agent,
            agent_name=os.environ.get("AGENT_NAME", ""),
            port=int(os.environ.get("AGENT_PORT", 8081))
        )
    )
