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
import audioop
import queue as thread_queue
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

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
    TrackSource,
    TrackPublishOptions,
)

from audio_uploader import upload_audio_to_gcs

# Flask + Auth
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from google.cloud import storage
from functools import wraps

# =========================
# Config & Logging
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
LIVEKIT_SAMPLE_RATE = 48000
FRAME_SAMPLES = int(LIVEKIT_SAMPLE_RATE * FRAME_MS / 1000)

APM_FRAME_MS = 10
APM_SAMPLES = int(LIVEKIT_SAMPLE_RATE * APM_FRAME_MS / 1000)

ENABLE_NOISE_SUPPRESSION = os.getenv("ENABLE_NOISE_SUPPRESSION", "true").lower() == "true"
ENABLE_HIGH_PASS = os.getenv("ENABLE_HIGH_PASS", "true").lower() == "true"
ENABLE_AGC = os.getenv("ENABLE_AGC", "false").lower() == "true"
ENABLE_AEC = os.getenv("ENABLE_AEC", "false").lower() == "true"

# =========================
# Bridge Settings (persisted in bridge_settings.json, configurable from UI)
# =========================
BRIDGE_SETTINGS_FILE = "bridge_settings.json"

VALID_SAMPLE_RATES = [0, 8000, 16000, 24000, 48000]

# Target sample rates — set by UI.
# BRIDGE_INBOUND_SR:  rate to resample inbound audio to before sending to LiveKit.
#                     0 = pass through (no upsample).
# BRIDGE_OUTBOUND_SR: rate to resample outbound audio to before sending to Twilio.
#                     0 = pass through (no downsample, send raw 48k).
BRIDGE_INBOUND_SR  = 48000  # inbound: Twilio → LiveKit
BRIDGE_OUTBOUND_SR = 8000   # outbound: LiveKit → Twilio (0 = no downsample)

def load_bridge_settings():
    """Load bridge_settings.json and apply to global sample-rate settings."""
    global BRIDGE_INBOUND_SR, BRIDGE_OUTBOUND_SR
    try:
        if os.path.exists(BRIDGE_SETTINGS_FILE):
            with open(BRIDGE_SETTINGS_FILE, 'r') as f:
                s = json.load(f)
            BRIDGE_INBOUND_SR  = int(s.get("inbound_sr",  48000))
            BRIDGE_OUTBOUND_SR = int(s.get("outbound_sr",  8000))
            log.info("Bridge settings loaded: inbound_sr=%s outbound_sr=%s",
                     BRIDGE_INBOUND_SR, BRIDGE_OUTBOUND_SR)
    except Exception as e:
        log.warning("Failed to load bridge_settings.json: %s", e)

def save_bridge_settings():
    """Persist current sample-rate settings to bridge_settings.json."""
    try:
        with open(BRIDGE_SETTINGS_FILE, 'w') as f:
            json.dump({
                "inbound_sr":  BRIDGE_INBOUND_SR,
                "outbound_sr": BRIDGE_OUTBOUND_SR,
            }, f, indent=2)
    except Exception as e:
        log.error("Failed to save bridge_settings.json: %s", e)

load_bridge_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("bridge")


# =========================
# Agent Mapping
# =========================
AGENT_MAPPING = {}
AGENT_MAPPING_FILE = "agent_mapping.json"

def load_agent_mapping():
    global AGENT_MAPPING
    try:
        if os.path.exists(AGENT_MAPPING_FILE):
            with open(AGENT_MAPPING_FILE, 'r') as f:
                raw = json.load(f)
            # Normalize: legacy format is {app_id: agent_name (str)}
            # New format is {app_id: {"agent_name": ..., "company": ...}}
            normalized = {}
            for app_id, val in raw.items():
                if isinstance(val, dict):
                    normalized[app_id] = val
                else:
                    normalized[app_id] = {"agent_name": str(val), "company": ""}
            AGENT_MAPPING = normalized
            log.info(f"Loaded {len(AGENT_MAPPING)} agent mappings")
        else:
            log.warning(f"Agent mapping file not found: {AGENT_MAPPING_FILE}")
            AGENT_MAPPING = {}
    except Exception as e:
        log.error(f"Failed to load agent mapping: {e}")
        AGENT_MAPPING = {}

load_agent_mapping()


# =========================
# Flask App with Auth
# =========================
flask_app = Flask(__name__, template_folder='templates')
flask_app.secret_key = os.urandom(24)

LOGIN_FILE = "login.json"

def load_users():
    try:
        if os.path.exists(LOGIN_FILE):
            with open(LOGIN_FILE, 'r') as f:
                data = json.load(f)
                return {u["username"]: u["password"] for u in data.get("users", [])}
        return {}
    except Exception as e:
        log.error(f"Failed to load login.json: {e}")
        return {}

