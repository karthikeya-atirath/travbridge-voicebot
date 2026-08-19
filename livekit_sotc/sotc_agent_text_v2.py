import os
import json
import asyncio
from dotenv import load_dotenv

APP_ENV = os.environ.get("APP_ENV", "dev")
load_dotenv(f".env_{APP_ENV}")

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, room_io
from livekit.plugins import google, silero, deepgram, sarvam
from google.genai.types import HttpOptions, ThinkingConfig
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from tools import (
    get_travel_package,
    get_all_bogo_packages,
    get_fare_calendar,
    search_packages_by_name,
    get_package_pricing,
)
from opportunity_create import create_opportunity_tool
from sotc_aa_prompt import AGENT_INSTRUCTION, INITIAL_GREETING, TIME_GREETING
from chat_history import ChatHistory
from app_logger import applog
from google_tools import (
    get_destination_info,
    get_destination_food,
    get_destination_weather,
    get_destination_sightseeing,
    get_destination_activities,
    get_destination_visa_info,
    get_destination_hotels,
    recommend_destinations,
    get_destination_flights,
    create_custom_itinerary,
    update_custom_itinerary,
)


class Assistant(agents.Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)

    async def on_user_turn_completed(
        self,
        turn_ctx: agents.ChatContext,
        new_message: agents.llm.ChatMessage,
    ) -> None | agents.llm.LLMStream:
        return None


