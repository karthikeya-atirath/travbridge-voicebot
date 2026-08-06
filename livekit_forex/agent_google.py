import logging
import json
import time
from google.genai import types
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import silero, openai
from opportunity_create import create_opportunity_tool
from livekit import agents
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    ConversationItemAddedEvent,
)

from livekit.plugins import google
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from tools import get_travel_package, get_package_pricing
from app_logger import applog
from chat_history import ChatHistory


# Minimal logging to reduce overhead
logging.basicConfig(level=logging.WARNING)  # Only warnings/errors by default
applog = logging.getLogger("livekit-app")
applog.setLevel(logging.INFO)


# =====================================================================
#                     ULTRA-LOW-LATENCY AGENT
# =====================================================================
class Assistant(Agent):
    def __init__(self, chat_history: ChatHistory):
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            #turn_detection=MultilingualModel(),
            llm=google.realtime.RealtimeModel(
                model="gemini-2.5-flash-native-audio-preview-12-2025",
                voice="Aoede",
                temperature=0.3,
                location="global",
                vertexai=False,
                proactivity=True,
                enable_affective_dialog=True,
                thinking_config=types.ThinkingConfig(include_thoughts=False),
            ),
            # Removed invalid parameter 'input_audio_transcription' (not supported in livekit-agents 1.3.x)
            tools=[get_travel_package, get_package_pricing, create_opportunity_tool],
        )

        self.chat_history = chat_history
        self.user_turn_end_time = None  # For delay measurement only

    async def on_user_turn_completed(self, ctx):
        self.user_turn_end_time = time.time()
        applog.info("User turn ended")

    async def on_agent_turn_started(self, ctx):
        if self.user_turn_end_time:
            delay = time.time() - self.user_turn_end_time
            applog.info(f"Agent response delay: {delay:.2f}s")
        else:
            applog.info("Agent started")


# =====================================================================
#                          MINIMAL ENTRYPOINT
# =====================================================================
async def entrypoint(ctx: agents.JobContext):
    # Extract metadata quickly (fallback to defaults)
    user_id = "guest_user"
    customer_id = "guest_user"
    device = "VoiceBot"

    # Extract room name and customer ID from LiveKit room
    try:
        if ctx.room and ctx.room.name:
            user_id = ctx.room.name  # e.g., "testroom_b6872582"
            # Extract customer ID (part after "testroom_")
            if user_id.startswith("testroom_"):
                customer_id = user_id.replace("testroom_", "")
            else:
                # If room name doesn't match expected pattern, extract after underscore
                parts = user_id.split("_")
                customer_id = parts[-1] if len(parts) > 1 else user_id
    except:
        pass

    try:
        if ctx.job and ctx.job.metadata:
            meta = json.loads(ctx.job.metadata)
            device = meta.get("device", device)
    except:
        pass

    # Chat history (kept minimal)
    chat_history = ChatHistory(user_id=user_id, customer_id=customer_id, device=device, chat_channel="VoiceBot")

    # Ultra-low-latency session config
    session = AgentSession(
        min_endpointing_delay=0.1,       # Very aggressive — fastest possible turn detection
        max_endpointing_delay=0.3,       # Prevent hanging on rare long silences
        preemptive_generation=True,      # Start speaking ASAP with partial response
    )

    # Capture only essential text messages
    @session.on("conversation_item_added")
    def on_msg(event: ConversationItemAddedEvent):
        item = event.item
        text = getattr(item, "text_content", None)
        if text and item.role in ("user", "assistant"):
            chat_history.add_message(item.role, text)

    # Save on close (minimal)
    @session.on("close")
    def on_close(event):
        chat_history.add_event("user_left", "Session closed")
        chat_history.save_json()
        chat_history.send_to_api()

    # Start session with absolute minimal options
    await session.start(
        room=ctx.room,
        agent=Assistant(chat_history=chat_history),
        room_input_options=RoomInputOptions(video_enabled=False),
    )

    # Initial instruction only if needed
    if SESSION_INSTRUCTION.strip():
        await session.generate_reply(instructions=SESSION_INSTRUCTION)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))