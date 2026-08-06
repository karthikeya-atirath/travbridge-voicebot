#!/bin/bash
cd /home/voicebot/Travbridge-VoiceBot

# ── TCIL Voice Bot ──
sudo docker run -d --name voicebot-tcil \
  --network host \
  --env-file ./livekit/.env_prod \
  -v ./livekit/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-tcil:latest

# ── TCIL Text Bot ──
sudo docker run -d --name textbot-tcil \
  --network host \
  --env-file ./livekit/.env_prod \
  -v ./livekit/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-tcil:latest \
  python agent_text_v2.py start

# ── SOTC Voice Bot ──
sudo docker run -d --name voicebot-sotc \
  --network host \
  --env-file ./livekit_sotc/.env_prod \
  -v ./livekit_sotc/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-sotc:latest

# ── SOTC Text Bot ──
sudo docker run -d --name textbot-sotc \
  --network host \
  --env-file ./livekit_sotc/.env_prod \
  -v ./livekit_sotc/chat_logs:/app/chat_logs \
  --restart unless-stopped \
  voicebot-sotc:latest \
  python sotc_agent_text_v2.py start
