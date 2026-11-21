import json
import os
import requests

current_dir = os.path.dirname(os.path.abspath(__file__))
candidates = [
    os.path.join(current_dir, ".env"),
    os.path.join(current_dir, "..", ".env"),
    os.path.join(current_dir, "..", "..", ".env")
]

for env_path in candidates:
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8-sig") as f:
            for line in f:
                clean = line.strip()
                if clean and "=" in clean and not clean.startswith("#"):
                    k, v = clean.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
        break

api_key = os.environ.get("GROQ_API_KEY") or ""

response = requests.post(
    url="https://api.groq.com/openai/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    },
    json={
        "model": "qwen/qwen3.8-27b",
        "messages": [
            {
                "role": "system",
                "content": "Zwracaj odpowiedz wylacznie jako poprawny obiekt JSON."
            },
            {
                "role": "user",
                "content": "Odpowiedz po polsku w formacie JSON z kluczem 'odpowiedz': jaki jest sens zycia?"
            }
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0
    },
    timeout=30
)

response.raise_for_status()
data = response.json()
print(json.dumps(data, indent=2, ensure_ascii=False))