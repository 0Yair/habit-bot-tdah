"""
SHARED — Config, Supabase, Telegram, IA
Importado por todos los módulos.
"""
import os, json, requests
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_KEY= os.environ["ANTHROPIC_API_KEY"]

# Supabase — fallback a las credenciales del tracker si el .env no las tiene
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://gdosrvuhsnwpcdikzrck.supabase.co")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imdkb3NydnVoc253cGNkaWt6cmNrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ0NTEzMDEsImV4cCI6MjA5MDAyNzMwMX0.iUh_Q6H7ubDgfG7cVEdBq24eFmWkGS8zsuPYp1wMC-g"
)

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
print(f"[Config] Supabase URL: {SUPABASE_URL}", flush=True)

# Estado en memoria compartido entre módulos
session = {
    "pending": [],
    "current": None,
    "results": {},
    "block": None,
    "waiting": False,
}

# ── Supabase ──────────────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def sb_get(table, params=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=sb_headers())
    return r.json()

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=sb_headers(), json=data)
    print(f"[sb_post] {table} → HTTP {r.status_code} | {r.text[:200]}", flush=True)
    return r.json()

def sb_patch(table, params, data):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=sb_headers(), json=data)
    return r.json()

def get_all_state():
    return sb_get("daily_summary", "select=*")

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_message(text, reply_markup=None):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(f"{BASE_URL}/sendMessage", json=payload)
    return r.json()

def answer_callback(callback_id):
    requests.post(f"{BASE_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})

def edit_message(chat_id, message_id, text):
    requests.post(f"{BASE_URL}/editMessageText", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
    })

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
    return r.json().get("result", [])

# ── IA ────────────────────────────────────────────────────────────────────────
def ai_call(prompt, max_tokens=150):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return r.json()["content"][0]["text"]
