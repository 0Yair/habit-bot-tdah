"""
HABIT TRACKER BOT — Versión sin conflictos de dependencias
Usa requests + supabase + anthropic directamente
"""

import os
import json
import time
import threading
import requests
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

# ── Estado en memoria ─────────────────────────────────────────────────────────
session = {
    "pending": [],
    "current": None,
    "results": {},
    "block": None,
    "waiting": False,
}

# ══════════════════════════════════════════════════════════════════════════════
# SUPABASE — REST directo con requests
# ══════════════════════════════════════════════════════════════════════════════

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def sb_get(table, params=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=sb_headers())
    return r.json()

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=sb_headers(), json=data)
    return r.json()

def sb_patch(table, params, data):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=sb_headers(), json=data)
    return r.json()

def get_habits(block=None):
    params = "select=*"
    if block:
        params += f"&block=eq.{block}"
    return sb_get("habits", params)

def get_all_state():
    return sb_get("daily_summary", "select=*")

def log_habit(habit_key, done, week_level, note=None):
    sb_post("habit_logs", {
        "habit_key": habit_key,
        "done": done,
        "week_level": week_level,
        "note": note,
        "logged_at": datetime.now().isoformat(),
    })
    # Actualizar racha
    state_list = sb_get("user_state", f"habit_key=eq.{habit_key}&select=*")
    if not state_list:
        return
    state = state_list[0]
    today = date.today()
    last = date.fromisoformat(state["last_logged"]) if state.get("last_logged") else None
    if done:
        new_streak = (state["streak"] + 1) if last and (today - last).days == 1 else 1
    else:
        new_streak = 0
    sb_patch("user_state", f"habit_key=eq.{habit_key}", {
        "streak": new_streak,
        "best_streak": max(new_streak, state.get("best_streak", 0)),
        "last_logged": today.isoformat(),
        "updated_at": datetime.now().isoformat(),
    })

def get_week_label(habit):
    levels = habit.get("week_levels", [])
    week = habit.get("current_week", 1)
    active = levels[0] if levels else {"label": "?", "desc": ""}
    for lvl in levels:
        if week >= lvl.get("week", 1):
            active = lvl
    return f'{active.get("label","?")} — {active.get("desc","")}'

def advance_week_if_needed(habit_key):
    state_list = sb_get("user_state", f"habit_key=eq.{habit_key}&select=*")
    if not state_list:
        return None
    state = state_list[0]
    if state.get("streak", 0) > 0 and state["streak"] % 7 == 0:
        new_week = min(state.get("current_week", 1) + 1, 6)
        sb_patch("user_state", f"habit_key=eq.{habit_key}", {"current_week": new_week})
        return new_week
    return None

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM — REST directo con requests
# ══════════════════════════════════════════════════════════════════════════════

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
        "parse_mode": "Markdown"
    })

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
    return r.json().get("result", [])

# ══════════════════════════════════════════════════════════════════════════════
# IA — Claude Haiku
# ══════════════════════════════════════════════════════════════════════════════

def ai_call(prompt, max_tokens=200):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    return r.json()["content"][0]["text"]

def build_context(habits_state):
    return "\n".join([
        f"- {h.get('emoji','')} {h.get('name','')}: racha={h.get('streak',0)}d, "
        f"semana_2pct={h.get('current_week',1)}, hecho_hoy={h.get('done_today',False)}"
        for h in habits_state
    ])

def ai_checkin_message(habit, all_state):
    context = build_context(all_state)
    week_label = get_week_label(habit)
    return ai_call(f"""Eres Hábit, coach de hábitos para TDAH. Español mexicano, cálido y directo.
Estado de Yair (24 años, auditor EY):
{context}

Hábito a preguntar: {habit.get('emoji','')} {habit.get('name','')}
Nivel 2% hoy: {week_label}
Racha actual: {habit.get('streak',0)} días

Escribe UN mensaje corto (máx 3 líneas) preguntando si completó este hábito.
Si racha 7+: celebra con energía. Si 0-2: hazlo fácil. Si 3-6: menciona que construye algo real.
Incluye el nivel exacto ({week_label}). Máximo 1 emoji.""")

