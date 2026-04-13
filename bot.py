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
from http.server import HTTPServer, BaseHTTPRequestHandler

WEBHOOK_SECRET = "habitbot_webhook_2024_yair"

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

def send_menu():
    """Manda el menú principal con botones rápidos."""
    hour = datetime.now().hour
    if hour < 12:
        greeting = "☀️ *Buenos días, Yair*"
    elif hour < 19:
        greeting = "🌤 *Buenas tardes, Yair*"
    else:
        greeting = "🌙 *Buenas noches, Yair*"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📋 Check-in hábitos", "callback_data": "menu_checkin"},
                {"text": "👤 Agregar persona", "callback_data": "menu_add_person"},
            ],
            [
                {"text": "🔍 Consultar persona", "callback_data": "menu_lookup_person"},
                {"text": "📊 Ver mi progreso", "callback_data": "menu_resumen"},
            ],
            [
                {"text": "💸 Registrar gasto", "callback_data": "menu_gasto"},
                {"text": "📈 Gastos del mes", "callback_data": "menu_gastos_mes"},
            ]
        ]
    }
    send_message(f"{greeting}\n¿Qué quieres hacer?", keyboard)

def start_checkin(block):
    habits = get_habits(block)
    if not habits:
        return
    session["block"] = block
    session["pending"] = list(habits)
    session["results"] = {}
    session["waiting"] = False
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

        # Menú de buenos días: 7:30 AM
        if h == 7 and m == 30:
            send_menu()
            time.sleep(61)

        # Check-in cama: 8:00 AM — mensaje disruptivo
        elif h == 8 and m == 0:
            habits = get_habits("morning")
            cama = next((h for h in habits if h["key"] == "cama"), None)
            if cama:
                all_state = get_all_state()
                msg = ai_checkin_message(cama, all_state)
                keyboard = {"inline_keyboard": [[
                    {"text": "✅ Sí", "callback_data": "done_cama"},
                    {"text": "❌ No", "callback_data": "skip_cama"}
                ]]}
                session["pending"] = [cama]
                session["results"] = {}
                session["block"] = "morning"
                send_message(msg, keyboard)
                session["waiting"] = True
            time.sleep(61)

        # Recordatorio si no tendió cama: 9:00 AM
        elif h == 9 and m == 0:
            all_state = get_all_state()
            cama = next((s for s in all_state if s.get("key") == "cama"), None)
            if cama and not cama.get("done_today"):
                send_message("👀 Oye, ¿ya tendiste la cama? Tu cerebro TDAH necesita esa victoria temprana para arrancar bien el día.",
                    {"inline_keyboard": [[
                        {"text": "✅ Ya la tendí", "callback_data": "done_cama"},
                        {"text": "❌ Aún no", "callback_data": "skip_cama"}
                    ]]}
                )
            time.sleep(61)

        # Check-in ejercicio: 9:00 PM todos los días
        elif h == 21 and m == 0:
            habits = get_habits("night")
            ejercicio = next((hb for hb in habits if hb["key"] == "ejercicio"), None)
            if ejercicio:
                all_state = get_all_state()
                msg = ai_checkin_message(ejercicio, all_state)
                keyboard = {"inline_keyboard": [[
                    {"text": "✅ Sí", "callback_data": "done_ejercicio"},
                    {"text": "❌ No", "callback_data": "skip_ejercicio"}
                ]]}
                session["pending"] = [ejercicio]
                session["results"] = {}
                session["block"] = "night"
                send_message(msg, keyboard)
                session["waiting"] = True
            time.sleep(61)

        # Alertas inteligentes: 8:00 PM de lunes a sábado
        elif h == 20 and m == 0 and now.weekday() != 6:
            check_smart_alerts()
            time.sleep(61)

        # Análisis semanal: domingos 8:00 PM
        elif h == 20 and m == 0 and now.weekday() == 6:
            send_weekly_analysis()
            time.sleep(61)

        # Análisis financiero mensual: día 1 de cada mes a las 9 AM
        elif h == 9 and m == 0 and now.day == 1:
            send_monthly_finance_analysis()
            time.sleep(61)

        # Check-in comida: 10:00 PM
        elif h == 22 and m == 0:
            habits = get_habits("night")
            comida = next((h for h in habits if h["key"] == "comida"), None)
            if comida:
                all_state = get_all_state()
                msg = ai_checkin_message(comida, all_state)
                keyboard = {"inline_keyboard": [[
                    {"text": "✅ Sí", "callback_data": "done_comida"},
                    {"text": "〰️ Más o menos", "callback_data": "partial_comida"},
                    {"text": "❌ No", "callback_data": "skip_comida"}
                ]]}
                session["pending"] = [comida]
                session["results"] = {}
                session["block"] = "night"
                send_message(msg, keyboard)
                session["waiting"] = True
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

    if data.startswith("menu_"):
        answer_callback(callback_id)
        if data == "menu_checkin":
            hour = datetime.now().hour
            block = "morning" if hour < 15 else "night"
            start_checkin(block)
        elif data == "menu_resumen":
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
        elif data == "menu_add_person":
            send_message("👤 Escríbeme así:\n\n`/persona add Nombre — lo que sabes de esa persona`\n\nEjemplo:\n`/persona add Angélica Albarrán — le gustan los perros, cumpleaños 30 octubre`")
        elif data == "menu_lookup_person":
            send_message("🔍 Escríbeme así:\n\n`/persona info Nombre` — para ver lo que sabes\n`/persona suggest Nombre` — para ideas de cómo conectar\n`/persona list` — para ver todos tus contactos")
        elif data == "menu_gastos_mes":
            handle_gastos_resumen()
        elif data == "menu_gasto":
            send_message(
                "💸 *Registrar gasto*\n\nTienes 3 formas:\n\n"
                "📸 *Foto del ticket* — mándame la foto directo\n"
                "`/gasto 250 comida_fuera BBVA_Gold Tacos el Güero` — manual\n"
                "`/gastos` — ver resumen del mes"
            )
        elif data.startswith("gasto_confirm_"):
            # Confirmar gasto extraído de foto
            expense_json = data[14:]
            try:
                exp = json.loads(expense_json)
                save_expense(exp)
                send_message(f"✅ Guardado: *{exp['description']}* — ${abs(exp['amount']):.0f} en {exp['card']}")
            except:
                send_message("❌ Error guardando el gasto.")
        elif data.startswith("gasto_cat_"):
            # Cambiar categoría del gasto pendiente
            parts = data[10:].split("_", 1)
            if len(parts) == 2 and "pending_expense" in session:
                session["pending_expense"]["category"] = parts[0] + "_" + parts[1] if len(parts) > 1 else parts[0]
                exp = session["pending_expense"]
                save_expense(exp)
                send_message(f"✅ Guardado como *{exp['category']}*: {exp['description']} — ${abs(exp['amount']):.0f}")
                session.pop("pending_expense", None)
        elif data.startswith("exp_card_"):
            card = data[9:]
            if "pending_expense" in session:
                session["pending_expense"]["card"] = card
                exp = session["pending_expense"]
                save_expense(exp)
                cat_label = CATS_FINANCE.get(exp.get("category", "otro"), "📌 Otro")
                card_label = CARDS_FINANCE.get(card, card)
                send_message(
                    f"✅ *Guardado en tu tracker*\n\n"
                    f"💰 ${abs(exp.get('amount', 0)):.0f} — {exp.get('description', '')}\n"
                    f"📂 {cat_label} · {card_label}\n\n"
                    f"_Visible en mi-tracker-xi.vercel.app_"
                )
                session.pop("pending_expense", None)
            else:
                send_message("❌ No hay gasto pendiente. Manda la foto de nuevo.")
        return
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
        send_menu()
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
    elif text == "/gastos":
        handle_gastos_resumen()
    elif text.startswith("/gasto"):
        handle_gasto_command(text)
    elif text == "/semanal":
        send_message("📊 Generando tu análisis semanal...")
        send_weekly_analysis()
    elif text.startswith("/persona"):
        answer = handle_persona_command(text)
        send_message(answer)
    elif not text.startswith("/"):
        all_state = get_all_state()
        answer = ai_answer_question(text, all_state)
        send_message(answer)

# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO FINANZAS — Registro de gastos desde Telegram
# Conectado a la misma tabla expenses de mi-tracker-xi.vercel.app
# ══════════════════════════════════════════════════════════════════════════════

CATS_FINANCE = {
    "renta": "🏠 Renta",
    "comida_super": "🛒 Súper",
    "comida_fuera": "🍽️ Comida fuera",
    "transporte": "🚗 Transporte",
    "entretenimiento": "🎬 Entretenimiento",
    "servicios": "💡 Servicios",
    "salud": "💊 Salud",
    "educacion": "📚 Educación",
    "subscripciones": "📱 Subscripciones",
    "movilidad": "🛵 Movilidad",
    "ahorros_transfer": "🏦 Ahorro",
    "otro": "📌 Otro",
}

CARDS_FINANCE = {
    "BBVA_Gold": "BBVA Gold",
    "HSBC_Volaris": "HSBC Volaris",
    "BBVA_Debito": "BBVA Débito",
    "Efectivo": "Efectivo",
}

def save_expense(exp: dict):
    """Guarda un gasto en la tabla expenses de Supabase (misma que el tracker web)."""
    row = {
        "date": exp.get("date", date.today().isoformat()),
        "amount": -abs(float(exp.get("amount", 0))),  # siempre negativo para gastos
        "description": exp.get("description", ""),
        "category": exp.get("category", "otro"),
        "card": exp.get("card", "BBVA_Gold"),
        "notes": exp.get("notes", "Registrado desde Telegram"),
        "reconciled": "pendiente",
    }
    sb_post("expenses", row)


