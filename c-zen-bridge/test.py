#!/usr/bin/env python3
import os
import asyncio
import base64
import json
import signal
import uuid
import time
import logging
import threading
from typing import Optional, Dict
from collections import deque
from urllib.parse import urlparse, parse_qs

import numpy as np
import websockets
from websockets.server import WebSocketServerProtocol
import wave

from livekit import rtc
from livekit.api import (
    AccessToken,
    VideoGrants,
    RoomConfiguration,
    RoomAgentDispatch,
)

from livekit.rtc import (
    AudioSource,
    AudioFrame,
    AudioTrack,
    AudioStream,
    RoomOptions,
    TrackKind,
    LocalAudioTrack,
)

from audio_uploader import upload_audio_to_gcs

# Flask Audio Player + Admin
from flask import Flask, render_template, request
from google.cloud import storage
from datetime import timedelta


# =========================
# Config & Logging Setup
# =========================
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "APIURXkwnDjd8Pa")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "trAsT9C033OZleHJ0tMNFjunov4k3mmmnjBWfHvq5Lo")

WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
WS_PING_INTERVAL_S = int(os.getenv("WS_PING_INTERVAL_S", "20"))
WS_PING_TIMEOUT_S = int(os.getenv("WS_PING_TIMEOUT_S", "20"))

TRACK_NAME_PUB = os.getenv("TRACK_NAME_PUB", "microphone")
FRAME_MS = int(os.getenv("FRAME_MS", "20"))
OUT_ENCODING = os.getenv("OUT_ENCODING", "linear16").lower()

SAMPLE_RATE = 48000
OUTPUT_SAMPLE_RATE = 8000
INPUT_SAMPLE_RATE = 8000

FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

APM_FRAME_MS = 10
APM_SAMPLES = int(SAMPLE_RATE * APM_FRAME_MS / 1000)

ENABLE_NOISE_SUPPRESSION = os.getenv("ENABLE_NOISE_SUPPRESSION", "true").lower() == "true"
ENABLE_HIGH_PASS = os.getenv("ENABLE_HIGH_PASS", "true").lower() == "true"
ENABLE_AGC = os.getenv("ENABLE_AGC", "false").lower() == "true"
ENABLE_AEC = os.getenv("ENABLE_AEC", "false").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("bridge")


# =========================
# Agent Mapping (dynamic)
# =========================
AGENT_MAPPING = {}
AGENT_MAPPING_FILE = "agent_mapping.json"

def load_agent_mapping():
    global AGENT_MAPPING
    try:
        if os.path.exists(AGENT_MAPPING_FILE):
            with open(AGENT_MAPPING_FILE, 'r') as f:
                AGENT_MAPPING = json.load(f)
            log.info(f"Loaded {len(AGENT_MAPPING)} agent mappings from {AGENT_MAPPING_FILE}")
        else:
            log.warning(f"Agent mapping file not found: {AGENT_MAPPING_FILE}. No dispatch will be applied.")
            AGENT_MAPPING = {}
    except Exception as e:
        log.error(f"Failed to load agent mapping: {e}")
        AGENT_MAPPING = {}


# Load mapping AFTER logging is set up
load_agent_mapping()