USERS = load_users()

# =========================
# Active Call Bridge Registry
# =========================
active_bridges: Dict[str, 'CallBridge'] = {}
active_bridges_lock = threading.Lock()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "logged_in" not in session:
            flash("Please log in to access this page", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def derive_role(username):
    """Derive user role from email domain.
    @atirath -> admin (sees everything + mappings)
    @thomascook -> tcil (sees only TCIL data)
    @sotc -> sotc (sees only SOTC data)
    """
    u = (username or '').lower()
    if '@atirath' in u:
        return 'admin'
    elif '@thomascook' in u:
        return 'tcil'
    elif '@sotc' in u:
        return 'sotc'
    return 'admin'  # fallback


@flask_app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            session['role'] = derive_role(username)
            flash("Login successful!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password", "error")
    return render_template('login.html')


@flask_app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    flash("Logged out successfully", "success")
    return redirect(url_for("login"))


@flask_app.route('/', methods=['GET'])
@login_required
def index():
    role = session.get('role', 'admin')
    return render_template('index.html', role=role)


@flask_app.route('/admin/mappings', methods=['GET', 'POST'])
@login_required
def manage_mappings():
    # Only admin (@atirath) users can access mappings
    if session.get('role') not in ('admin', None):
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for("index"))
    global AGENT_MAPPING
    message = None

    if request.method == 'POST':
        action     = request.form.get('action')
        app_id     = request.form.get('app_id', '').strip()
        agent_name = request.form.get('agent_name', '').strip()
        company    = request.form.get('company', '').strip()
        prompt     = request.form.get('prompt', '').strip()
        opp_mode   = request.form.get('opp_mode', 'all').strip()
        opp_tools  = request.form.getlist('opp_tools')  # multi-select
        old_app_id = request.form.get('old_app_id', '').strip()

        opp_tool_calls = 'all' if opp_mode == 'all' else opp_tools

        inbound_sr_form  = int(request.form.get('inbound_sr',  BRIDGE_INBOUND_SR)  or BRIDGE_INBOUND_SR)
        outbound_sr_form = int(request.form.get('outbound_sr', BRIDGE_OUTBOUND_SR) or BRIDGE_OUTBOUND_SR)

        def _build_entry():
            entry = {"agent_name": agent_name, "company": company}
            if prompt:
                entry["prompt"] = prompt
            entry["opp_tool_calls"] = opp_tool_calls
            entry["inbound_sr"]  = inbound_sr_form
            entry["outbound_sr"] = outbound_sr_form
            return entry

        if action in ('add', 'update') and app_id and agent_name:
            AGENT_MAPPING[app_id] = _build_entry()
            message = f"Mapping saved: {app_id} → {agent_name}"

        elif action == 'edit' and old_app_id and app_id and agent_name:
            # Remove old key if App ID changed
            if old_app_id != app_id and old_app_id in AGENT_MAPPING:
                del AGENT_MAPPING[old_app_id]
            AGENT_MAPPING[app_id] = _build_entry()
            message = f"Mapping updated: {app_id} → {agent_name}"

        elif action == 'delete' and app_id in AGENT_MAPPING:
            del AGENT_MAPPING[app_id]
            message = f"Mapping deleted: {app_id}"

        try:
            with open(AGENT_MAPPING_FILE, 'w') as f:
                json.dump(AGENT_MAPPING, f, indent=2)
            log.info("Agent mappings saved")
            load_agent_mapping()
        except Exception as e:
            log.error(f"Failed to save mappings: {e}")
            message = f"Error saving: {e}"

    return render_template(
        'admin_mappings.html',
        mappings=AGENT_MAPPING,
        message=message,
        inbound_sr=BRIDGE_INBOUND_SR,
        outbound_sr=BRIDGE_OUTBOUND_SR,
        valid_rates=VALID_SAMPLE_RATES,
    )


# ─── Agent config API (no login — internal use by agents) ────────────────────

@flask_app.route('/api/agent_config/<app_id>')
def api_agent_config(app_id):
    """Return prompt and opp_tool_calls for an agent mapping. Called by agents at call start."""
    entry = AGENT_MAPPING.get(app_id, {})
    if isinstance(entry, str):
        entry = {"agent_name": entry}
    return jsonify({
        "prompt": entry.get("prompt", ""),
        "opp_tool_calls": entry.get("opp_tool_calls", "all"),
    })


# ─── Bridge audio settings API ───────────────────────────────────────────────