def ai_extract_expense_from_photo(image_base64: str) -> dict:
    """Usa Claude Vision para extraer datos del ticket."""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Analiza este ticket/recibo y extrae la información.
Responde SOLO con JSON válido, sin markdown:
{"amount": número_positivo, "description": "nombre del comercio o descripción breve", "category": "una de: renta/comida_super/comida_fuera/transporte/entretenimiento/servicios/salud/educacion/subscripciones/movilidad/ahorros_transfer/otro", "date": "YYYY-MM-DD o null si no se ve"}

Si no puedes leer el ticket, responde: {"error": "no_readable"}"""
                    }
                ]
            }]
        }
    )
    text = r.json()["content"][0]["text"]
    clean = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def handle_photo(update: dict):
    """Procesa foto de ticket enviada por el usuario."""
    msg = update.get("message", {})
    photos = msg.get("photo", [])
    if not photos:
        return

    # Tomar la foto de mejor calidad
    best_photo = max(photos, key=lambda p: p.get("file_size", 0))
    file_id = best_photo["file_id"]

    send_message("📸 Leyendo tu ticket...")

    # Descargar la foto
    try:
        file_r = requests.get(f"{BASE_URL}/getFile", params={"file_id": file_id})
        file_path = file_r.json()["result"]["file_path"]
        img_r = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
        image_base64 = __import__("base64").b64encode(img_r.content).decode()

        expense = ai_extract_expense_from_photo(image_base64)

        if "error" in expense:
            send_message("❌ No pude leer el ticket. Intenta con mejor iluminación o regístralo manual:\n`/gasto 250 comida_fuera BBVA_Gold Descripción`")
            return

        # Guardar fecha de hoy si no se detectó
        if not expense.get("date"):
            expense["date"] = date.today().isoformat()

        # Guardar en sesión para confirmación
        session["pending_expense"] = expense

        cat_label = CATS_FINANCE.get(expense.get("category", "otro"), "📌 Otro")
        amount = abs(expense.get("amount", 0))

        # Pedir tarjeta y confirmación
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "BBVA Gold", "callback_data": f"exp_card_BBVA_Gold"},
                    {"text": "HSBC Volaris", "callback_data": f"exp_card_HSBC_Volaris"},
                ],
                [
                    {"text": "BBVA Débito", "callback_data": f"exp_card_BBVA_Debito"},
                    {"text": "Efectivo", "callback_data": f"exp_card_Efectivo"},
                ]
            ]
        }
        send_message(
            f"🧾 *Ticket detectado*\n\n"
            f"💰 *${amount:.0f}* — {expense.get('description', '')}\n"
            f"📂 Categoría: {cat_label}\n"
            f"📅 {expense.get('date', date.today().isoformat())}\n\n"
            f"¿En qué tarjeta lo cargo?",
            keyboard
        )

    except Exception as e:
        print(f"Error procesando foto: {e}", flush=True)
        send_message("❌ Error procesando el ticket. Intenta de nuevo o usa `/gasto` manual.")


def handle_gasto_command(text: str):
    """Procesa comando manual: /gasto 250 comida_fuera BBVA_Gold Descripción."""
    parts = text.strip().split(" ", 4)
    # /gasto amount category card description
    if len(parts) < 3:
        send_message(
            "💸 *Registrar gasto manual*\n\n"
            "`/gasto MONTO CATEGORÍA TARJETA Descripción`\n\n"
            "Ejemplo:\n`/gasto 250 comida_fuera BBVA_Gold Tacos el Güero`\n\n"
            "Categorías: `renta` `comida_super` `comida_fuera` `transporte` `entretenimiento` `servicios` `salud` `educacion` `subscripciones` `movilidad` `otro`\n\n"
            "Tarjetas: `BBVA_Gold` `HSBC_Volaris` `BBVA_Debito` `Efectivo`"
        )
        return

    try:
        amount = float(parts[1])
        category = parts[2] if len(parts) > 2 else "otro"
        card = parts[3] if len(parts) > 3 else "BBVA_Gold"
        description = parts[4] if len(parts) > 4 else "Gasto registrado"

        exp = {
            "amount": amount,
            "category": category,
            "card": card,
            "description": description,
            "date": date.today().isoformat(),
        }
        save_expense(exp)
        cat_label = CATS_FINANCE.get(category, "📌 Otro")
        card_label = CARDS_FINANCE.get(card, card)
        send_message(f"✅ Guardado en tu tracker:\n\n💰 *${amount:.0f}* — {description}\n📂 {cat_label} · {card_label}")
    except Exception as e:
        send_message(f"❌ Error: {e}\nFormato: `/gasto 250 comida_fuera BBVA_Gold Descripción`")


def handle_gastos_resumen():
    """Muestra resumen de gastos del mes actual."""
    today = date.today()
    first_day = today.replace(day=1).isoformat()

    expenses_raw = sb_get("expenses", f"date=gte.{first_day}&select=*&order=date.desc")

    if not expenses_raw:
        send_message("📊 Sin gastos registrados este mes.")
        return

    # Filtrar solo gastos reales (excluir ingresos/abonos)
    # Los gastos pueden estar como negativos o positivos según cómo se registraron
    gastos = []
    for e in expenses_raw:
        amt = e.get("amount", 0)
        entry_type = e.get("entry_type", "gasto")
        # Excluir ingresos y pagos de tarjeta
        if entry_type in ("ingreso_nomina", "ingreso_otro", "pago_tarjeta"):
            continue
        # Incluir si tiene monto (positivo o negativo, ambos son gastos)
        if amt != 0:
            gastos.append(e)

    if not gastos:
        send_message("📊 Sin gastos registrados este mes.")
        return

    total = sum(abs(e.get("amount", 0)) for e in gastos)
    by_cat = {}
    for e in gastos:
        cat = e.get("category", "otro")
        by_cat[cat] = by_cat.get(cat, 0) + abs(e.get("amount", 0))

    lines = [f"📊 *Gastos de {today.strftime('%B %Y')}*\n"]
    lines.append(f"💸 Total: *${total:,.0f} MXN*\n")

    sorted_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
    for cat, amt in sorted_cats[:6]:
        label = CATS_FINANCE.get(cat, cat)
        pct = int(amt / total * 100) if total > 0 else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"{label}\n`{bar}` ${amt:,.0f} ({pct}%)")

    lines.append(f"\n_{len(gastos)} transacciones · Ver más en mi-tracker-xi.vercel.app_")
    send_message("\n".join(lines))


def ai_monthly_finance_analysis(expenses_month: list, incomes_month: list) -> str:
    """Análisis financiero mensual con IA."""
    # Filtrar solo gastos reales (excluir ingresos/traspasos)
    gastos_reales = [e for e in expenses_month
                     if e.get("entry_type", "gasto") not in ("ingreso_nomina", "ingreso_otro", "pago_tarjeta")
                     and e.get("amount", 0) != 0]

    total_gastos = sum(abs(e.get("amount", 0)) for e in gastos_reales)
    total_ingresos = sum(e.get("amount", 0) for e in incomes_month if e.get("amount", 0) > 0)
    by_cat = {}
    for e in gastos_reales:
        cat = e.get("category", "otro")
        by_cat[cat] = by_cat.get(cat, 0) + abs(e.get("amount", 0))
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
    context = "\n".join([f"- {CATS_FINANCE.get(k,k)}: ${v:,.0f}" for k, v in top_cats])

    return ai_call(f"""Eres Hábit, coach financiero personal de Yair. Español mexicano, directo y honesto.