# =========================
# Flask Audio Player + Admin
# =========================
def start_audio_player():
    app = Flask(__name__, template_folder='templates')

    GCS_KEY_FILE = os.environ.get("GCS_KEY_FILE", "file.json")
    GCS_BUCKET_NAME = "voice_bot_travbridge"

    try:
        storage_client = storage.Client.from_service_account_json(GCS_KEY_FILE)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
    except Exception as e:
        print(f"Warning: Could not initialize GCS client for audio player: {e}")
        bucket = None

    @app.route('/')
    def index():
        search = request.args.get('search', '')
        files = []
        if bucket:
            try:
                blobs = bucket.list_blobs()
                for blob in blobs:
                    if search.lower() in blob.name.lower():
                        signed_url = blob.generate_signed_url(
                            version="v4",
                            expiration=timedelta(hours=1),
                            method="GET"
                        )
                        files.append({
                            'name': blob.name,
                            'url': signed_url
                        })
            except Exception as e:
                print(f"Error listing bucket in UI: {e}")

        return render_template('index.html', files=files, search=search)

    @app.route('/admin/mappings', methods=['GET', 'POST'])
    def manage_mappings():
        global AGENT_MAPPING
        message = None

        if request.method == 'POST':
            action = request.form.get('action')
            app_id = request.form.get('app_id')
            agent_name = request.form.get('agent_name')

            if action == 'add' or action == 'update':
                if app_id and agent_name:
                    AGENT_MAPPING[app_id] = agent_name
                    message = f"Mapping added/updated: {app_id} → {agent_name}"
            elif action == 'delete':
                if app_id in AGENT_MAPPING:
                    del AGENT_MAPPING[app_id]
                    message = f"Mapping deleted: {app_id}"

            try:
                with open(AGENT_MAPPING_FILE, 'w') as f:
                    json.dump(AGENT_MAPPING, f, indent=2)
                log.info("Agent mappings updated and saved")
                load_agent_mapping()  # Reload immediately
            except Exception as e:
                log.error(f"Failed to save agent mappings: {e}")
                message = f"Error saving mappings: {e}"

        return render_template('admin_mappings.html', mappings=AGENT_MAPPING, message=message)

    print("Starting Audio Player UI → http://0.0.0.0:8000")
    print("Admin panel → http://0.0.0.0:8000/admin/mappings")
    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)