def ai_reaction(habit, done, all_state):
    context = build_context(all_state)
    return ai_call(f"""Eres Hábit, coach TDAH. Español mexicano, directo.
El usuario {'COMPLETÓ ✅' if done else 'NO completó ❌'}: {habit.get('emoji','')} {habit.get('name','')}
Racha: {habit.get('streak',0)} días
{context}
Reacción muy corta (1-2 líneas). Específica, no genérica. Máximo 1 emoji.""", 100)

def ai_daily_summary(all_state, results):
    done_count = sum(1 for v in results.values() if v)
    total = len(results)
    context = build_context(all_state)
    return ai_call(f"""Eres Hábit, coach TDAH. Español mexicano, honesto y motivador.
Yair completó {done_count} de {total} hábitos hoy.
Completados: {[k for k,v in results.items() if v]}
No completados: {[k for k,v in results.items() if not v]}
{context}
Resumen del día (máx 5 líneas): número destacado, observación de patrones, una cosa concreta para mañana, cierre motivador genuino.""", 300)

def ai_answer_question(question, all_state):
    context = build_context(all_state)
    return ai_call(f"""Eres Hábit, coach de hábitos TDAH. Español mexicano, cálido y basado en evidencia.
Estado de Yair:
{context}
Pregunta: {question}
Responde útil, específico y breve (máx 5 líneas).""", 400)

# ══════════════════════════════════════════════════════════════════════════════
# FLUJO DEL CHECK-IN
# ══════════════════════════════════════════════════════════════════════════════

def start_checkin(block):
    habits = get_habits(block)
    if not habits:
        return
    session["block"] = block
    session["pending"] = list(habits)
    session["results"] = {}
    session["waiting"] = False
    label = "mañana" if block == "morning" else "noche"
    emoji = "🌤" if block == "morning" else "🌙"
    send_message(f"{emoji} *Check-in de {label}*\nVamos con tus {len(habits)} hábitos:")
    time.sleep(1)
    ask_next_habit()

def ask_next_habit():
    if not session["pending"]:
        finish_checkin()
        return
    habit = session["pending"][0]
    session["current"] = habit
    all_state = get_all_state()
    msg = ai_checkin_message(habit, all_state)

    # Comida tiene 3 opciones, el resto 2
    if habit["key"] == "comida":
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Sí", "callback_data": f"done_{habit['key']}"},
                {"text": "〰️ Más o menos", "callback_data": f"partial_{habit['key']}"},
                {"text": "❌ No", "callback_data": f"skip_{habit['key']}"}
            ]]
        }
    else:
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Sí", "callback_data": f"done_{habit['key']}"},
                {"text": "❌ No", "callback_data": f"skip_{habit['key']}"}
            ]]
        }
    send_message(msg, keyboard)
    session["waiting"] = True

def finish_checkin():
    all_state = get_all_state()
    summary = ai_daily_summary(all_state, session["results"])
    done = sum(1 for v in session["results"].values() if v)
    total = len(session["results"])
    filled = int((done / total) * 10) if total > 0 else 0
    bar = "█" * filled + "░" * (10 - filled)
    send_message(f"📊 *Resumen del día*\n`{bar}` {done}/{total}\n\n{summary}\n\n_¿Tienes alguna pregunta? Escríbeme._")
    session["pending"] = []
    session["current"] = None
    session["waiting"] = False

# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — Recordatorios automáticos
# ══════════════════════════════════════════════════════════════════════════════

def scheduler_loop():
    while True:
        now = datetime.now()
        h, m = now.hour, now.minute
        # Check-in mañana: 9:00
        if h == 9 and m == 0:
            start_checkin("morning")
            time.sleep(61)
        # Recordatorio mañana: 10:30
        elif h == 10 and m == 30:
            all_state = get_all_state()
            pending = [s for s in all_state if s.get("block") == "morning" and not s.get("done_today")]
            if pending:
                send_message(f"⏰ Aún tienes {len(pending)} hábitos matutinos pendientes. Escribe /checkin cuando puedas.")
            time.sleep(61)
        # Check-in noche: 21:30
        elif h == 21 and m == 30:
            start_checkin("night")
            time.sleep(61)
        else:
            time.sleep(30)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP — Long polling
# ══════════════════════════════════════════════════════════════════════════════

