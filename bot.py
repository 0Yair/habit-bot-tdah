"""
BOT PRINCIPAL — Orquesta los módulos: habitos, finanzas, asistente.
"""
import socket, sys, time, threading
import requests

from shared import (
    BASE_URL, CHAT_ID, TOKEN,
    session, get_all_state,
    send_message, answer_callback, get_updates, sb_get, now_mx,
)
from habitos import (
    get_habits, start_checkin, ask_next_habit,
    send_resumen, send_rachas, send_weekly_analysis,
    check_smart_alerts, handle_habit_callback,
    start_new_habit_flow, show_reminders_menu,
    handle_habit_flow_text, handle_habit_flow_callback,
)
from finanzas import (
    handle_photo, handle_gasto_command, handle_gastos_resumen,
    handle_gastos_por_categoria, handle_finance_callback,
    send_monthly_finance_analysis, test_supabase_connection,
    send_finance_submenu, handle_finance_query,
)
from asistente import handle_persona_command, ai_answer_question

# ── Instancia única (evita duplicados cuando corre más de un proceso) ──────────
def _ensure_single_instance():
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _s.bind(("127.0.0.1", 47832))   # puerto exclusivo del bot
        _s.listen(1)
        return _s                        # mantener referencia para que no se cierre
    except OSError:
        print("❌ El bot ya está corriendo. Saliendo.", flush=True)
        sys.exit(0)

_LOCK = _ensure_single_instance()

# ── Submenús ───────────────────────────────────────────────────────────────────
def send_menu():
    h = now_mx().hour
    saludo = "☀️ *Buenos días*" if h < 12 else ("🌤 *Buenas tardes*" if h < 19 else "🌙 *Buenas noches*")
    send_message(f"{saludo}, Yair", {"inline_keyboard": [
        [{"text": "💰 Finanzas", "callback_data": "menu_finanzas"},
         {"text": "🏃 Hábitos",  "callback_data": "menu_habitos"}],
        [{"text": "👥 Personas", "callback_data": "menu_personas"}],
    ]})

def send_habitos_submenu():
    send_message("🏃 *Hábitos*", {"inline_keyboard": [
        [{"text": "📋 Check-in",      "callback_data": "hab_checkin"},
         {"text": "📊 Progreso",      "callback_data": "hab_progreso"}],
        [{"text": "➕ Nuevo hábito",  "callback_data": "hab_nuevo"},
         {"text": "⏰ Recordatorios", "callback_data": "hab_recordatorios"}],
        [{"text": "🥗 Plan comida",   "callback_data": "hab_plan_comida"},
         {"text": "📅 Semanal",       "callback_data": "hab_semanal"}],
    ]})

# ── Dispatch de callbacks simples (sin contexto de mensaje) ───────────────────
_CB = {
    "menu_finanzas":     send_finance_submenu,
    "menu_habitos":      send_habitos_submenu,
    "menu_personas":     lambda: send_message(
        "👥 *Personas*\n\n"
        "`/persona add Nombre — lo que sabes`\n"
        "`/persona info Nombre`\n"
        "`/persona suggest Nombre`\n"
        "`/persona list`"
    ),
    "fin_ciclo":         handle_gastos_resumen,
    "fin_cats":          handle_gastos_por_categoria,
    "fin_registrar":     lambda: send_message(
        "💸 *Registrar gasto*\n\n"
        "📸 Foto del ticket — mándala directo\n"
        "`/gasto 250 comida_fuera BBVA_Gold Tacos`"
    ),
    "hab_progreso":      send_resumen,
    "hab_nuevo":         start_new_habit_flow,
    "hab_recordatorios": show_reminders_menu,
    "hab_semanal":       send_weekly_analysis,
    "hab_plan_comida":   lambda: send_message("🥗 Plan alimenticio — próximamente."),
}

# ── Dispatch de comandos de texto ─────────────────────────────────────────────
_CMD = {
    "/start":   send_menu,
    "/menu":    send_menu,
    "/resumen": send_resumen,
    "/racha":   send_rachas,
    "/gastos":  handle_gastos_resumen,
    "/semanal": send_weekly_analysis,
}

# ── Callbacks ──────────────────────────────────────────────────────────────────
def handle_callback(update):
    cb         = update["callback_query"]
    data       = cb["data"]
    chat_id    = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    original   = cb["message"].get("text", "")

    answer_callback(cb["id"])

    # 1. Flujos multi-paso activos
    if session.get("flow") in ("new_habit", "set_reminder"):
        if handle_habit_flow_callback(data):
            return

    # 2. Respuestas de check-in (done_, skip_, partial_) — también desde recordatorios
    if handle_habit_callback(data, chat_id, message_id, original):
        return

    # 3. Dispatch estático
    if data in _CB:
        _CB[data]()
        return

    # 4. Callbacks con lógica extra
    if data == "hab_checkin":
        start_checkin("morning" if now_mx().hour < 15 else "night")
        return

    if data == "fin_consultar":
        session["flow"] = "fin_query"
        send_message("💬 ¿Qué quieres saber de tus gastos?\nEj: ¿cuánto gasté en comida este ciclo?")
        return

    # 5. Finanzas: ticket foto y confirmaciones
    handle_finance_callback(data)

