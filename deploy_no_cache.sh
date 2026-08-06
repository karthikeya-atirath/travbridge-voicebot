#!/bin/bash
set -e

echo "Building voicebot-tcil with --no-cache..."
docker build --no-cache -t voicebot-tcil:latest -f Dockerfile.livekit .

echo "Building voicebot-sotc with --no-cache..."
docker build --no-cache -t voicebot-sotc:latest -f Dockerfile.livekit_sotc .

echo "Stopping containers..."
docker stop voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc || true
docker rm -f voicebot-tcil textbot-tcil voicebot-sotc textbot-sotc || true

echo "Starting containers..."
docker run -d --name voicebot-tcil --network host --env-file ./livekit/.env_prod -v ./livekit/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro -v ./livekit/chat_logs:/app/chat_logs --restart unless-stopped voicebot-tcil:latest

docker run -d --name textbot-tcil --network host --env-file ./livekit/.env_prod -v ./livekit/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro -v ./livekit/chat_logs:/app/chat_logs --restart unless-stopped voicebot-tcil:latest python agent_text_v2.py start

docker run -d --name voicebot-sotc --network host --env-file ./livekit_sotc/.env_prod -v ./livekit_sotc/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro -v ./livekit_sotc/chat_logs:/app/chat_logs --restart unless-stopped voicebot-sotc:latest

docker run -d --name textbot-sotc --network host --env-file ./livekit_sotc/.env_prod -v ./livekit_sotc/asvamultiplayer-0c4c83832cfc.json:/app/asvamultiplayer-0c4c83832cfc.json:ro -v ./livekit_sotc/chat_logs:/app/chat_logs --restart unless-stopped voicebot-sotc:latest python sotc_agent_text_v2.py start

echo "Deploy complete!"
