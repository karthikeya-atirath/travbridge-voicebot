import os
from google import genai
from google.genai.types import HttpOptions

client = genai.Client(
    vertexai=False,
    api_key="DummyAPIKey",
    http_options=HttpOptions(base_url='http://10.160.0.6:8000')
)

try:
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents='Tell me a joke.'
    )
    print("SUCCESS:", response.text)
except Exception as e:
    print("ERROR:", e)