@flask_app.route('/api/bridge-settings', methods=['GET', 'POST'])
@login_required
def api_bridge_settings():
    """Get or update the inbound/outbound sample-rate settings live."""
    global BRIDGE_INBOUND_SR, BRIDGE_OUTBOUND_SR
    if request.method == 'POST':
        data = request.get_json(force=True)
        inbound  = int(data.get('inbound_sr',  BRIDGE_INBOUND_SR))
        outbound = int(data.get('outbound_sr', BRIDGE_OUTBOUND_SR))
        if inbound not in VALID_SAMPLE_RATES or outbound not in VALID_SAMPLE_RATES:
            return jsonify({'error': f'Invalid rate. Valid options: {VALID_SAMPLE_RATES}'}), 400
        BRIDGE_INBOUND_SR  = inbound
        BRIDGE_OUTBOUND_SR = outbound
        save_bridge_settings()
        log.info("Bridge settings updated via UI: inbound_sr=%s outbound_sr=%s", inbound, outbound)
    return jsonify({
        'inbound_sr':  BRIDGE_INBOUND_SR,
        'outbound_sr': BRIDGE_OUTBOUND_SR,
        'valid_rates': VALID_SAMPLE_RATES,
    })


# ─── External API proxy endpoints (avoids CORS on browser) ───────────────────

TCIL_BASE = "https://travbridge.atirath.com/v1"
SOTC_BASE = "https://travbridge.atirath.com/sotc"


def _ext_post(url, body):
    """POST to external API, return JSON or raise."""
    import urllib.request as _ur
    data = json.dumps(body).encode()
    req = _ur.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with _ur.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


@flask_app.route('/api/tcil/conversations', methods=['POST'])
@login_required
def proxy_tcil_conversations():
    body = request.get_json(force=True)
    try:
        data = _ext_post(f"{TCIL_BASE}/get_all_conversations", body)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@flask_app.route('/api/sotc/conversations', methods=['POST'])
@login_required
def proxy_sotc_conversations():
    body = request.get_json(force=True)
    try:
        data = _ext_post(f"{SOTC_BASE}/SOTC_get_all_conversations", body)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@flask_app.route('/api/tcil/conversation', methods=['POST'])
@login_required
def proxy_tcil_conversation():
    body = request.get_json(force=True)
    try:
        data = _ext_post(f"{TCIL_BASE}/get_conversation", body)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@flask_app.route('/api/sotc/conversation', methods=['POST'])
@login_required
def proxy_sotc_conversation():
    body = request.get_json(force=True)
    try:
        data = _ext_post(f"{SOTC_BASE}/SOTC_get_conversation", body)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@flask_app.route('/api/audio_url/<conversation_id>')
