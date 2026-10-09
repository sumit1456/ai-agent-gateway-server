import requests

API_KEY = "gw_vfTUvJo08plY-Gmaz5PcR0x71HII7ad-b-gGDJTKIBs"
BASE_URL = "http://localhost:8000"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Test: List agents
try:
    r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers, timeout=10)
    print("GET /v1/agents/", r.status_code)
    print(r.text)
except Exception as e:
    print("ERR", e)