def handle_callback(update):
    cb = update["callback_query"]
    data = cb["data"]
    callback_id = cb["id"]
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    original_text = cb["message"].get("text", "")

    answer_callback(callback_id)

    if not data.startswith(("done_", "skip_", "partial_")):
        return

    if data.startswith("done_"):
        action, habit_key = "done", data[5:]
        done = True
        note = "sí"
    elif data.startswith("partial_"):
        action, habit_key = "partial", data[8:]
        done = True
        note = "más o menos"
    else:
        action, habit_key = "skip", data[5:]
        done = False
        note = "no"

    all_state = get_all_state()
    habit = next((h for h in all_state if h.get("key") == habit_key), None)
    if not habit:
        return

    week_level = habit.get("current_week", 1)
    log_habit(habit_key, done, week_level, note)
    new_week = advance_week_if_needed(habit_key)

    session["results"][habit_key] = done
    session["pending"] = [h for h in session["pending"] if h.get("key") != habit_key]

    all_state = get_all_state()
    reaction = ai_reaction(habit, done, all_state)

    level_up = ""
    if new_week:
        level_up = f"\n\n🆙 *¡Subiste al nivel {new_week}!* La versión 2% más difícil empieza mañana."

    icon = "✅" if done else "❌"
    edit_message(chat_id, message_id, f"{original_text}\n\n{icon} {reaction}{level_up}")

    time.sleep(1.5)
    ask_next_habit()

# ══════════════════════════════════════════════════════════════════════════════
# PERSONAS — Agenda inteligente de contactos
# ══════════════════════════════════════════════════════════════════════════════

def save_person(raw_input):
    extracted = ai_call(f"""Extrae información de esta nota sobre una persona.
Responde SOLO con JSON válido, sin explicación ni markdown.
Formato exacto:
{{"name": "Nombre Apellido", "birthday": "YYYY-MM-DD o null", "interests": ["interés1", "interés2"], "notes": "resumen de todo lo demás"}}
Texto: {raw_input}""", 300)
    try:
        clean = extracted.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        data["raw_input"] = raw_input
        data["updated_at"] = datetime.now().isoformat()
        existing = sb_get("people_notes", f"name_search=eq.{data['name'].lower()}&select=id")
        if existing:
            sb_patch("people_notes", f"id=eq.{existing[0]['id']}", data)
            return f"✅ Actualicé la info de *{data['name']}*."
        else:
            sb_post("people_notes", data)
            return f"✅ Guardé a *{data['name']}* en tu agenda."
    except Exception as e:
        print(f"Error guardando persona: {e}", flush=True)
        return "❌ No pude procesar esa info. Intenta de nuevo."

def get_person(name):
    results = sb_get("people_notes", f"name_search=like.*{name.lower()}*&select=*")
    return results[0] if results else None

def ai_person_summary(person):
    return ai_call(f"""Eres Hábit, asistente personal de Yair. Español mexicano, cálido y directo.
Resume lo que Yair sabe de esta persona de forma útil y natural:
Nombre: {person.get('name')}
Cumpleaños: {person.get('birthday') or 'desconocido'}
Intereses: {', '.join(person.get('interests') or []) or 'no registrados'}
Notas: {person.get('notes') or 'sin notas'}
Sé conversacional. Si hay cumpleaños próximo (dentro de 30 días), menciónalo. Máximo 5 líneas.""", 300)

def ai_person_suggestions(person, context=""):
    return ai_call(f"""Eres Hábit, coach personal de Yair. Español mexicano, práctico y creativo.
Da 3-5 sugerencias concretas para conectar con {person.get('name')}.
Puede ser: qué regalarle, qué tema tocar, qué hacer juntos.
Intereses: {', '.join(person.get('interests') or []) or 'no registrados'}
Cumpleaños: {person.get('birthday') or 'desconocido'}
Notas: {person.get('notes') or 'sin notas'}
{f'Contexto: {context}' if context else ''}
Sé específico y creativo. Máximo 6 líneas.""", 400)

