"""
BOT PRINCIPAL — Orquesta los módulos: habitos, finanzas, asistente.
"""
import time
import threading
import requests
from datetime import datetime, date

from shared import (
    BASE_URL, CHAT_ID, TOKEN,
    session, get_all_state,
    send_message, answer_callback, get_updates, sb_get,
    now_mx,
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

# ── Menú principal ─────────────────────────────────────────────────────────────
def send_menu():
    hour = now_mx().hour
    greeting = "☀️ *Buenos días*" if hour < 12 else ("🌤 *Buenas tardes*" if hour < 19 else "🌙 *Buenas noches*")
    keyboard = {"inline_keyboard": [
        [{"text": "💰 Finanzas", "callback_data": "menu_finanzas"},
         {"text": "🏃 Hábitos",  "callback_data": "menu_habitos"}],
        [{"text": "👥 Personas", "callback_data": "menu_personas"}],
    ]}
    send_message(f"{greeting}, Yair", keyboard)

def send_habitos_submenu():
    send_message(
        "🏃 *Hábitos*",
        {"inline_keyboard": [
            [{"text": "📋 Check-in",      "callback_data": "hab_checkin"},
             {"text": "📊 Progreso",      "callback_data": "hab_progreso"}],
            [{"text": "➕ Nuevo",          "callback_data": "hab_nuevo"},
             {"text": "⏰ Recordatorios", "callback_data": "hab_recordatorios"}],
            [{"text": "📅 Semanal",       "callback_data": "hab_semanal"}],
        ]}
    )

# ── Callbacks ──────────────────────────────────────────────────────────────────
def handle_callback(update):
    cb          = update["callback_query"]
    data        = cb["data"]
    callback_id = cb["id"]
    chat_id     = cb["message"]["chat"]["id"]
    message_id  = cb["message"]["message_id"]
    original    = cb["message"].get("text", "")

    answer_callback(callback_id)

    # ── Flujos activos primero ─────────────────────────────────────────────────
    if session.get("flow") in ("new_habit", "set_reminder"):
        if handle_habit_flow_callback(data):
            return

    # ── Menú principal ─────────────────────────────────────────────────────────
    if data == "menu_finanzas":
        send_finance_submenu()
        return
    if data == "menu_habitos":
        send_habitos_submenu()
        return
    if data == "menu_personas":
        send_message(
            "👥 *Personas*\n\n"
            "`/persona add Nombre — lo que sabes`\n"
            "`/persona info Nombre`\n"
            "`/persona suggest Nombre`\n"
            "`/persona list`"
        )
        return

    # ── Submenú Finanzas ───────────────────────────────────────────────────────
    if data == "fin_registrar":
        send_message(
            "💸 *Registrar gasto*\n\n"
            "📸 Foto del ticket — mándala directo\n"
            "`/gasto 250 comida_fuera BBVA_Gold Tacos`"
        )
        return
    if data == "fin_ciclo":
        handle_gastos_resumen()
        return
    if data == "fin_cats":
        handle_gastos_por_categoria()
        return
    if data == "fin_consultar":
        session["flow"]      = "fin_query"
        session["flow_step"] = 0
        send_message("💬 ¿Qué quieres saber de tus gastos?\nEj: ¿cuánto gasté en comida este ciclo?")
        return

    # ── Submenú Hábitos ────────────────────────────────────────────────────────
    if data == "hab_checkin":
        block = "morning" if now_mx().hour < 15 else "night"
        start_checkin(block)
        return
    if data == "hab_progreso":
        send_resumen()
        return
    if data == "hab_nuevo":
        start_new_habit_flow()
        return
    if data == "hab_recordatorios":
        show_reminders_menu()
        return
    if data == "hab_semanal":
        send_weekly_analysis()
        return

    # ── Callbacks de finanzas (foto, confirmación de gasto) ───────────────────
    if handle_finance_callback(data):
        return

    # ── Callbacks de hábitos (done_, skip_, partial_) ─────────────────────────
    handle_habit_callback(data, chat_id, message_id, original)

# ── Mensajes de texto ──────────────────────────────────────────────────────────
def handle_message(update):
    msg     = update.get("message", {})
    text    = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if chat_id != str(CHAT_ID):
        return

    # Flujos activos
    flow = session.get("flow")
    if flow == "fin_query":
        handle_finance_query(text)
        session["flow"] = None
        return
    if flow in ("new_habit", "set_reminder"):
        handle_habit_flow_text(text)
        return

    # Comandos
    if text == "/start" or text == "/menu":
        send_menu()
    elif text == "/checkin":
        block = "morning" if now_mx().hour < 15 else "night"
        start_checkin(block)
    elif text == "/resumen":
        send_resumen()
    elif text == "/racha":
        send_rachas()
    elif text == "/gastos":
        handle_gastos_resumen()
    elif text.startswith("/gasto "):
        handle_gasto_command(text)
    elif text == "/semanal":
        send_weekly_analysis()
    elif text.startswith("/persona"):
        send_message(handle_persona_command(text))
    elif text == "/test_gasto":
        send_message("🔍 Diagnosticando conexión Supabase...")
        send_message(test_supabase_connection())
    elif not text.startswith("/"):
        all_state = get_all_state()
        send_message(ai_answer_question(text, all_state))

# ── Scheduler ─────────────────────────────────────────────────────────────────
_reminder_cache: list  = []
_reminder_cache_ts: float = 0.0

def _get_reminders():
    global _reminder_cache, _reminder_cache_ts
    if time.time() - _reminder_cache_ts > 300:
        try:
            _reminder_cache    = sb_get("bot_reminders", "active=eq.true&select=*") or []
            _reminder_cache_ts = time.time()
        except Exception as e:
            print(f"[reminders cache] {e}", flush=True)
    return _reminder_cache

def _fire_db_reminders(h, m):
    for rem in _get_reminders():
        if rem.get("hour") == h and rem.get("minute") == m:
            key    = rem.get("habit_key")
            habits = get_habits()
            habit  = next((hb for hb in habits if hb["key"] == key), None)
            if habit:
                session["pending"] = [habit]
                session["results"] = {}
                session["block"]   = habit.get("block", "morning")
                session["flow"]    = None
                ask_next_habit()

def scheduler_loop():
    _last_fired_minute = (-1, -1)

    while True:
        now = now_mx()
        h, m = now.hour, now.minute

        if (h, m) != _last_fired_minute:
            _last_fired_minute = (h, m)

            # 7:30 — Menú de buenos días
            if h == 7 and m == 30:
                send_menu()

            # 8:00 — Check-in cama
            elif h == 8 and m == 0:
                habits = get_habits("morning")
                cama = next((hb for hb in habits if hb["key"] == "cama"), None)
                if cama:
                    session["pending"] = [cama]
                    session["results"] = {}
                    session["block"]   = "morning"
                    session["flow"]    = None
                    ask_next_habit()

            # 9:00 — Recordatorio cama + cierre de ciclo día 19
            elif h == 9 and m == 0:
                all_state = get_all_state()
                cama = next((s for s in all_state if s.get("key") == "cama"), None)
                if cama and not cama.get("done_today"):
                    send_message(
                        "👀 ¿Ya tendiste la cama?",
                        {"inline_keyboard": [[
                            {"text": "✅ Ya", "callback_data": "done_cama"},
                            {"text": "❌ No", "callback_data": "skip_cama"},
                        ]]}
                    )
                if now.day == 19:
                    send_monthly_finance_analysis()

            # 20:00 — Alertas (L-S) o análisis semanal (DOM)
            elif h == 20 and m == 0:
                if now.weekday() == 6:
                    send_weekly_analysis()
                else:
                    check_smart_alerts()

            # 21:00 — Check-in ejercicio
            elif h == 21 and m == 0:
                habits = get_habits("night")
                ejercicio = next((hb for hb in habits if hb["key"] == "ejercicio"), None)
                if ejercicio:
                    session["pending"] = [ejercicio]
                    session["results"] = {}
                    session["block"]   = "night"
                    session["flow"]    = None
                    ask_next_habit()

            # 22:00 — Check-in comida
            elif h == 22 and m == 0:
                habits = get_habits("night")
                comida = next((hb for hb in habits if hb["key"] == "comida"), None)
                if comida:
                    session["pending"] = [comida]
                    session["results"] = {}
                    session["block"]   = "night"
                    session["flow"]    = None
                    ask_next_habit()

            # Recordatorios configurados en BD (cualquier hora/minuto)
            _fire_db_reminders(h, m)

        time.sleep(30)

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🤖 Hábit bot iniciando...", flush=True)

    try:
        r = requests.get(f"{BASE_URL}/getMe")
        print(f"✅ Telegram: @{r.json()['result']['username']}", flush=True)
    except Exception as e:
        print(f"❌ Telegram: {e}", flush=True)

    try:
        habits = get_habits()
        print(f"✅ Supabase: {len(habits)} hábitos", flush=True)
    except Exception as e:
        print(f"❌ Supabase: {e}", flush=True)

    threading.Thread(target=scheduler_loop, daemon=True).start()

    offset = None
    try:
        old = get_updates(None)
        if old:
            offset = old[-1]["update_id"] + 1
            print(f"🧹 {len(old)} updates viejos ignorados", flush=True)
    except:
        pass

    print("✅ Esperando mensajes...", flush=True)
    processed = set()

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
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
                    msg = update.get("message", {})
                    requests.post(f"{BASE_URL}/sendChatAction",
                                  json={"chat_id": CHAT_ID, "action": "typing"})
                    if "photo" in msg:
                        handle_photo(update)
                    else:
                        handle_message(update)
        except Exception as e:
            print(f"Error loop: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
