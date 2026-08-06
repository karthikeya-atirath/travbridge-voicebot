import os
from google import genai
from google.genai.types import HttpOptions
from google.oauth2.credentials import Credentials

client = genai.Client(
    vertexai=True,
    project="livekit123",
    location="asia-south1",
    credentials=Credentials(token="dummy-token"), 
    http_options=HttpOptions(base_url='http://10.160.0.5:8004')
)

try:
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents='Tell me a joke.'
    )
    print("SUCCESS:", response.text)
except Exception as e:
    print("ERROR:", e)
