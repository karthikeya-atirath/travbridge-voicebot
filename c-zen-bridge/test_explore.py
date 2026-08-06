import sys
sys.path.append('.')
from telephone import flask_app, exploration_data

with flask_app.test_request_context(
    '/api/tcil/exploration',
    method='POST',
    json={"chat_started_from":"","chat_started_to":"","chat_channel":"VoiceBot","only_tool_conversations":False}
):
    try:
        response = exploration_data('tcil')
        print(response.get_data(as_text=True)[:2000])
    except Exception as e:
        print(f"Error: {e}")