# =========================
# DSP helpers
# =========================
def linear_resample_int16(pcm16: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if pcm16.size == 0 or sr_in == sr_out:
        return pcm16
    n_out = int(round(pcm16.size * (sr_out / sr_in)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.int16)
    x_old = np.linspace(0.0, 1.0, num=pcm16.size, endpoint=False, dtype=np.float32)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False, dtype=np.float32)
    y = np.interp(x_new, x_old, pcm16.astype(np.float32))
    return np.clip(y, -32768, 32767).astype(np.int16)

def rms_dbfs(pcm16: np.ndarray) -> float:
    if pcm16.size == 0:
        return -120.0
    s = pcm16.astype(np.float32)
    v = float(np.sqrt(np.mean(s * s)) + 1e-9)
    return 20.0 * np.log10(v / 32768.0)

def decode_livekit_frame(data_bytes: bytes, num_channels: int) -> np.ndarray:
    ch = max(1, num_channels or 1)
    n = len(data_bytes)
    if n % (2 * ch) == 0:
        pcm16 = np.frombuffer(data_bytes, dtype=np.int16)
        if ch > 1:
            pcm16 = pcm16.reshape(-1, ch).mean(axis=1).astype(np.int16)
        return pcm16
    if n % (4 * ch) == 0:
        f = np.frombuffer(data_bytes, dtype=np.float32)
        if ch > 1:
            f = f.reshape(-1, ch).mean(axis=1)
        f = np.clip(f, -1.0, 1.0)
        return (f * 32767.0).astype(np.int16)
    return np.zeros(0, dtype=np.int16)

def is_audio_kind(kind) -> bool:
    try:
        return kind == TrackKind.KIND_AUDIO
    except Exception:
        return False


# =========================
# Per-call bridge
# =========================
class CallBridge:
    def __init__(self, ws: WebSocketServerProtocol, app_id: Optional[str] = None, caller_id: Optional[str] = None):
        self.ws = ws
        self.app_id = app_id
        self.caller_id = caller_id
        self.uuid = uuid.uuid4().hex[:8]
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.tw_sr = INPUT_SAMPLE_RATE
        self.room: Optional[rtc.Room] = None
        self.audio_source: Optional[AudioSource] = None
        self.audio_track: Optional[LocalAudioTrack] = None
        self._tasks = set()
        self._closing = asyncio.Event()
        self._pub_q = deque(maxlen=50)
        self._pub_lock = asyncio.Lock()
        self.frames_sent_to_livekit = 0
        self.last_debug_time = time.time()

        self.customer_audio = []
        self.ai_audio = []

        self.apm = None
        self._apm_buf = np.zeros(0, dtype=np.int16)
        if ENABLE_NOISE_SUPPRESSION or ENABLE_HIGH_PASS or ENABLE_AGC or ENABLE_AEC:
            try:
                self.apm = rtc.AudioProcessingModule(
                    echo_cancellation=ENABLE_AEC,
                    noise_suppression=ENABLE_NOISE_SUPPRESSION,
                    high_pass_filter=ENABLE_HIGH_PASS,
                    auto_gain_control=ENABLE_AGC,
                )
                log.info("WebRTC APM enabled")
            except Exception as e:
                log.warning(f"APM unavailable: {e}")

    def _apm_process_pcm(self, pcm16: np.ndarray) -> np.ndarray:
        if self.apm is None or pcm16.size == 0:
            return pcm16

        self._apm_buf = np.concatenate([self._apm_buf, pcm16]) if self._apm_buf.size else pcm16
        out_chunks = []

        while self._apm_buf.size >= APM_SAMPLES:
            chunk = self._apm_buf[:APM_SAMPLES]
            self._apm_buf = self._apm_buf[APM_SAMPLES:]
            af = AudioFrame(
                data=chunk.tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=APM_SAMPLES,
            )
            try:
                self.apm.process_stream(af)
                processed = np.frombuffer(af.data, dtype=np.int16)
                out_chunks.append(processed.copy())
            except Exception as e:
                log.warning(f"APM process error: {e}")
                out_chunks.append(chunk)

        return np.concatenate(out_chunks) if out_chunks else np.zeros(0, dtype=np.int16)

    async def connect_livekit(self):
        unique_room_name = f"{self.app_id or 'unknown'}_{self.caller_id or 'unknown'}_{self.uuid}"

        grants = VideoGrants(
            room_join=True,
            room=unique_room_name,
            can_publish=True,
            can_subscribe=True
        )

        token_builder = (
            AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(f"ws-{self.stream_sid or uuid.uuid4().hex}")
            .with_name("WS Bridge")
            .with_grants(grants)
        )

        # Dynamic dispatch
        agent_name = AGENT_MAPPING.get(self.app_id)
        if agent_name:
            log.info(f"Dispatching to agent '{agent_name}' for app_id={self.app_id}")
            token_builder.with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=agent_name)]
                )
            )
        elif "default" in AGENT_MAPPING:
            log.info(f"Using default agent: {AGENT_MAPPING['default']}")
            token_builder.with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=AGENT_MAPPING['default'])]
                )
            )
        else:
            log.info(f"No agent mapping for app_id={self.app_id} → default worker")

        token = token_builder.to_jwt()

        self.room = rtc.Room()
        try:
            await self.room.connect(LIVEKIT_URL, token, options=RoomOptions(auto_subscribe=True))
            log.info(f"Connected to room: {self.room.name}")
        except Exception as e:
            log.error(f"LiveKit connect failed: {e}")
            raise

        self.audio_source = AudioSource(sample_rate=SAMPLE_RATE, num_channels=1)
        self.audio_track = LocalAudioTrack.create_audio_track(TRACK_NAME_PUB, self.audio_source)
        try:
            await self.room.local_participant.publish_track(self.audio_track)
            log.info(f"Published track: {TRACK_NAME_PUB}")
        except Exception as e:
            log.error(f"Publish failed: {e}")
            await self.disconnect_livekit()
            raise

        asyncio.create_task(self.flush_early_buffer())

        @self.room.on("track_subscribed")
        def _on_sub(track: AudioTrack, pub, participant):
            if not is_audio_kind(getattr(track, "kind", None)):
                return
            log.info(f"Subscribed to audio from {participant.identity}")
            t = asyncio.create_task(self.relay_lk_to_ws(track))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        for p in self.room.remote_participants.values():
            for pub in p.track_publications.values():
                if is_audio_kind(pub.kind):
                    await pub.set_subscribed(True)

    async def flush_early_buffer(self):
        async with self._pub_lock:
            while self._pub_q:
                frame = self._pub_q.popleft()
                if self.audio_source and frame.size:
                    af = AudioFrame(
                        data=frame.tobytes(),
                        sample_rate=SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=len(frame),
                    )
                    maybe = self.audio_source.capture_frame(af)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                    self.frames_sent_to_livekit += 1

    async def disconnect_livekit(self):
        for t in list(self._tasks):
            t.cancel()
        if self.room:
            await self.room.disconnect()
            self.room = None
        self.audio_source = None
        self.audio_track = None
        async with self._pub_lock:
            self._pub_q.clear()

    async def relay_lk_to_ws(self, track: AudioTrack):
        stream = AudioStream(track)
        buf = np.zeros(0, dtype=np.int16)
        try:
            async for event in stream:
                af = getattr(event, "frame", event)
                pcm = decode_livekit_frame(af.data, af.num_channels)
                buf = np.concatenate([buf, pcm]) if buf.size else pcm
                while buf.size >= FRAME_SAMPLES:
                    chunk = buf[:FRAME_SAMPLES]
                    buf = buf[FRAME_SAMPLES:]
                    self.ai_audio.append(chunk)
                    f_out = linear_resample_int16(chunk, SAMPLE_RATE, OUTPUT_SAMPLE_RATE)
                    payload_b64 = base64.b64encode(f_out.tobytes()).decode("ascii")
                    await self.ws.send(json.dumps({
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": payload_b64},
                    }))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Relay error: {e}")

    async def handle_start(self, msg: Dict):
        start = msg.get("start", {})
        self.stream_sid = start.get("streamSid")
        self.call_sid = start.get("callSid")
        self.tw_sr = INPUT_SAMPLE_RATE
        log.info(f"START streamSid={self.stream_sid} ({self.tw_sr}Hz)")
        try:
            await self.connect_livekit()
        except Exception as e:
            log.error(f"LiveKit start failed: {e}")
            await self.close()

    async def handle_media(self, msg: Dict):
        media = msg.get("media", {})
        if media.get("track") != "inbound":
            return
        payload_b64 = media.get("payload")
        if not payload_b64:
            return

        raw = base64.b64decode(payload_b64)
        pcm = np.frombuffer(raw, dtype="<i2")
        vu = rms_dbfs(pcm)
        log.info(f"VU in: {vu:.1f} dBFS")

        pcm_out = linear_resample_int16(pcm, self.tw_sr, SAMPLE_RATE)
        self.customer_audio.append(pcm_out)
        pcm_out = self._apm_process_pcm(pcm_out)

        if self.audio_source and pcm_out.size:
            idx = 0
            async with self._pub_lock:
                while idx + APM_SAMPLES <= pcm_out.size:
                    chunk = pcm_out[idx:idx + APM_SAMPLES]
                    idx += APM_SAMPLES
                    af = AudioFrame(
                        data=chunk.tobytes(),
                        sample_rate=SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=len(chunk),
                    )
                    maybe = self.audio_source.capture_frame(af)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                    self.frames_sent_to_livekit += 1

        else:
            async with self._pub_lock:
                if len(self._pub_q) >= self._pub_q.maxlen:
                    log.warning("Early buffer full, dropping frame")
                self._pub_q.append(pcm_out)

    async def handle_dtmf(self, msg: Dict):
        digit = msg.get("dtmf", {}).get("digit")
        if digit and self.room:
            try:
                await self.room.local_participant.publish_data(
                    json.dumps({"type": "dtmf", "digit": digit}).encode(),
                    reliable=True,
                )
                log.info(f"DTMF sent: {digit}")
            except Exception as e:
                log.error(f"DTMF publish error: {e}")

    async def handle_vad(self, msg: Dict):
        vad_val = msg.get("vad", {}).get("value")
        if vad_val is not None and self.room:
            try:
                await self.room.local_participant.publish_data(
                    json.dumps({"type": "vad", "value": vad_val}).encode(),
                    reliable=True,
                )
                log.info(f"VAD sent: {vad_val}")
            except Exception as e:
                log.error(f"VAD publish error: {e}")

    async def handle_stop(self, _msg: Dict):
        await self.close()

    async def run(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                ev = (msg.get("event") or "").lower()
                if ev == "start":
                    await self.handle_start(msg)
                elif ev == "media":
                    await self.handle_media(msg)
                elif ev == "stop":
                    await self.handle_stop(msg)
                elif ev == "dtmf":
                    await self.handle_dtmf(msg)
                elif ev == "vad":
                    await self.handle_vad(msg)
        except websockets.ConnectionClosed:
            log.info("WebSocket closed")
        finally:
            await self.close()

    async def close(self):
        if not self._closing.is_set():
            self._closing.set()
            await self.disconnect_livekit()
            log.info("LiveKit disconnected")

            if self.customer_audio or self.ai_audio:
                try:
                    customer = np.concatenate(self.customer_audio) if self.customer_audio else np.zeros(0, dtype=np.int16)
                    ai = np.concatenate(self.ai_audio) if self.ai_audio else np.zeros(0, dtype=np.int16)
                    max_len = max(len(customer), len(ai))
                    customer = np.pad(customer, (0, max_len - len(customer)))
                    ai = np.pad(ai, (0, max_len - len(ai)))
                    stereo = np.column_stack((customer, ai)).astype(np.int16)

                    filename = f"{self.app_id or 'unknown'}_{self.caller_id or 'unknown'}_{self.uuid}.wav"
                    with wave.open(filename, 'wb') as wf:
                        wf.setnchannels(2)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(stereo.tobytes())
                    log.info(f"Saved stereo audio: {filename}")

                    success, gcs_path = upload_audio_to_gcs(filename, filename)
                    if success:
                        log.info(f"Uploaded: {gcs_path}")
                    else:
                        log.error("Upload failed")
                except Exception as e:
                    log.error(f"Audio save/upload failed: {e}")

            try:
                await self.ws.close()
            except Exception:
                pass


# =========================
# WebSocket Server
# =========================
async def process_request(path, headers):
    if headers.get("Upgrade", "").lower() != "websocket":
        body = b"Use WebSocket\n"
        return (426, [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))], body)
    return None

async def handle_ws(ws: WebSocketServerProtocol, path: str):
    log.info(f"WS connected: {path}")
    parsed = urlparse(path)
    query_params = parse_qs(parsed.query)
    app_id = query_params.get("APPID", [None])[0]
    caller_id = query_params.get("CallerId", [None])[0]
    bridge = CallBridge(ws, app_id=app_id, caller_id=caller_id)
    try:
        await bridge.run()
    except Exception as e:
        log.error(f"WS handler error: {e}")
    finally:
        await bridge.close()

async def main():
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        raise RuntimeError("Missing LiveKit credentials")

    server = await websockets.serve(
        handle_ws, WS_HOST, WS_PORT,
        ping_interval=WS_PING_INTERVAL_S,
        ping_timeout=WS_PING_TIMEOUT_S,
        process_request=process_request,
        max_size=None,
        max_queue=None
    )
    log.info(f"Bridge listening on ws://{WS_HOST}:{WS_PORT}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    await stop.wait()
    server.close()
    await server.wait_closed()
    log.info("Server shutdown complete")


if __name__ == "__main__":
    flask_thread = threading.Thread(
        target=start_audio_player,
        name="Flask-Audio-Player",
        daemon=True
    )
    flask_thread.start()

    asyncio.run(main())