Resumen financiero del mes:
Ingresos: ${total_ingresos:,.0f} MXN
Gastos: ${total_gastos:,.0f} MXN
Balance: ${total_ingresos - total_gastos:,.0f} MXN
Top categorías de gasto:
{context}

Escribe un análisis mensual financiero (máx 8 líneas):
1. Estado general del mes (directo, sin rodeos)
2. Lo que más gastó y si tiene sentido
3. Una observación inteligente sobre sus patrones
4. Una acción concreta para el próximo mes
Tono: como un CFO amigo que lo conoce. Sin frases genéricas.""", 400)


def send_monthly_finance_analysis():
    """Manda análisis financiero mensual — primer día del mes."""
    today = date.today()
    first_day = today.replace(day=1).isoformat()
    from datetime import timedelta
    last_month_last = (today.replace(day=1) - timedelta(days=1))
    last_month_first = last_month_last.replace(day=1).isoformat()

    expenses_month = sb_get("expenses", f"date=gte.{last_month_first}&date=lte.{last_month_last.isoformat()}&select=*")
    incomes_month = sb_get("incomes", f"date=gte.{last_month_first}&date=lte.{last_month_last.isoformat()}&select=*")

    if not expenses_month:
        return

    analysis = ai_monthly_finance_analysis(expenses_month, incomes_month)
    total = sum(abs(e.get("amount", 0)) for e in expenses_month if e.get("amount", 0) < 0)

    send_message(
        f"💼 *Análisis financiero — {last_month_last.strftime('%B %Y')}*\n\n"
        f"Total gastado: *${total:,.0f} MXN*\n\n"
        f"{analysis}\n\n"
        f"_Ver detalles en mi-tracker-xi.vercel.app_ 📊"
    )


    print("🤖 Hábit bot iniciando...")
    # Scheduler en hilo separado
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()

    # Webhook server
    wt = threading.Thread(target=start_webhook_server, daemon=True)
    wt.start()

    # Limpiar updates viejos al arrancar para evitar duplicados
    offset = None
    try:
        old = get_updates(None)
        if old:
            offset = old[-1]["update_id"] + 1
            print(f"🧹 Limpiando {len(old)} updates viejos", flush=True)
    except:
        pass

    print("✅ Bot corriendo. Esperando mensajes...", flush=True)
    processed = set()

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                offset = uid + 1
                if uid in processed:
                    continue
                processed.add(uid)
                if len(processed) > 200:
                    processed = set(list(processed)[-100:])
                if "callback_query" in update:
                    handle_callback(update)
                elif "message" in update:
                    msg = update.get("message", {})
                    requests.post(f"{BASE_URL}/sendChatAction", json={"chat_id": CHAT_ID, "action": "typing"})
                    if "photo" in msg:
                        handle_photo(update)
                    else:
                        handle_message(update)
        except Exception as e:
            print(f"Error: {e}", flush=True)
            time.sleep(5)

# ══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS SEMANAL — Domingos 8:00 PM
# ══════════════════════════════════════════════════════════════════════════════

def ai_weekly_analysis(logs_week: list, all_state: list) -> str:
    """Genera un análisis semanal profundo con reencuadre motivacional."""
    done_by_habit = {}
    for h in all_state:
        key = h.get("key")
        done_count = sum(1 for l in logs_week if l.get("habit_key") == key and l.get("done"))
        done_by_habit[h.get("name")] = done_count

    context = "\n".join([f"- {name}: {count}/7 días" for name, count in done_by_habit.items()])
    best = max(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)
    worst = min(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)

    return ai_call(f"""Eres Hábit, coach de hábitos para TDAH. Español mexicano, honesto, profundo y motivador.

