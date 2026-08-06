import os

files = [
    "livekit/agent_text_v2.py",
    "livekit/google_tools.py",
    "livekit/session_end_processor.py",
    "livekit/agent_stt_llm_tts_v1.py",
    "livekit_sotc/sotc_agent_stt_llm_tts_v1.py",
    "livekit_sotc/session_end_processor.py",
    "livekit_sotc/sotc_agent_text_v2.py",
    "livekit_sotc/google_tools.py"
]

for file in files:
    if os.path.exists(file):
        with open(file, "r") as f:
            lines = f.readlines()
        with open(file, "w") as f:
            for line in lines:
                if "thinking_config" not in line:
                    f.write(line)
        print(f"Processed {file}")