# ── Mensajes de texto ──────────────────────────────────────────────────────────
def handle_message(update):
    msg     = update.get("message", {})
    text    = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if chat_id != str(CHAT_ID):
        return

    # Flujos activos (entrada de texto durante un flujo multi-paso)
    flow = session.get("flow")
    if flow == "fin_query" and not text.startswith("/"):
        handle_finance_query(text)
        session["flow"] = None
        return
    if flow in ("new_habit", "set_reminder"):
        handle_habit_flow_text(text)
        return

    # Comandos del dict
    if text in _CMD:
        _CMD[text]()
        return

    # Comandos con argumento
    if text.startswith("/checkin"):
        start_checkin("morning" if now_mx().hour < 15 else "night")
    elif text.startswith("/gasto "):
        handle_gasto_command(text)
    elif text.startswith("/persona"):
        send_message(handle_persona_command(text))
    elif text == "/test_gasto":
        send_message("🔍 Diagnosticando conexión Supabase...")
        send_message(test_supabase_connection())
    elif not text.startswith("/"):
        send_message(ai_answer_question(text, get_all_state()))

# ── Scheduler ─────────────────────────────────────────────────────────────────
_rem_cache: list  = []
_rem_cache_ts: float = 0.0

def _reminders():
    """Cache de recordatorios de BD, refresca cada 5 min."""
    global _rem_cache, _rem_cache_ts
    if time.time() - _rem_cache_ts > 300:
        try:
            _rem_cache    = sb_get("bot_reminders", "active=eq.true&select=*") or []
            _rem_cache_ts = time.time()
        except Exception as e:
            print(f"[reminders] {e}", flush=True)
    return _rem_cache

def _trigger(key: str, block: str):
    """Inicia check-in para un hábito específico."""
    habit = next((h for h in get_habits(block) if h["key"] == key), None)
    if habit:
        session.update(pending=[habit], results={}, block=block,
                       flow=None, active_message_id=None, active_chat_id=None)
        ask_next_habit()

def scheduler_loop():
    last = (-1, -1)
    while True:
        now = now_mx()
        h, m = now.hour, now.minute
        if (h, m) != last:
            last = (h, m)

            # Todos los bloques son `if` (no `elif`) para que múltiples
            # acciones puedan dispararse en el mismo minuto.
            if h == 7  and m == 30: send_menu()

            if h == 8  and m == 0:  _trigger("cama", "morning")

            if h == 9  and m == 0:
                s = next((x for x in get_all_state() if x.get("key") == "cama"), None)
                if s and not s.get("done_today"):
                    send_message("👀 ¿Ya tendiste la cama?", {"inline_keyboard": [[
                        {"text": "✅ Ya", "callback_data": "done_cama"},
                        {"text": "❌ No", "callback_data": "skip_cama"},
                    ]]})
                if now.day == 19:
                    send_monthly_finance_analysis()

            if h == 20 and m == 0:
                send_weekly_analysis() if now.weekday() == 6 else check_smart_alerts()

            if h == 21 and m == 0:  _trigger("ejercicio", "night")

            if h == 22 and m == 0:  _trigger("comida", "night")

            # Recordatorios configurados en BD
            for rem in _reminders():
                if rem.get("hour") == h and rem.get("minute") == m:
                    _trigger(rem["habit_key"], rem.get("block", "morning"))

        time.sleep(30)

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🤖 Bot iniciando...", flush=True)

    try:
        username = requests.get(f"{BASE_URL}/getMe").json()["result"]["username"]
        print(f"✅ Telegram: @{username}", flush=True)
    except Exception as e:
        print(f"❌ Telegram: {e}", flush=True)

    try:
        print(f"✅ Supabase: {len(get_habits())} hábitos", flush=True)
    except Exception as e:
        print(f"❌ Supabase: {e}", flush=True)

    threading.Thread(target=scheduler_loop, daemon=True).start()

    # Descartar updates acumulados antes de arrancar
    offset = None
    try:
        old = get_updates(None)
        if old:
            offset = old[-1]["update_id"] + 1
            print(f"🧹 {len(old)} updates viejos ignorados", flush=True)
    except Exception:
        pass

    print("✅ Esperando mensajes...", flush=True)
    processed: set = set()

    while True:
        try:
            for update in get_updates(offset):
                uid    = update["update_id"]
                offset = uid + 1
                if uid in processed:
                    continue
                processed.add(uid)
                if len(processed) > 200:
                    processed = set(list(processed)[-100:])

                if "callback_query" in update:
                    handle_callback(update)
                elif "message" in update:
                    msg = update["message"]
                    requests.post(f"{BASE_URL}/sendChatAction",
                                  json={"chat_id": CHAT_ID, "action": "typing"})
                    if "photo" in msg:
                        handle_photo(update)
                    else:
                        handle_message(update)
        except Exception as e:
            print(f"[loop] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
