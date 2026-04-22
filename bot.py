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
    send_message, answer_callback, get_updates,
)
from habitos import (
    get_habits, start_checkin, ask_next_habit,
    send_resumen, send_rachas, send_weekly_analysis,
    check_smart_alerts, handle_habit_callback,
)
from finanzas import (
    handle_photo, handle_gasto_command, handle_gastos_resumen,
    handle_finance_callback, send_monthly_finance_analysis,
    test_supabase_connection,
)
from asistente import handle_persona_command, ai_answer_question

# ── Menú principal ─────────────────────────────────────────────────────────────
def send_menu():
    hour = datetime.now().hour
    greeting = "☀️ *Buenos días*" if hour < 12 else ("🌤 *Buenas tardes*" if hour < 19 else "🌙 *Buenas noches*")
    keyboard = {"inline_keyboard": [
        [{"text": "📋 Check-in hábitos", "callback_data": "menu_checkin"},
         {"text": "📊 Mi progreso",       "callback_data": "menu_resumen"}],
        [{"text": "💸 Registrar gasto",   "callback_data": "menu_gasto"},
         {"text": "📈 Gastos del mes",    "callback_data": "menu_gastos_mes"}],
        [{"text": "👤 Agregar persona",   "callback_data": "menu_add_person"},
         {"text": "🔍 Buscar persona",    "callback_data": "menu_lookup_person"}],
    ]}
    send_message(f"{greeting}, Yair", keyboard)

# ── Callbacks ──────────────────────────────────────────────────────────────────
def handle_callback(update):
    cb          = update["callback_query"]
    data        = cb["data"]
    callback_id = cb["id"]
    chat_id     = cb["message"]["chat"]["id"]
    message_id  = cb["message"]["message_id"]
    original    = cb["message"].get("text", "")

    answer_callback(callback_id)

    # ── Menú ──────────────────────────────────────────────────────────────────
    if data.startswith("menu_"):
        if data == "menu_checkin":
            block = "morning" if datetime.now().hour < 15 else "night"
            start_checkin(block)
        elif data == "menu_resumen":
            send_resumen()
        elif data == "menu_gastos_mes":
            handle_gastos_resumen()
        elif data == "menu_gasto":
            send_message(
                "💸 *Registrar gasto*\n\n"
                "📸 Foto del ticket — mándala directo\n"
                "`/gasto 250 comida_fuera BBVA_Gold Tacos`"
            )
        elif data == "menu_add_person":
            send_message("👤 `/persona add Nombre — lo que sabes de esa persona`")
        elif data == "menu_lookup_person":
            send_message("🔍 `/persona info Nombre` · `/persona suggest Nombre` · `/persona list`")
        return

    # ── Finanzas ──────────────────────────────────────────────────────────────
    if handle_finance_callback(data):
        return

    # ── Hábitos ───────────────────────────────────────────────────────────────
    handle_habit_callback(data, chat_id, message_id, original)

# ── Mensajes de texto ──────────────────────────────────────────────────────────
def handle_message(update):
    msg     = update.get("message", {})
    text    = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if chat_id != str(CHAT_ID):
        return

    if text == "/start":
        send_menu()
    elif text == "/checkin":
        block = "morning" if datetime.now().hour < 15 else "night"
        start_checkin(block)
    elif text == "/resumen":
        send_resumen()
    elif text == "/racha":
        send_rachas()
    elif text == "/gastos":
        handle_gastos_resumen()
    elif text.startswith("/gasto"):
        handle_gasto_command(text)
    elif text == "/semanal":
        send_weekly_analysis()
    elif text.startswith("/persona"):
        send_message(handle_persona_command(text))
    elif text == "/test_gasto":
        send_message("🔍 Diagnosticando conexión Supabase...")
        resultado = test_supabase_connection()
        send_message(resultado)
    elif not text.startswith("/"):
        all_state = get_all_state()
        send_message(ai_answer_question(text, all_state))

# ── Scheduler ─────────────────────────────────────────────────────────────────
def scheduler_loop():
    while True:
        now = datetime.now()
        h, m = now.hour, now.minute

        # 7:30 — Menú de buenos días
        if h == 7 and m == 30:
            send_menu()
            time.sleep(61)

        # 8:00 — Check-in cama
        elif h == 8 and m == 0:
            habits = get_habits("morning")
            cama = next((hb for hb in habits if hb["key"] == "cama"), None)
            if cama:
                session["pending"] = [cama]
                session["results"] = {}
                session["block"] = "morning"
                ask_next_habit()
            time.sleep(61)

        # 9:00 — Recordatorio si no tendió cama
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
            time.sleep(61)

        # 21:00 — Check-in ejercicio
        elif h == 21 and m == 0:
            habits = get_habits("night")
            ejercicio = next((hb for hb in habits if hb["key"] == "ejercicio"), None)
            if ejercicio:
                session["pending"] = [ejercicio]
                session["results"] = {}
                session["block"] = "night"
                ask_next_habit()
            time.sleep(61)

        # 22:00 — Check-in comida
        elif h == 22 and m == 0:
            habits = get_habits("night")
            comida = next((hb for hb in habits if hb["key"] == "comida"), None)
            if comida:
                session["pending"] = [comida]
                session["results"] = {}
                session["block"] = "night"
                ask_next_habit()
            time.sleep(61)

        # 20:00 L-S — Alertas inteligentes
        elif h == 20 and m == 0 and now.weekday() != 6:
            check_smart_alerts()
            time.sleep(61)

        # 20:00 DOM — Análisis semanal
        elif h == 20 and m == 0 and now.weekday() == 6:
            send_weekly_analysis()
            time.sleep(61)

        # Día 19, 9:00 — Cierre de ciclo BBVA
        elif h == 9 and m == 0 and now.day == 19:
            send_monthly_finance_analysis()
            time.sleep(61)

        else:
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

    # Limpiar updates viejos
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