Es domingo — momento de análisis semanal de Yair (24 años, auditor en EY, construyendo su mejor versión).

Resultados de la semana:
{context}
Mejor hábito: {best[0]} ({best[1]}/7)
Hábito a reforzar: {worst[0]} ({worst[1]}/7)

Escribe un análisis semanal con esta estructura (usa *negritas* para los títulos):
1. *Esta semana fuiste:* — una frase que capture la esencia de su semana (honesto, no condescendiente)
2. *Lo que construiste:* — qué logró en concreto, qué patrón se está formando
3. *El porqué importa:* — recuérdale POR QUÉ está haciendo esto. Su meta de Manager, su salud, su identidad. Hazlo sentir el propósito.
4. *La única cosa para esta semana:* — UN foco concreto para la semana que viene
5. *Tu frase de la semana:* — una frase corta y poderosa que lo regrese a su centro cuando se distraiga

Tono: como un mentor que lo conoce de verdad. Sin frases genéricas. Máximo 12 líneas.""", 500)


def send_weekly_analysis():
    """Manda el análisis semanal."""
    from datetime import timedelta
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()

    logs_week = sb_get("habit_logs", f"logged_at=gte.{week_ago}&select=*")
    all_state = get_all_state()

    analysis = ai_weekly_analysis(logs_week, all_state)

    total_possible = len(all_state) * 7
    total_done = sum(1 for l in logs_week if l.get("done"))
    pct = round((total_done / total_possible * 100)) if total_possible > 0 else 0
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)

    send_message(
        f"📅 *Análisis semanal*\n"
        f"`{bar}` {pct}% completado\n\n"
        f"{analysis}\n\n"
        f"_Nueva semana, nuevo sprint. Tú decides quién eres. 🔥_"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ALERTAS INTELIGENTES — Revisión diaria
# ══════════════════════════════════════════════════════════════════════════════

def check_smart_alerts():
    from datetime import timedelta
    today = date.today()
    alerts = []
    try:
        budgets_raw = sb_get("budgets", "id=eq.1&select=data")
        if budgets_raw and budgets_raw[0].get("data"):
            budgets = budgets_raw[0]["data"]
            first_day = today.replace(day=1).isoformat()
            expenses_month = sb_get("expenses", f"date=gte.{first_day}&select=category,amount")
            by_cat = {}
            for e in expenses_month:
                if e.get("amount", 0) < 0:
                    cat = e.get("category", "otro")
                    by_cat[cat] = by_cat.get(cat, 0) + abs(e["amount"])
            for cat, spent in by_cat.items():
                budget = budgets.get(cat, 0)
                if budget > 0:
                    pct = spent / budget * 100
                    if pct >= 100:
                        alerts.append(f"🚨 *{CATS_FINANCE.get(cat, cat)}* — presupuesto AGOTADO (${spent:,.0f} / ${budget:,.0f})")
                    elif pct >= 80:
                        alerts.append(f"⚠️ *{CATS_FINANCE.get(cat, cat)}* — {pct:.0f}% del presupuesto usado")
    except Exception as e:
        print(f"Error alertas presupuesto: {e}", flush=True)

    card_cycles = {
        "BBVA Gold": {"corte_dia": 18, "pago_dia": 7},
        "HSBC Volaris": {"corte_dia": 9, "pago_dia": 28},
    }
    from datetime import timedelta
    for card_name, cycle in card_cycles.items():
        day = today.day
        corte_dia = cycle["corte_dia"]
        days_to_corte = corte_dia - day if day <= corte_dia else (today.replace(day=1) + timedelta(days=32)).replace(day=corte_dia).day - day + 30
        pago_dia = cycle["pago_dia"]
        days_to_pago = pago_dia - day if day <= pago_dia else (today.replace(day=1) + timedelta(days=32)).replace(day=pago_dia).day - day + 30
        if 0 < days_to_corte <= 5:
            alerts.append(f"📅 *{card_name}* — corte en {days_to_corte} días")
        if 0 < days_to_pago <= 3:
            alerts.append(f"💳 *{card_name}* — pago en {days_to_pago} días. ¡No olvides pagar!")

    try:
        first_day = today.replace(day=1)
        last_month_end = first_day - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        this_month_exp = sb_get("expenses", f"date=gte.{first_day.isoformat()}&select=amount")
        last_month_exp = sb_get("expenses", f"date=gte.{last_month_start.isoformat()}&date=lte.{last_month_end.isoformat()}&select=amount")
        this_total = sum(abs(e["amount"]) for e in this_month_exp if e.get("amount", 0) < 0)
        last_total = sum(abs(e["amount"]) for e in last_month_exp if e.get("amount", 0) < 0)
        days_elapsed = today.day
        if last_total > 0 and days_elapsed > 5:
            projected = (this_total / days_elapsed) * 30
            if projected > last_total * 1.15:
                pct_over = ((projected - last_total) / last_total) * 100
                alerts.append(f"📈 Vas *{pct_over:.0f}% más arriba* que el mes pasado en gasto. Proyección: ${projected:,.0f}")
    except Exception as e:
        print(f"Error alertas overspending: {e}", flush=True)

    try:
        all_state = get_all_state()
        for h in all_state:
            if h.get("streak", 0) == 0:
                logs = sb_get("habit_logs", f"habit_key=eq.{h['key']}&done=eq.false&select=logged_at&order=logged_at.desc&limit=5")
                if len(logs) >= 3:
                    alerts.append(f"💤 *{h.get('emoji','')} {h.get('name','')}* — {len(logs)}+ días sin completarlo")
    except Exception as e:
        print(f"Error alertas hábitos: {e}", flush=True)

    if alerts:
        send_message("🔔 *Alertas del día*\n\n" + "\n".join(alerts) + "\n\n_Revisa mi-tracker-xi.vercel.app_")


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOKS — Recibe eventos de Supabase y la web
# ══════════════════════════════════════════════════════════════════════════════

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_POST(self):
        if self.path != "/webhook/supabase":
            self.send_response(404); self.end_headers(); return
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {WEBHOOK_SECRET}":
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.end_headers()
        threading.Thread(target=process_webhook, args=(body,), daemon=True).start()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self.send_response(404); self.end_headers()


def process_webhook(body: bytes):
    try:
        data = json.loads(body)
        table = data.get("table", "")
        record = data.get("record", {})
        event_type = data.get("type", "")
        if table == "expenses" and event_type == "INSERT":
            notes = record.get("notes", "")
            if "Telegram" not in notes:
                amount = abs(record.get("amount", 0))
                cat = CATS_FINANCE.get(record.get("category", "otro"), "📌 Otro")
                card = CARDS_FINANCE.get(record.get("card", ""), record.get("card", ""))
                send_message(f"💸 Gasto registrado desde la web:\n*${amount:,.0f}* — {record.get('description', '')}\n{cat} · {card}")
        elif table == "debts" and event_type == "INSERT":
            send_message(f"📋 Nueva deuda desde la web:\n*{record.get('name','Deuda')}* — ${record.get('amount',0):,.0f}")
        elif table == "incomes" and event_type == "INSERT":
            amount = record.get("amount", 0)
            tipo = record.get("type", "otro")
            if tipo == "sueldo":
                send_message(f"💼 ¡Ingresó tu quincena! *${amount:,.0f} MXN*\n_¿Ya separaste el ahorro?_")
            elif tipo == "extraordinario":
                send_message(f"🎁 Ingreso extraordinario: *${amount:,.0f} MXN*")
    except Exception as e:
        print(f"Error webhook: {e}", flush=True)


def start_webhook_server():
    server = HTTPServer(("0.0.0.0", 8080), WebhookHandler)
    print("🌐 Webhook server en puerto 8080", flush=True)
    server.serve_forever()


def main():
    print("🤖 Hábit bot iniciando...", flush=True)

    try:
        r = requests.get(f"{BASE_URL}/getMe")
        print(f"✅ Telegram OK: {r.json().get('result',{}).get('username','??')}", flush=True)
    except Exception as e:
        print(f"❌ Telegram ERROR: {e}", flush=True)

    try:
        data = get_habits()
        print(f"✅ Supabase OK: {len(data)} hábitos", flush=True)
    except Exception as e:
        print(f"❌ Supabase ERROR: {e}", flush=True)

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()

    # Webhook server
    wt = threading.Thread(target=start_webhook_server, daemon=True)
    wt.start()

    # Limpiar updates viejos al arrancar para evitar duplicados
    offset = None
    try:
        old = get_updates(None)
        if old:
            offset = old[-1]["update_id"] + 1
            print(f"🧹 Limpiando {len(old)} updates viejos", flush=True)
    except:
        pass

    print("✅ Bot corriendo. Esperando mensajes...", flush=True)
    processed = set()

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                offset = uid + 1
                if uid in processed:
                    continue
                processed.add(uid)
                if len(processed) > 200:
                    processed = set(list(processed)[-100:])
                if "callback_query" in update:
                    handle_callback(update)
                elif "message" in update:
                    msg = update.get("message", {})
                    requests.post(f"{BASE_URL}/sendChatAction", json={"chat_id": CHAT_ID, "action": "typing"})
                    if "photo" in msg:
                        handle_photo(update)
                    else:
                        handle_message(update)
        except Exception as e:
            print(f"Error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