def handle_persona_command(text):
    parts = text.strip().split(" ", 2)
    if len(parts) < 2:
        return ("📋 *Comandos de personas:*\n\n"
                "`/persona add Angélica — le gustan los perros, cumpleaños 30 oct`\n"
                "`/persona info Angélica`\n"
                "`/persona suggest Angélica`\n"
                "`/persona list`")
    subcmd = parts[1].lower()
    if subcmd == "add":
        if len(parts) < 3:
            return "Escribe así: `/persona add Nombre — lo que sabes de ella`"
        return save_person(parts[2])
    elif subcmd == "info":
        if len(parts) < 3:
            return "Escribe así: `/persona info Nombre`"
        person = get_person(parts[2])
        if not person:
            return "No encontré a nadie con ese nombre. ¿Ya lo guardaste con `/persona add`?"
        summary = ai_person_summary(person)
        bday_str = ""
        if person.get("birthday"):
            try:
                bd = date.fromisoformat(person["birthday"])
                today = date.today()
                next_bd = bd.replace(year=today.year)
                if next_bd < today:
                    next_bd = bd.replace(year=today.year + 1)
                days_left = (next_bd - today).days
                if days_left <= 30:
                    bday_str = f"\n\n🎂 *Su cumpleaños es en {days_left} días* ({bd.strftime('%d de %B')})"
            except:
                pass
        return f"👤 *{person['name']}*\n\n{summary}{bday_str}"
    elif subcmd == "suggest":
        if len(parts) < 3:
            return "Escribe así: `/persona suggest Nombre`"
        name_ctx = parts[2].split("—", 1)
        name = name_ctx[0].strip()
        ctx = name_ctx[1].strip() if len(name_ctx) > 1 else ""
        person = get_person(name)
        if not person:
            return f"No encontré a *{name}*. ¿Ya lo guardaste con `/persona add`?"
        suggestions = ai_person_suggestions(person, ctx)
        return f"💡 *Sugerencias para {person['name']}:*\n\n{suggestions}"
    elif subcmd == "list":
        people = sb_get("people_notes", "select=name,birthday&order=name")
        if not people:
            return "Aún no tienes personas guardadas. Usa `/persona add` para agregar."
        lines = ["📋 *Tu agenda de personas:*\n"]
        for p in people:
            bday = f" 🎂 {p['birthday']}" if p.get('birthday') else ""
            lines.append(f"• {p['name']}{bday}")
        return "\n".join(lines)
    else:
        return "Subcomando no reconocido. Escribe `/persona` para ver opciones."

def handle_message(update):
    msg = update.get("message", {})
    text = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if chat_id != str(CHAT_ID):
        return

    if text == "/start":
        send_message(
            "👋 Hola Yair, soy *Hábit* — tu coach de hábitos con TDAH.\n\n"
            "Comandos:\n"
            "/checkin — check-in manual\n"
            "/resumen — progreso de hoy\n"
            "/racha — ver tus rachas\n\n"
            "O escríbeme cualquier pregunta sobre tus hábitos."
        )
    elif text == "/checkin":
        hour = datetime.now().hour
        block = "morning" if hour < 15 else "night"
        start_checkin(block)
    elif text == "/resumen":
        all_state = get_all_state()
        lines = ["📊 *Tu progreso de hoy*\n"]
        for h in all_state:
            icon = "✅" if h.get("done_today") else "⬜"
            lines.append(f"{icon} {h.get('emoji','')} {h.get('name','')} — racha: {h.get('streak',0)}d")
        done = sum(1 for h in all_state if h.get("done_today"))
        total = len(all_state)
        filled = int((done / total) * 10) if total > 0 else 0
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"\n`{bar}` {done}/{total} completados")
        send_message("\n".join(lines))
    elif text == "/racha":
        all_state = get_all_state()
        lines = ["🔥 *Rachas y niveles 2%*\n"]
        for h in all_state:
            fire = "🔥" if h.get("streak", 0) >= 5 else "  "
            lines.append(
                f"{fire} {h.get('emoji','')} {h.get('name','')}\n"
                f"   Racha: {h.get('streak',0)}d | Mejor: {h.get('best_streak',0)}d | Nivel: {h.get('current_week',1)}/6"
            )
        send_message("\n".join(lines))
    elif text.startswith("/persona"):
        answer = handle_persona_command(text)
        send_message(answer)
    elif not text.startswith("/"):
        all_state = get_all_state()
        answer = ai_answer_question(text, all_state)
        send_message(answer)

def main():
    print("🤖 Hábit bot iniciando...")
    # Scheduler en hilo separado
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()

    offset = None
    print("✅ Bot corriendo. Esperando mensajes...")

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    handle_callback(update)
                elif "message" in update:
                    handle_message(update)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
