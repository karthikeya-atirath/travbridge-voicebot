import argparse
import os

from dotenv import load_dotenv
from livekit.api import AccessToken, VideoGrants, RoomConfiguration, RoomAgentDispatch

load_dotenv(".env_dev")

parser = argparse.ArgumentParser(description="Generate a LiveKit access token for testing")
parser.add_argument("--room", default="test-room", help="Room name to join")
parser.add_argument("--identity", default="playground-user", help="Participant identity")
args = parser.parse_args()

agent_name = os.environ.get("AGENT_NAME", "")

token_builder = (
    AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    .with_identity(args.identity)
    .with_name(args.identity)
    .with_grants(
        VideoGrants(
            room_join=True,
            room=args.room,
            can_publish=True,
            can_subscribe=True,
        )
    )
)

# The worker only picks up jobs matching its own dispatch mode: if
# AGENT_NAME is set (explicit dispatch, e.g. mirroring .env_prod), a token
# with no RoomAgentDispatch never gets an agent, worker running or not.
if agent_name:
    token_builder = token_builder.with_room_config(
        RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name)])
    )

token = token_builder.to_jwt()

print("LIVEKIT_URL:", os.environ["LIVEKIT_URL"])
print("ROOM:", args.room)
print("IDENTITY:", args.identity)
print("TOKEN:", token)