server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    user_id = ctx.room.name if ctx.room and ctx.room.name else "guest_user"
    parts = user_id.strip().split("_")
    app_id = parts[0] if parts else "unknown"
    customer_id = parts[1][-10:] if len(parts) > 1 else "guest_user"
    print(f"[INIT] app_id={app_id}  customer_id={customer_id}")

    session = AgentSession(
        stt=sarvam.STT(
            model="saaras:v3",
            language="hi-IN",
            mode="codemix",
        ),
        llm=google.LLM(
            model="gemini-3.5-flash-lite",
            vertexai=False,
            api_key="DummyAPIKey",
            http_options=HttpOptions(base_url="http://10.160.0.6:8000"),
            temperature=0.5,
        ),
        vad=silero.VAD.load(
            activation_threshold=0.55,
            min_silence_duration=0.8,
            min_speech_duration=0.15,
            prefix_padding_duration=0.15,
            sample_rate=16000,
            force_cpu=True,
        ),
        turn_detection=MultilingualModel(),
        min_endpointing_delay=1.0,
        max_endpointing_delay=2.5,
        preemptive_generation=True,
        allow_interruptions=False,
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
    )

    chat_history = ChatHistory(
        user_id=user_id,
        customer_id=customer_id,
        device="TextBot",
        chat_channel="TextBot",
    )

    async def _publish_chat_message(role: str, content: str):
        """Publish a chat message to React via LiveKit data channel."""
        try:
            payload = json.dumps({
                "event": "chat_message",
                "role": role,
                "content": content,
            }, ensure_ascii=False)
            await ctx.room.local_participant.publish_data(
                payload,
                topic="chat_events",
                reliable=True,
            )
            applog.info(f"[REACT] Published {role} message to React ({len(content)} chars)")
        except Exception as e:
            applog.error(f"[REACT] Failed to publish chat message: {e}")

    _last_published_text = {"value": ""}

    @session.on("conversation_item_added")
    def on_msg(event):
        item = event.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        applog.info(f"[MSG] conversation_item_added: role={role}, has_text={bool(text)}, text_preview={repr((text or '')[:80])}")
        if role in ("user", "assistant") and text:
            # Guard against duplicate publishes of the exact same text
            if text == _last_published_text["value"]:
                applog.info(f"[MSG] Skipping duplicate {role} message")
                return
            _last_published_text["value"] = text
            chat_history.add_message(role, text)
            # Push to React in real-time
            asyncio.create_task(_publish_chat_message(role, text))

    @session.on("function_tools_executed")
    def on_tools_executed(event):
        try:
            applog.info(f"[TOOLS] function_tools_executed fired with {len(event.function_calls)} call(s)")
            for fn_call, fn_output in event.zipped():
                applog.info(f"[TOOLS] Function call: {fn_call.name}({fn_call.arguments[:100]}...)")
                # Tools in google_tools.py and tools.py manually write themselves to ChatHistory
                # using _log_tool_call() and _log_tool_output(). We must NOT log them again here
                # otherwise they appear twice in the DynamoDB / conversation transcript.

                # ── Push to React in real-time via LiveKit data channel ────
                asyncio.create_task(
                    _publish_tool_event(fn_call, fn_output)
                )
        except Exception as e:
            applog.error(f"[TOOLS] Error in function_tools_executed handler: {e}", exc_info=True)

    async def _publish_tool_event(fn_call, fn_output):
        MAX_PAYLOAD_BYTES = 14_000  # stay under LiveKit ~16 KB data-channel limit
        applog.info(f"[TOOL_EVENT] _publish_tool_event entered for {fn_call.name}")

        try:
            # We only publish the final output since function_tools_executed fires AFTER completion.

            # 2. Publish the output (updates card to success/error in React)
            if fn_output is not None:
                try:
                    # These tools produce card_data — send only slim payload to frontend
                    _CARD_DATA_TOOLS = {"get_travel_package", "get_all_bogo_packages", "search_packages_by_name"}
                    if fn_call.name in _CARD_DATA_TOOLS:
                        from tools import _latest_card_data
                        if _latest_card_data:
                            raw = fn_output.output
                            # Get count/destination from output
                            count = 0
                            destination = ""
                            if isinstance(raw, dict):
                                count = raw.get("count", 0)
                                destination = raw.get("destination", "")
                            elif isinstance(raw, str):
                                try:
                                    parsed = json.loads(raw)
                                    count = parsed.get("count", 0)
                                    destination = parsed.get("destination", "")
                                except Exception:
                                    pass

                            slim_output = json.dumps({
                                "card_data": _latest_card_data,
                                "count": count,
                                "destination": destination,
                            })
                            output_payload = json.dumps({
                                "event": "function_call_output",
                                "name": fn_output.name or fn_call.name,
                                "output": slim_output,
                                "call_id": fn_output.call_id,
                                "is_error": fn_output.is_error,
                            }, ensure_ascii=False)
                            applog.info(f"[TOOL_EVENT] Sending card_data for {fn_call.name}: {len(_latest_card_data)} packages, {len(output_payload)} bytes")
                            applog.info(f"[TOOL_EVENT] FULL PAYLOAD: {output_payload}")
                        else:
                            applog.warning(f"[TOOL_EVENT] No card_data available for {fn_call.name}, skipping")
                            return
                    else:
                        output_payload = json.dumps({
                            "event": "function_call_output",
                            "name": fn_output.name or fn_call.name,
                            "output": fn_output.output,
                            "call_id": fn_output.call_id,
                            "is_error": fn_output.is_error,
                        }, ensure_ascii=False)

                    await ctx.room.local_participant.publish_data(
                        output_payload,
                        topic="tool_events",
                        reliable=True,
                    )
                    applog.info(f"[TOOL_EVENT] Published output for {fn_call.name} ({len(output_payload)} bytes)")
                except Exception as e:
                    applog.error(f"[TOOL_EVENT] Failed to publish output for {fn_call.name}: {e}", exc_info=True)
        except Exception as e:
            applog.error(f"[TOOL_EVENT] CRITICAL: _publish_tool_event crashed for {fn_call.name}: {e}", exc_info=True)

    @session.on("close")
    def on_close(_event):
        chat_history.add_event("user_left", "Session closed")
        # Text bot: skip opportunity creation, only save chat history
        try:
            chat_history.send_to_dynamo()
            applog.info("[ON_CLOSE] ✓ Sent to DynamoDB")
        except Exception as e:
            applog.error(f"[ON_CLOSE] ✗ send_to_dynamo failed: {e}")
        try:
            chat_history.send_to_api()
            applog.info("[ON_CLOSE] ✓ Sent to Elastic Search API")
        except Exception as e:
            applog.error(f"[ON_CLOSE] ✗ send_to_api failed: {e}")

    session.output.set_audio_enabled(False)

    # Connect the agent to the room immediately so it can receive participant events
    await ctx.connect()

    # --- Discover participants: Customer + Call Center Agent ---
    customer_identity = None
    agent_identity = None
    for p in ctx.room.remote_participants.values():
        applog.info(f"[DEBUG] Existing participant found in room with identity: '{p.identity}'")
        if p.identity.startswith("customer_"):
            customer_identity = p.identity
        elif p.identity.startswith("agent_"):
            agent_identity = p.identity
            
    if not customer_identity:
        applog.info("Waiting for Customer to join (looking for identity starting with 'customer_')...")
        customer_fut = asyncio.Future()
        
        @ctx.room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant):
            nonlocal agent_identity
            applog.info(f"[DEBUG] New participant connected with identity: '{participant.identity}'")
            if participant.identity.startswith("customer_"):
                if not customer_fut.done():
                    customer_fut.set_result(participant.identity)
            elif participant.identity.startswith("agent_") and not agent_identity:
                agent_identity = participant.identity
                    
        customer_identity = await customer_fut

    # --- Extract agent name from identity ---
    # Format: agent_{AgentName}_{Email}_{OutlookId}_{CallId}
    agent_display_name = "Travel Agent"
    if agent_identity:
        remainder = agent_identity.removeprefix("agent_")
        id_parts = remainder.split("_")
        # Find the email part (contains @) — everything before it is the name
        email_idx = next((i for i, p in enumerate(id_parts) if "@" in p), -1)
        if email_idx > 0:
            agent_display_name = " ".join(id_parts[:email_idx])
        elif id_parts:
            agent_display_name = id_parts[0]
        applog.info(f"[INIT] Call Center Agent name: {agent_display_name}")

    applog.info(f"Customer identified as {customer_identity}. Starting AgentSession.")

    # --- Extract customer mobile from identity (e.g. "customer_9876543210") ---
    if customer_identity:
        customer_id = customer_identity.removeprefix("customer_")[-10:]
        applog.info(f"[INIT] Updated customer_id from identity: {customer_id}")

    # --- Build instructions with agent name + customer mobile ---
    instructions = AGENT_INSTRUCTION + f"""

AGENT IDENTITY (use this for the greeting and throughout the conversation):
- Call Center Agent's name: **{agent_display_name}**
- Customer's mobile number: {customer_id}

IMPORTANT: In the introduction and whenever referring to yourself, use the name "{agent_display_name}" instead of "Travel Agent".
The greeting MUST be: "{TIME_GREETING}! Welcome to **SOTC** — India's best travel company. I'm **{agent_display_name}**, your personal travel expert. How can we help you plan your next holiday today?"
"""

    agent_obj = Assistant(instructions=instructions)

    await session.start(
        room=ctx.room,
        agent=agent_obj,
        room_options=room_io.RoomOptions(participant_identity=customer_identity),
    )

    # --- Dual Transcription Logic for Call Center Agent ---
    
    def setup_manual_stt(participant: rtc.RemoteParticipant, track: rtc.Track):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
            
        # ONLY apply manual STT to Call Center Agents
        if not participant.identity.startswith("agent_"):
            return
            
        applog.info(f"[MANUAL STT] Starting background transcription for {participant.identity}")
        stt_plugin = sarvam.STT(model="saaras:v3", language="hi-IN", mode="codemix")
        stt_stream = stt_plugin.stream()
        
        async def process_track():
            audio_stream = rtc.AudioStream(track)
            async for event in audio_stream:
                stt_stream.push_frame(event.frame)
                
        async def listen_transcripts():
            async for event in stt_stream:
                if event.type == agents.stt.SpeechEventType.FINAL_TRANSCRIPT:
                    transcript = event.alternatives[0].text
                    if transcript.strip():
                        msg_content = f"[Call Center Agent]: {transcript.strip()}"
                        applog.info(f"{msg_content}")
                        
                        # 1. Silently inject into AI context (no interruption/response triggered)
                        #    Using role="developer" to avoid confusing the turn detector,
                        #    which expects alternating user/assistant messages.
                        new_ctx = agent_obj.chat_ctx.copy()
                        new_ctx.add_message(role="user", content=msg_content)
                        await agent_obj.update_chat_ctx(new_ctx)
                        
                        # 2. Add to chat history DB
                        chat_history.add_message("user", msg_content)
                        
                        # 3. Push to React in real-time
                        await _publish_chat_message("agent", transcript.strip())
                        
        asyncio.create_task(process_track())
        asyncio.create_task(listen_transcripts())

    # 1. Setup manual STT for participants who are already in the room
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track:
                setup_manual_stt(participant, publication.track)
                
    # 2. Setup manual STT for participants who join later
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        setup_manual_stt(participant, track)


if __name__ == "__main__":
    from livekit.agents import WorkerOptions
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=my_agent,
            agent_name="sotc-text-travel-bot-v2",
            port=8091,
        )
    )
