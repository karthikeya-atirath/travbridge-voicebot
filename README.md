# Travbridge-VoiceBot

Real-time AI-powered VoiceBot and TextBot for **Thomas Cook (TCIL)** and **SOTC** travel brands. Handles inbound PSTN calls and text-based agent-assist sessions, understands customer queries via speech-to-text, responds using an LLM-powered travel agent, and automatically creates CRM opportunities at the end of each session. Conversation data is persisted to **AWS DynamoDB** for durable, queryable storage.

---

## Table of Contents

- [Call Flow](#call-flow)
- [System Architecture](#system-architecture)
- [Tool Call Flow](#tool-call-flow)
- [Session End Processing](#session-end-processing)
- [Project Structure](#project-structure)
- [Technology Stack](#technology-stack)
- [Docker Containers](#docker-containers)
- [Services](#services)
- [Build & Deploy](#build--deploy)
- [Quick Reference](#quick-reference)

---

## Call Flow

End-to-end sequence from inbound PSTN call to CRM opportunity creation. The C-Zen bridge receives the call, creates a LiveKit room, and dispatches the appropriate agent based on the DID number. The agent handles real-time STT → LLM → TTS and logs every interaction to the conversation history.

```mermaid
sequenceDiagram
    autonumber
    participant Caller as Caller (PSTN)
    participant CZEN as czen-bridge
    participant LK as LiveKit Server
    participant Agent as Agent (STT/LLM/TTS)
    participant Deepgram as Deepgram STT
    participant Gemini as Gemini 2.5 Flash
    participant TTS as Google TTS
    participant Tools as Tool APIs
    participant CRM as CRM API
    participant Elastic as Elastic API
    participant Dynamo as DynamoDB

    Note over CZEN: telephone.py (WebSocket server)

    Caller->>CZEN: Inbound call via SOTC / ThomasCook DID
    CZEN->>CZEN: Identify DID → select agent from agent_mapping.json
    CZEN->>LK: Create room (name: appId_callerId_uuid)
    CZEN->>LK: Publish caller audio track (16kHz PCM)
    LK-->>Agent: Dispatch agent (tc-travel-bot / sotc-travel-bot)

    Note over Agent: agent_stt_llm_tts_v1.py

    Agent->>Agent: Extract customer_id from room name
    Agent->>Agent: Init ChatHistory + log to chat_logs/

    loop Real-time conversation
        LK-->>Agent: Caller audio frames
        Agent->>Deepgram: Stream audio (nova-2, Hindi)
        Deepgram-->>Agent: Transcript text
        Agent->>Agent: Log user message to ChatHistory
        Agent->>Gemini: Prompt + conversation context + tools
        alt LLM calls a tool
            Gemini-->>Agent: Function call (get_travel_package / get_fare_calendar / etc.)
            Agent->>Agent: Log function_call to ChatHistory
            Agent->>Tools: HTTP request to TravBridge / package APIs
            Tools-->>Agent: API response (packages / fares / BOGO)
            Agent->>Agent: Log function_call_output to ChatHistory
            Agent->>Gemini: Tool result → continue generation
        end
        Gemini-->>Agent: Response text
        Agent->>Agent: Log assistant message to ChatHistory
        Agent->>TTS: Synthesize speech (en-IN-Chirp3-HD-Kore)
        TTS-->>Agent: Audio frames
        Agent->>LK: Publish agent audio
        LK-->>CZEN: Agent audio
        CZEN-->>Caller: Play audio (8kHz resampled)
    end

    alt User goes silent (10s+)
        Agent->>Agent: Detect user_away state
        Agent->>TTS: Re-prompt message every 18s
        TTS-->>Agent: Audio
        Agent->>LK: Publish re-prompt audio
    end

    Caller->>CZEN: Hangup
    CZEN->>LK: Close room & tracks

    Note over Agent: Session End (on_close event)

    Agent->>Agent: Log "user_left" event
    Agent->>Gemini: Extract CRM fields from conversation (thinking_budget=0)
    Gemini-->>Agent: JSON (name, email, destination, package, summary)
    Agent->>CRM: Check if opportunity exists (mobile)
    alt Opportunity exists
        Agent->>CRM: Update opportunity
    else New customer
        Agent->>CRM: Create opportunity
    end
    CRM-->>Agent: Opportunity ID
    Agent->>Agent: Save conversation JSON to chat_logs/
    Agent->>Elastic: Push conversation data to Elastic API
    Agent->>Dynamo: Persist conversation to DynamoDB (always)
```

---

## System Architecture

The system runs four Docker containers — two voice bots and two text bots (TCIL and SOTC), each connecting to the same LiveKit server but handling different brands. Calls are routed to the correct agent based on the DID-to-agent mapping configured in `agent_mapping.json`.

```mermaid
graph TB
    subgraph "External"
        PSTN["📞 PSTN Caller"]
        CZEN_API["C-Zen Telephony API"]
        REACT["🖥️ React Frontend"]
    end

    subgraph "czen-bridge (telephone.py)"
        WS["WebSocket Server :8765"]
        BRIDGE["CallBridge"]
        RESAMPLE["Audio Resampler (8kHz ↔ 48kHz)"]
    end

    subgraph "LiveKit Server (:7880)"
        ROOMS["Room Manager"]
        DISPATCH["Agent Dispatcher"]
    end

    subgraph "Docker Containers"
        subgraph "voicebot-tcil (:8081)"
            AGENT_TC["agent_stt_llm_tts_v1.py"]
        end
        subgraph "voicebot-sotc (:8082)"
            AGENT_SOTC["sotc_agent_stt_llm_tts_v1.py"]
        end
        subgraph "textbot-tcil (:8090)"
            TEXT_TC["agent_text_v2.py"]
        end
        subgraph "textbot-sotc (:8091)"
            TEXT_SOTC["sotc_agent_text_v2.py"]
        end
    end

    subgraph "Shared Modules"
        TOOLS["tools.py / google_tools.py"]
        OPP["opportunity_create.py"]
        SEP["session_end_processor.py"]
        CH["chat_history.py"]
        DS["dynamo_saver.py"]
    end

    subgraph "External Services"
        DG["Deepgram STT"]
        GEMINI["Gemini 2.5 Flash (Vertex AI)"]
        GTTS["Google TTS"]
        TB["TravBridge Package API"]
        CRM["CRM API (Oracle)"]
        ELASTIC["Elastic Search API"]
        DYNAMO["AWS DynamoDB"]
    end

    PSTN --> CZEN_API --> WS --> BRIDGE
    BRIDGE --> RESAMPLE --> ROOMS
    REACT --> ROOMS
    ROOMS --> DISPATCH --> AGENT_TC & AGENT_SOTC & TEXT_TC & TEXT_SOTC

    AGENT_TC & AGENT_SOTC & TEXT_TC & TEXT_SOTC --> TOOLS --> TB
    AGENT_TC & AGENT_SOTC & TEXT_TC & TEXT_SOTC --> DG & GEMINI & GTTS
    AGENT_TC & AGENT_SOTC --> SEP --> OPP --> CRM
    CH --> ELASTIC
    CH --> DYNAMO
```

---

## Tool Call Flow

During a conversation, the Gemini LLM can invoke any of the four registered tools. Each tool logs its input arguments and output to the conversation history before returning results to the LLM for continued response generation.

| Tool | Description | API |
|------|-------------|-----|
| `get_travel_package` | Search packages by destination, theme, budget, days, departure city | TravBridge |
| `get_fare_calendar` | Fetch available dates and prices for a specific package | TravBridge |
| `get_all_bogo_packages` | List all active Buy-One-Get-One offers | TravBridge |
| `create_opportunity_tool` | Create or update a CRM lead with customer details | Oracle CRM |

```mermaid
flowchart LR
    subgraph "Gemini LLM decides to call a tool"
        A["LLM Response"] --> B{"Which tool?"}
    end

    B -->|"get_travel_package"| C["Search packages by:\n destination, theme,\n budget, days, hub"]
    B -->|"get_fare_calendar"| D["Fetch dates & prices\n for a specific package"]
    B -->|"get_all_bogo_packages"| E["List all BOGO\n (Buy One Get One)"]
    B -->|"create_opportunity_tool"| F["Create/update CRM\n opportunity with\n customer details"]

    C --> G["TravBridge API"]
    D --> G
    E --> G
    F --> H["Oracle CRM API"]

    G --> I["Response → ChatHistory\n → LLM continues"]
    H --> I
```

---

## Session End Processing

When a call ends, the `on_close` event fires and triggers `session_end_processor.py`. This module uses Gemini (with `thinking_budget=0` to avoid Vertex AI's thinking-mode issue) to extract structured CRM data from the full conversation — including tool call context. It then creates or updates an opportunity in the CRM, pushes to ElasticSearch, and **always** persists the full conversation to **DynamoDB** (regardless of CRM outcome) for durable storage.

```mermaid
flowchart TD
    A["🔴 Session Close Event"] --> B["Log 'user_left' event"]
    B --> C["Build conversation text\n (last 40 messages + tool calls)"]
    C --> D["Gemini 2.5 Flash extracts CRM fields\n (thinking_budget=0)"]
    D --> E{"Extraction\n successful?"}

    E -->|Yes| F["Extracted: name, email,\n destination, package, summary"]
    E -->|No| G["Use defaults\n (nulls + customer_id)"]

    F --> H{"Opportunity\n exists?"}
    G --> H

    H -->|"Yes (mobile match)"| I["Update existing opportunity"]
    H -->|"No"| J["Create new opportunity"]

    I --> K["Save opportunity_id to doc"]
    J --> K

    K --> L["Save JSON to chat_logs/"]
    L --> M["Push to Elastic API"]
    M --> O["Persist to DynamoDB\n (always, regardless of CRM outcome)"]
    O --> N["✅ Session complete"]
```

---

## Project Structure

```
Livekit-VoiceBot/
│
├── c-zen-bridge/                  # WebSocket bridge between telephony and LiveKit
│   ├── telephone.py               # Main bridge server (CallBridge, audio relay)
│   ├── agent_mapping.json         # DID → agent name routing
│   ├── audio_uploader.py          # Call audio upload utility
│   └── test.py                    # Bridge test script
│
├── livekit/                       # TCIL (Thomas Cook) agent source
│   ├── agent_stt_llm_tts_v1.py    # Voice bot entry point
│   ├── agent_text_v2.py           # Text/AgentAssist bot entry point
│   ├── aa_prompt.py               # AgentAssist prompt/instructions
│   ├── tools.py                   # Travel package search tools
│   ├── google_tools.py            # Google-based travel tools (destinations, flights, itineraries)
│   ├── opportunity_create.py      # CRM opportunity create/update
│   ├── session_end_processor.py   # Post-call LLM extraction + CRM
│   ├── chat_history.py            # Conversation logging
│   ├── chat_data_updater.py       # Elastic API integration
│   ├── dynamo_saver.py            # AWS DynamoDB conversation persistence
│   ├── prompts.py                 # System prompt / instructions
│   ├── constants.py               # Shared constants
│   ├── app_logger.py              # Logging config
│   ├── configuration/             # Environment config package
│   │   ├── __init__.py
│   │   └── config_env.py          # Env-var loader
│   ├── livekit.yaml               # LiveKit server config
│   ├── .env_dev                   # Dev environment variables
│   ├── .env_prod                  # Production environment variables
│   ├── asvamultiplayer-*.json     # GCP service account credentials
│   └── chat_logs/                 # Persisted conversation logs
│
├── livekit_sotc/                  # SOTC agent source (same structure, different brand)
│   ├── sotc_agent_stt_llm_tts_v1.py  # Voice bot entry point
│   ├── sotc_agent_text_v2.py         # Text/AgentAssist bot entry point
│   ├── sotc_aa_prompt.py             # AgentAssist prompt/instructions
│   ├── sotc_auto_dialer.py           # Outbound auto-dialer
│   ├── convert_xlsx_to_json.py       # Excel-to-JSON converter for call lists
│   ├── tools.py                   # Travel package search tools
│   ├── google_tools.py            # Google-based travel tools
│   ├── opportunity_create.py      # CRM opportunity create/update
│   ├── session_end_processor.py   # Post-call LLM extraction + CRM
│   ├── chat_history.py            # Conversation logging
│   ├── chat_data_updater.py       # Elastic API integration
│   ├── dynamo_saver.py            # AWS DynamoDB conversation persistence
│   ├── prompts.py                 # System prompt / instructions
│   ├── constants.py               # Shared constants
│   ├── app_logger.py              # Logging config
│   ├── configuration/             # Environment config package
│   │   ├── __init__.py
│   │   └── config_env.py          # Env-var loader
│   ├── livekit.yaml               # LiveKit server config
│   ├── .env_dev                   # Dev environment variables
│   ├── .env_prod                  # Production environment variables
│   ├── asvamultiplayer-*.json     # GCP service account credentials
│   └── chat_logs/                 # Persisted conversation logs
│
├── agent-starter-react/           # React frontend (PM2-managed)
│
├── Dockerfile.livekit             # Docker image for TCIL agents (voice + text)
├── Dockerfile.livekit_sotc        # Docker image for SOTC agents (voice + text)
├── requirements.txt               # Python dependencies (pip freeze)
├── .dockerignore                  # Files excluded from Docker build context
└── .gitignore
```

---

## Technology Stack

| Component | Technology | Details |
|-----------|-----------|---------|
| **Speech-to-Text** | Deepgram / Sarvam | `nova-2` model (Hindi); Sarvam `saaras:v3` (codemix) |
| **LLM** | Google Gemini 2.5 Flash | Via Vertex AI (`asia-south1`) |
| **Text-to-Speech** | Google Cloud TTS | `en-IN-Chirp3-HD-Kore` voice |
| **Voice Activity Detection** | Silero VAD | CPU-based, 16kHz sample rate |
| **Turn Detection** | LiveKit Multilingual Model | ONNX model for end-of-turn detection |
| **Media Server** | LiveKit | WebRTC rooms, agent dispatch |
| **Telephony Bridge** | C-Zen + WebSocket | PCM audio relay (8kHz ↔ 48kHz) |
| **Package Search API** | TravBridge | ElasticSearch-backed REST API |
| **CRM** | Oracle Service Cloud | Opportunity create/update/lookup |
| **Conversation Storage** | ElasticSearch | Via TravBridge save API |
| **Conversation Storage** | AWS DynamoDB | Tables: `voice_bot_tc` (TCIL), `voice_bot_sotc` (SOTC) |
| **Runtime** | Python 3.12 | Docker containers |

---

## Docker Containers

The system runs **4 Docker containers** from **2 images**. Each image contains both voice and text bot code; the container's CMD determines which bot runs.

### Container Overview

| Container | Image | Entry Point | Port | Agent Name |
|-----------|-------|-------------|------|------------|
| `voicebot-tcil` | `voicebot-tcil:latest` | `agent_stt_llm_tts_v1.py` | 8081 | `tc-travel-bot` |
| `textbot-tcil` | `voicebot-tcil:latest` | `agent_text_v2.py` | 8090 | `tc-text-travel-bot-v2` |
| `voicebot-sotc` | `voicebot-sotc:latest` | `sotc_agent_stt_llm_tts_v1.py` | 8082 | `sotc-travel-bot` |
| `textbot-sotc` | `voicebot-sotc:latest` | `sotc_agent_text_v2.py` | 8091 | `sotc-text-travel-bot-v2` |

### Docker Images → Dockerfiles

| Dockerfile | Image | Source Directory |
|------------|-------|-----------------|
| `Dockerfile.livekit` | `voicebot-tcil` | `livekit/` |
| `Dockerfile.livekit_sotc` | `voicebot-sotc` | `livekit_sotc/` |

### What's injected at runtime (NOT baked into image)

| Item | How | Why |
|------|-----|-----|
| `.env_prod` | `--env-file` flag on `docker run` | Secrets stay outside the image |
| GCP credentials JSON | Volume mount (read-only) | Service account key |
| `chat_logs/` | Bind mount | Logs persist after container restart |
| AWS credentials | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` in `.env_prod` | DynamoDB auth |
| DynamoDB table name | `DYNAMODB_TABLE_NAME` in `.env_prod` | `voice_bot_tc` (TCIL) / `voice_bot_sotc` (SOTC) |

---

## Services

The infrastructure services run as systemd. The agent bots run as Docker containers.

### Systemd Services

| Service | Config | Runs | Port |
|---------|--------|------|------|
| `livekit.service` | `/etc/systemd/system/livekit.service` | `livekit-server` (livekit.yaml) | 7880 |
| `czen-bridge.service` | `/etc/systemd/system/czen-bridge.service` | `telephone.py` | 8765 |

### Systemd Commands

| Action | LiveKit Server | C-Zen Bridge |
|--------|---------------|--------------|
| **Status** | `sudo systemctl status livekit.service` | `sudo systemctl status czen-bridge.service` |
| **Start** | `sudo systemctl start livekit.service` | `sudo systemctl start czen-bridge.service` |
| **Stop** | `sudo systemctl stop livekit.service` | `sudo systemctl stop czen-bridge.service` |
| **Restart** | `sudo systemctl restart livekit.service` | `sudo systemctl restart czen-bridge.service` |
| **Logs** | `sudo journalctl -u livekit.service -f` | `sudo journalctl -u czen-bridge.service -f` |

---

## Build & Deploy

All commands run from `/home/gcp-admin/Livekit-VoiceBot/`.

### 1. Build Images

```bash
# Build TCIL image (used by voicebot-tcil & textbot-tcil)
sudo docker build -f Dockerfile.livekit -t voicebot-tcil:latest .

# Build SOTC image (used by voicebot-sotc & textbot-sotc)
sudo docker build -f Dockerfile.livekit_sotc -t voicebot-sotc:latest .
```

### 2. Stop Old Containers

```bash
sudo docker stop voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc
sudo docker rm voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc
```

### 3. Start All 4 Containers

```bash
# ── TCIL Voice Bot ──
sudo docker run -d --name voicebot-tcil \
  --network host \
  --env-file ./livekit/.env_prod \
  -v ./livekit/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro \
  -v ./livekit/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-tcil:latest

# ── TCIL Text Bot ──
sudo docker run -d --name textbot-tcil \
  --network host \
  --env-file ./livekit/.env_prod \
  -v ./livekit/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro \
  -v ./livekit/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-tcil:latest \
  python agent_text_v2.py start

# ── SOTC Voice Bot ──
sudo docker run -d --name voicebot-sotc \
  --network host \
  --env-file ./livekit_sotc/.env_prod \
  -v ./livekit_sotc/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro \
  -v ./livekit_sotc/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-sotc:latest

# ── SOTC Text Bot ──
sudo docker run -d --name textbot-sotc \
  --network host \
  --env-file ./livekit_sotc/.env_prod \
  -v ./livekit_sotc/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro \
  -v ./livekit_sotc/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-sotc:latest \
  python sotc_agent_text_v2.py start
```

### 4. Verify

```bash
sudo docker ps
sudo docker logs voicebot-tcil --tail 10
sudo docker logs voicebot-sotc --tail 10
sudo docker logs textbot-tcil --tail 10
sudo docker logs textbot-sotc --tail 10
```

---

## Quick Reference

All commands run from `/home/gcp-admin/Livekit-VoiceBot/`.

### Container Management

| Action | Command |
|--------|---------|
| **Check all containers** | `sudo docker ps` |
| **Stop all bots** | `sudo docker stop voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc` |
| **Start all bots** | `sudo docker start voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc` |
| **Restart all bots** | `sudo docker restart voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc` |
| **View TCIL voice logs** | `sudo docker logs -f voicebot-tcil` |
| **View TCIL text logs** | `sudo docker logs -f textbot-tcil` |
| **View SOTC voice logs** | `sudo docker logs -f voicebot-sotc` |
| **View SOTC text logs** | `sudo docker logs -f textbot-sotc` |

### Image Management

| Action | Command |
|--------|---------|
| **Build TCIL image** | `sudo docker build -f Dockerfile.livekit -t voicebot-tcil:latest .` |
| **Build SOTC image** | `sudo docker build -f Dockerfile.livekit_sotc -t voicebot-sotc:latest .` |
| **List all images** | `sudo docker images \| grep voicebot` |
| **Delete a version** | `sudo docker rmi voicebot-tcil:v1.0 voicebot-sotc:v1.0` |
| **Clean unused images** | `sudo docker image prune -a` |
| **Check disk usage** | `sudo docker system df` |
| **Full cleanup** | `sudo docker system prune -a` |

### Full Rebuild & Redeploy

```bash
# 1. Build both images
sudo docker build -f Dockerfile.livekit -t voicebot-tcil:latest .
sudo docker build -f Dockerfile.livekit_sotc -t voicebot-sotc:latest .

# 2. Stop and remove old containers
sudo docker stop voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc
sudo docker rm voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc

# 3. Start all 4 containers (see "Start All 4 Containers" section above)
```

### React Frontend (PM2)

The React frontend (`agent-starter-react`) is managed via PM2.

```bash
# View status
pm2 list
pm2 describe react-app

# Restart the app
pm2 restart react-app

# Stop the app
pm2 stop react-app

# Start the app (if stopped)
pm2 start react-app

# View live logs
pm2 logs react-app

# Monitor CPU/Memory
pm2 monit
```