@login_required
def audio_signed_url(conversation_id):
    """Return a short-lived signed GCS URL for conversationId_final.wav."""
    try:
        storage_client = storage.Client.from_service_account_json(
            os.environ.get("GCS_KEY_FILE", "file.json")
        )
        bucket = storage_client.bucket("voice_bot_travbridge")
        blob = bucket.blob(f"{conversation_id}_final.wav")
        url = blob.generate_signed_url(version="v4", expiration=timedelta(hours=1), method="GET")
        return jsonify({"url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@flask_app.route('/api/<company>/daily_counts', methods=['POST'])
@login_required
def daily_counts(company):
    """Aggregate daily conversation counts server-side (pages through all results)."""
    body = request.get_json(force=True)
    api_url = (f"{TCIL_BASE}/get_all_conversations" if company == 'tcil'
               else f"{SOTC_BASE}/SOTC_get_all_conversations")
    daily = {}
    page = 1
    PAGE_SIZE = 200
    try:
        while True:
            payload = {**body, "page": page, "count": PAGE_SIZE}
            data = _ext_post(api_url, payload)
            convs = data.get("conversations", [])
            for c in convs:
                dt = (c.get("chat_modified") or "")[:10]
                if dt:
                    daily[dt] = daily.get(dt, 0) + 1
            total = data.get("total", 0)
            if page * PAGE_SIZE >= total or not convs:
                break
            page += 1
        return jsonify({"daily": daily, "total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@flask_app.route('/api/<company>/exploration', methods=['POST'])
@login_required
def exploration_data(company):
    """Build sequential funnel: All → func1 → func2 → … → Opp Created, with conv IDs per stage."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    body = request.get_json(force=True)
    list_url = (f"{TCIL_BASE}/get_all_conversations" if company == 'tcil'
                else f"{SOTC_BASE}/SOTC_get_all_conversations")
    detail_url = (f"{TCIL_BASE}/get_conversation" if company == 'tcil'
                  else f"{SOTC_BASE}/SOTC_get_conversation")

    # Step 1: page through list API
    all_convs = []
    page = 1
    try:
        while True:
            payload = {**body, "page": page, "count": 200}
            data = _ext_post(list_url, payload)
            convs = data.get("conversations", [])
            all_convs.extend(convs)
            if page * 200 >= data.get("total", 0) or not convs:
                break
            page += 1
    except Exception as e:
        return jsonify({"error": f"List fetch failed: {e}"}), 502

    # Build conv_info lookup
    conv_info = {}
    for c in all_convs:
        conv_info[c["conversationId"]] = {
            "customerId": c.get("customerId", ""),
            "opportunity_id": c.get("opportunity_id", ""),
            "chat_modified": c.get("chat_modified", ""),
        }

    all_ids = [c["conversationId"] for c in all_convs]
    opp_ids = [c["conversationId"] for c in all_convs if c.get("opportunity_id")]

    # Step 2: fetch details (max 200) to get ordered function sequences
    sample = all_convs[:200]
    conv_funcs = {}   # conv_id → ordered list of function names
    func_positions = {}  # func_name → list of positions (for ordering)

    def _fetch_detail(conv):
        try:
            d = _ext_post(detail_url, {"conversationId": conv["conversationId"]})
            msgs = d.get("conversation", {}).get("conversation", [])
            ordered = []
            seen = set()
            for m in msgs:
                if m.get("role") == "function_call" and m.get("name"):
                    fn = m["name"]
                    if fn not in seen:
                        ordered.append(fn)
                        seen.add(fn)
            return conv["conversationId"], ordered
        except Exception:
            return conv["conversationId"], []

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(_fetch_detail, c) for c in sample]
        for f in as_completed(futures):
            cid, ordered = f.result()
            conv_funcs[cid] = ordered
            for pos, fn in enumerate(ordered):
                func_positions.setdefault(fn, []).append(pos)

    # Order functions by median position
    func_order = sorted(func_positions.keys(),
                        key=lambda fn: sorted(func_positions[fn])[len(func_positions[fn])//2])

    # Build GA4-style path steps (no practical limit, full visualization)
    max_steps = 100
    max_nodes = 12
    steps = []

    # Step 0: Starting Point
    steps.append({"label": "Starting Point", "nodes": [
        {"name": "session_start", "count": len(sample), "ids": list(conv_funcs.keys())}
    ]})

    # Steps 1..N: function at position i
    for si in range(max_steps):
        step_nodes = {}
        for cid, funcs in conv_funcs.items():
            if si < len(funcs):
                fn = funcs[si]
                step_nodes.setdefault(fn, []).append(cid)
        if not step_nodes:
            break
        sorted_nodes = sorted(step_nodes.items(), key=lambda x: -len(x[1]))
        nodes = [{"name": n, "count": len(ids), "ids": ids} for n, ids in sorted_nodes[:max_nodes]]
        if len(sorted_nodes) > max_nodes:
            more_ids = [cid for _, ids in sorted_nodes[max_nodes:] for cid in ids]
            nodes.append({"name": f"+{len(sorted_nodes)-max_nodes} More", "count": len(more_ids), "ids": more_ids})
        steps.append({"label": f"Step +{si+1}", "nodes": nodes})

    # Build flows between consecutive steps
    flows = []
    for si in range(len(steps) - 1):
        for fi, fn in enumerate(steps[si]["nodes"]):
            from_ids = set(fn["ids"])
            for ti, tn in enumerate(steps[si+1]["nodes"]):
                overlap_ids = list(from_ids & set(tn["ids"]))
                if overlap_ids:
                    flows.append({"fs": si, "fi": fi, "ts": si+1, "ti": ti, "c": len(overlap_ids), "ids": overlap_ids})

    # Add Final Outcome Step
    outcome_si = len(steps)
    lead_ids = [cid for cid in conv_funcs.keys() if cid in opp_ids]
    dropped_ids = [cid for cid in conv_funcs.keys() if cid not in opp_ids]
    
    outcome_nodes = []
    if lead_ids:
        outcome_nodes.append({"name": "Lead Created", "count": len(lead_ids), "ids": lead_ids})
    if dropped_ids:
        outcome_nodes.append({"name": "Dropped", "count": len(dropped_ids), "ids": dropped_ids})
        
    if outcome_nodes:
        steps.append({"label": "Outcome", "nodes": outcome_nodes})
        
        outcome_flows = {}
        for cid in conv_funcs.keys():
            last_si = 0
            last_fi = 0
            for s in range(outcome_si - 1, -1, -1):
                found = False
                for f_idx, f_node in enumerate(steps[s]["nodes"]):
                    if cid in f_node["ids"]:
                        last_si = s
                        last_fi = f_idx
                        found = True
                        break
                if found:
                    break
            
            out_ti = -1
            for t_idx, t_node in enumerate(outcome_nodes):
                if cid in t_node["ids"]:
                    out_ti = t_idx
                    break
                    
            if out_ti != -1:
                key = (last_si, last_fi, outcome_si, out_ti)
                if key not in outcome_flows:
                    outcome_flows[key] = []
                outcome_flows[key].append(cid)
                
        for (fs, fi, ts, ti), cids in outcome_flows.items():
            flows.append({"fs": fs, "fi": fi, "ts": ts, "ti": ti, "c": len(cids), "ids": cids})

    return jsonify({
        "steps": steps,
        "flows": flows,
        "conversations": conv_info,
        "sampled": len(sample),
        "total": len(all_convs),
    })


@flask_app.route('/sessions')
@login_required
def api_sessions():
    with active_bridges_lock:
        result = []
        for sid, b in active_bridges.items():
            # Look up company from agent mapping
            company = ''
            entry = AGENT_MAPPING.get(b.app_id)
            if isinstance(entry, dict):
                company = entry.get('company', '')
            result.append({
                'session_id': sid,
                'app_id': b.app_id or '—',
                'caller_id': b.caller_id or '—',
                'stream_sid': b.stream_sid or '—',
                'frames_sent': b.frames_sent_to_livekit,
                'connected': b.room is not None,
                'started': b.call_started.strftime('%Y-%m-%d %H:%M:%S UTC'),
                'company': company.upper(),
            })
    return jsonify(result)


@flask_app.route('/audio_stream/<session_id>')
@login_required
def audio_stream(session_id):
    with active_bridges_lock:
        bridge = active_bridges.get(session_id)
    if not bridge:
        return Response('Session not found', status=404)

    listener_id = uuid.uuid4().hex
    q: thread_queue.Queue = thread_queue.Queue(maxsize=300)
    bridge.add_audio_listener(listener_id, q)

    def generate():
        yield f'retry: 1000\ndata: {{"type":"init","sampleRate":{bridge.tw_sr}}}\n\n'
        try:
            while True:
                try:
                    item = q.get(timeout=10.0)
                    if item is None:  # sentinel: call ended
                        yield 'data: {"type":"ended"}\n\n'
                        break
                    role, chunk = item
                    b64 = base64.b64encode(chunk).decode()
                    r = 'c' if role == 'customer' else 'a'
                    yield f'data: {{"r":"{r}","d":"{b64}"}}\n\n'
                except thread_queue.Empty:
                    yield ': keepalive\n\n'
        except GeneratorExit:
            pass
        finally:
            bridge.remove_audio_listener(listener_id)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def start_audio_player():
    print("Starting Audio Player UI → http://0.0.0.0:8000")
    print("Login: http://0.0.0.0:8000/login")
    print("Admin: http://0.0.0.0:8000/admin/mappings")
    flask_app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False, threaded=True)


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
        self.session_id = uuid.uuid4().hex
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.tw_sr = 8000                   # will be updated from mediaFormat
        self.tw_encoding = "audio/x-mulaw"  # Twilio default encoding
        # Per-call resample rates loaded from app_id mapping on handle_start
        self.inbound_sr  = BRIDGE_INBOUND_SR   # Twilio → LiveKit target (0 = pass-through)
        self.outbound_sr = BRIDGE_OUTBOUND_SR  # LiveKit → Twilio target
        self.lk_in_sr    = BRIDGE_INBOUND_SR   # actual rate pushed into AudioSource
        self.room: Optional[rtc.Room] = None
        self.audio_source: Optional[AudioSource] = None
        self.audio_track: Optional[LocalAudioTrack] = None
        self._tasks = set()
        self._closing = asyncio.Event()
        self._pub_lock = asyncio.Lock()
        self.frames_sent_to_livekit = 0
        self.last_debug_time = time.time()
        self.call_started = datetime.utcnow()
        # Live audio streaming to dashboard listeners (thread-safe queues)
        self.live_audio_queues: Dict[str, thread_queue.Queue] = {}
        self._audio_queues_lock = threading.Lock()
        # Register in global registry
        with active_bridges_lock:
            active_bridges[self.session_id] = self

        # For recording
        # Raw original rate version (for _final.wav)
        self.customer_audio_raw = []   # exactly as received from Twilio
        self.ai_audio_raw = []         # downsampled from LiveKit to tw_sr

        self.apm = None
        self._apm_buf = np.zeros(0, dtype=np.int16)
        self._in_buf = np.zeros(0, dtype=np.int16)
        if ENABLE_NOISE_SUPPRESSION or ENABLE_HIGH_PASS or ENABLE_AGC or ENABLE_AEC:
            try:
                self.apm = rtc.AudioProcessingModule(
                    echo_cancellation=ENABLE_AEC,
                    noise_suppression=ENABLE_NOISE_SUPPRESSION,
                    high_pass_filter=ENABLE_HIGH_PASS,
                    auto_gain_control=ENABLE_AGC,
                )
                log.info(
                    "WebRTC APM enabled: NS=%s HPF=%s AGC=%s AEC=%s",
                    ENABLE_NOISE_SUPPRESSION, ENABLE_HIGH_PASS, ENABLE_AGC, ENABLE_AEC
                )
            except Exception as e:
                log.warning("WebRTC APM unavailable: %s", e)

    # ------------------------------------------------------------------
    # Live audio listener management (for dashboard SSE streaming)
    # ------------------------------------------------------------------
    def add_audio_listener(self, lid: str, q: thread_queue.Queue):
        with self._audio_queues_lock:
            self.live_audio_queues[lid] = q

    def remove_audio_listener(self, lid: str):
        with self._audio_queues_lock:
            self.live_audio_queues.pop(lid, None)

    def _push_audio_to_listeners(self, role: str, pcm: np.ndarray):
        """Push audio to all dashboard SSE listeners (already at tw_sr)."""
        if pcm.size == 0:
            return
        with self._audio_queues_lock:
            if not self.live_audio_queues:
                return
            chunk_bytes = pcm.tobytes()
            for q in list(self.live_audio_queues.values()):
                try:
                    q.put_nowait((role, chunk_bytes))
                except thread_queue.Full:
                    pass

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
                sample_rate=LIVEKIT_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=APM_SAMPLES,
            )
            try:
                self.apm.process_stream(af)
                processed = np.frombuffer(af.data, dtype=np.int16)
                out_chunks.append(processed.copy())
            except Exception as e:
                log.warning("APM process_stream error, bypassing: %s", e)
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

        agent_entry = AGENT_MAPPING.get(self.app_id)
        if agent_entry:
            if isinstance(agent_entry, dict):
                agent_name = agent_entry.get('agent_name', str(agent_entry))
                if 'inbound_sr' in agent_entry:
                    self.inbound_sr = int(agent_entry['inbound_sr'])
                    self.lk_in_sr = self.inbound_sr if self.inbound_sr > 0 else self.tw_sr
                if 'outbound_sr' in agent_entry:
                    self.outbound_sr = int(agent_entry['outbound_sr'])
            else:
                agent_name = str(agent_entry)

            log.info(f"Dispatching to agent '{agent_name}' for app_id={self.app_id} (inbound_sr={self.inbound_sr}, lk_in_sr={self.lk_in_sr})")
            token_builder.with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=agent_name)]
                )
            )
        elif "default" in AGENT_MAPPING:
            default_entry = AGENT_MAPPING['default']
            if isinstance(default_entry, dict):
                default_name = default_entry.get('agent_name', str(default_entry))
                if 'inbound_sr' in default_entry:
                    self.inbound_sr = int(default_entry['inbound_sr'])
                    self.lk_in_sr = self.inbound_sr if self.inbound_sr > 0 else self.tw_sr
                if 'outbound_sr' in default_entry:
                    self.outbound_sr = int(default_entry['outbound_sr'])
            else:
                default_name = str(default_entry)
                
            log.info(f"Using default agent: {default_name} (inbound_sr={self.inbound_sr}, lk_in_sr={self.lk_in_sr})")
            token_builder.with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=default_name)]
                )
            )
        else:
            log.info("No specific agent mapping → using default worker")

        token = token_builder.to_jwt()

        self.room = rtc.Room()
        try:
            await self.room.connect(LIVEKIT_URL, token, options=RoomOptions(auto_subscribe=True))
            log.info("LiveKit connected to room=%s", self.room.name)
        except Exception as e:
            log.error(f"Failed to connect to LiveKit: {e}")
            raise

        self.audio_source = AudioSource(sample_rate=self.tw_sr, num_channels=1)
        self.audio_track = LocalAudioTrack.create_audio_track(TRACK_NAME_PUB, self.audio_source)

        options = TrackPublishOptions(source=TrackSource.SOURCE_MICROPHONE)
        try:
            await self.room.local_participant.publish_track(self.audio_track, options)
            log.info("Published source as track '%s'", TRACK_NAME_PUB)
        except Exception as e:
            log.error(f"Failed to publish track: {e}")
            await self.disconnect_livekit()
            raise

        @self.room.on("track_subscribed")
        def _on_sub(track: AudioTrack, pub, participant):
            if not is_audio_kind(getattr(track, "kind", None)):
                return
            log.info("Subscribed remote audio from %s", participant.identity)
            t = asyncio.create_task(self.relay_lk_to_ws(track))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        for p in self.room.remote_participants.values():
            for pub in p.track_publications.values():
                if is_audio_kind(pub.kind):
                    await pub.set_subscribed(True)

    async def disconnect_livekit(self):
        for t in list(self._tasks):
            try:
                t.cancel()
                await t
            except Exception:
                pass
        if self.room:
            try:
                await self.room.disconnect()
                log.info("Disconnected from LiveKit room")
            except Exception as e:
                log.error(f"Error during room disconnection: {e}")
            self.room = None
        self.audio_source = None
        self.audio_track = None

    async def relay_lk_to_ws(self, track: AudioTrack):
        """Receive audio from LiveKit agent → downsample to tw_sr → chunk into 20ms → send to Twilio."""
        stream = AudioStream(track)
        buf = np.zeros(0, dtype=np.int16)
        frame_size = int(self.tw_sr * FRAME_MS / 1000)  # 160 samples at 8kHz for 20ms
        try:
            async for event in stream:
                af = getattr(event, "frame", event)
                frame_sr = getattr(af, 'sample_rate', LIVEKIT_SAMPLE_RATE)
                pcm = decode_livekit_frame(af.data, af.num_channels)
                if pcm.size == 0:
                    continue

                # Downsample to tw_sr (8kHz) for Twilio
                if frame_sr != self.tw_sr:
                    pcm_out = linear_resample_int16(pcm, frame_sr, self.tw_sr)
                else:
                    pcm_out = pcm

                buf = np.concatenate([buf, pcm_out]) if buf.size else pcm_out

                # Send in 20ms chunks (what Twilio/C-Zen expects)
                while buf.size >= frame_size:
                    chunk = buf[:frame_size]
                    buf = buf[frame_size:]

                    self.ai_audio_raw.append(chunk.copy())
                    self._push_audio_to_listeners('ai', chunk)

                    payload_b64 = base64.b64encode(chunk.tobytes()).decode("ascii")
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
        mf = start.get("mediaFormat", {})
        self.tw_sr = int(mf.get("sampleRate") or 8000)
        self.tw_encoding = mf.get("encoding", "audio/x-mulaw").lower()

        log.info("START streamSid=%s  tw_sr=%dHz  encoding=%s",
                 self.stream_sid, self.tw_sr, self.tw_encoding)
        try:
            await self.connect_livekit()
        except Exception as e:
            log.error(f"Failed to start LiveKit: {e}")
            await self.close()

    async def handle_media(self, msg: Dict):
        media = msg.get("media", {})
        if media.get("track") != "inbound":
            return

        payload_b64 = media.get("payload")
        if not payload_b64:
            return

        raw = base64.b64decode(payload_b64)

        # Decode based on encoding
        if "mulaw" in self.tw_encoding or "pcmu" in self.tw_encoding:
            pcm_bytes = audioop.ulaw2lin(raw, 2)
        else:
            pcm_bytes = raw
        pcm_raw = np.frombuffer(pcm_bytes, dtype=np.int16)

        # Save raw customer audio
        self.customer_audio_raw.append(pcm_raw.copy())

        vu = rms_dbfs(pcm_raw)
        log.info("VU in (WS→LK) %.1f dBFS @ %dHz", vu, self.tw_sr)

        # Stream to live dashboard
        self._push_audio_to_listeners('customer', pcm_raw)

        # Push raw decoded PCM directly into LiveKit — no resampling
        if self.audio_source and pcm_raw.size:
            af = AudioFrame(
                data=pcm_raw.tobytes(),
                sample_rate=self.tw_sr,
                num_channels=1,
                samples_per_channel=len(pcm_raw),
            )
            maybe = self.audio_source.capture_frame(af)
            if asyncio.iscoroutine(maybe):
                await maybe
            self.frames_sent_to_livekit += 1

            if self.frames_sent_to_livekit <= 10:
                log.info(f"Sent frame {self.frames_sent_to_livekit} to LiveKit")

            if self.frames_sent_to_livekit % 100 == 0:
                log.info(f"Sent {self.frames_sent_to_livekit} frames total to LiveKit")

        else:
            log.debug("audio_source not ready yet, dropping frame (LiveKit still connecting)")

        current_time = time.time()
        if current_time - self.last_debug_time > 5.0:
            log.info(f"Publisher stats: sent {self.frames_sent_to_livekit} frames to LiveKit")
            self.last_debug_time = current_time

    async def handle_dtmf(self, msg: Dict):
        digit = msg.get("dtmf", {}).get("digit")
        log.info("DTMF received: %s", digit)
        if self.room:
            try:
                await self.room.local_participant.publish_data(
                    json.dumps({"type": "dtmf", "digit": digit}).encode(),
                    reliable=True,
                )
            except Exception as e:
                log.error(f"Error publishing DTMF: {e}")

    async def handle_vad(self, msg: Dict):
        vad_val = msg.get("vad", {}).get("value")
        log.info("VAD event: %s", vad_val)
        if self.room:
            try:
                await self.room.local_participant.publish_data(
                    json.dumps({"type": "vad", "value": vad_val}).encode(),
                    reliable=True,
                )
            except Exception as e:
                log.error(f"Error publishing VAD: {e}")

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
            log.info("WS closed")
        finally:
            await self.close()

    async def close(self):
        if not self._closing.is_set():
            self._closing.set()
            # Deregister from global registry
            with active_bridges_lock:
                active_bridges.pop(self.session_id, None)
            # Signal all SSE listeners that the stream has ended
            with self._audio_queues_lock:
                for q in list(self.live_audio_queues.values()):
                    try:
                        q.put_nowait(None)  # None = sentinel: call ended
                    except thread_queue.Full:
                        pass
            try:
                await self.disconnect_livekit()
                log.info("Cleanly closed LiveKit connection")
            except Exception as e:
                log.error(f"Error during final cleanup: {e}")

            base_filename = f"{self.app_id or 'unknown'}_{self.caller_id or 'unknown'}_{self.uuid}"

            # Save raw original-rate stereo recording (_final.wav)
            if self.customer_audio_raw or self.ai_audio_raw:
                try:
                    customer_raw = np.concatenate(self.customer_audio_raw) if self.customer_audio_raw else np.zeros(0, dtype=np.int16)
                    ai_raw = np.concatenate(self.ai_audio_raw) if self.ai_audio_raw else np.zeros(0, dtype=np.int16)
                    max_len_raw = max(len(customer_raw), len(ai_raw))
                    customer_raw = np.pad(customer_raw, (0, max_len_raw - len(customer_raw)))
                    ai_raw = np.pad(ai_raw, (0, max_len_raw - len(ai_raw)))
                    stereo_raw = np.column_stack((customer_raw, ai_raw)).astype(np.int16)

                    final_filename = f"{base_filename}_final.wav"
                    with wave.open(final_filename, 'wb') as wf:
                        wf.setnchannels(2)
                        wf.setsampwidth(2)
                        wf.setframerate(self.tw_sr)
                        wf.writeframes(stereo_raw.tobytes())
                    log.info(f"Saved raw original-rate stereo audio: {final_filename} @ {self.tw_sr}Hz")

                    success, gcs_path = upload_audio_to_gcs(final_filename, final_filename)
                    if success:
                        log.info(f"Uploaded to GCS: {gcs_path}")
                    else:
                        log.error("GCS upload failed for raw final file")
                except Exception as e:
                    log.error(f"Raw final audio save/upload failed: {e}")

            try:
                await self.ws.close()
                log.info("Cleanly closed WebSocket connection")
            except Exception as e:
                log.error(f"Error closing WebSocket: {e}")


# =========================
# WebSocket server
# =========================
async def process_request(path, headers):
    if headers.get("Upgrade", "").lower() != "websocket":
        body = b"Use WebSocket\n"
        return (426, [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))], body)
    return None


async def handle_ws(ws: WebSocketServerProtocol, path: str):
    log.info("WS connected: path=%s", path)
    parsed = urlparse(path)
    query_params = parse_qs(parsed.query)
    app_id = query_params.get("APPID", [None])[0]
    caller_id = query_params.get("CallerId", [None])[0]
    bridge = CallBridge(ws, app_id=app_id, caller_id=caller_id)
    try:
        await bridge.run()
    except Exception as e:
        log.error(f"Error in WebSocket handler: {e}")
    finally:
        await bridge.close()


async def main():
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise RuntimeError("Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET")

    server = await websockets.serve(
        handle_ws, WS_HOST, WS_PORT,
        ping_interval=WS_PING_INTERVAL_S,
        ping_timeout=WS_PING_TIMEOUT_S,
        process_request=process_request,
        max_size=None,
        max_queue=None
    )
    log.info("✅ Listening on ws://%s:%d", WS_HOST, WS_PORT)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    except Exception as e:
        log.error(f"Error in main loop: {e}")
    finally:
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