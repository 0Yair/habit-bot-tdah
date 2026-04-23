"""
HÁBITOS — Check-ins, rachas, alertas, análisis semanal, creación de hábitos, recordatorios.
Mensajes cortos: máx 2 líneas en preguntas, 1 línea en reacciones.
"""
import re, json, time
from datetime import datetime, date, timedelta
from shared import session, sb_get, sb_post, sb_patch, get_all_state, send_message, edit_message, ai_call, CHAT_ID

# ── Supabase ──────────────────────────────────────────────────────────────────
def get_habits(block=None):
    params = "select=*"
    if block:
        params += f"&block=eq.{block}"
    return sb_get("habits", params)

def log_habit(habit_key, done, week_level, note=None):
    sb_post("habit_logs", {
        "habit_key": habit_key,
        "done":      done,
        "week_level": week_level,
        "note":      note,
        "logged_at": datetime.now().isoformat(),
    })
    state_list = sb_get("user_state", f"habit_key=eq.{habit_key}&select=*")
    if not state_list:
        return
    state = state_list[0]
    today = date.today()
    last  = date.fromisoformat(state["last_logged"]) if state.get("last_logged") else None
    if done:
        new_streak = (state["streak"] + 1) if last and (today - last).days == 1 else 1
    else:
        new_streak = 0
    sb_patch("user_state", f"habit_key=eq.{habit_key}", {
        "streak":      new_streak,
        "best_streak": max(new_streak, state.get("best_streak", 0)),
        "last_logged": today.isoformat(),
        "updated_at":  datetime.now().isoformat(),
    })

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

def get_week_label(habit):
    levels = habit.get("week_levels", [])
    week   = habit.get("current_week", 1)
    active = levels[0] if levels else {"label": "?", "desc": ""}
    for lvl in levels:
        if week >= lvl.get("week", 1):
            active = lvl
    return f'{active.get("label","?")} — {active.get("desc","")}'

def build_context(habits_state):
    return "\n".join([
        f"- {h.get('emoji','')} {h.get('name','')}: racha={h.get('streak',0)}d"
        for h in habits_state
    ])

# ── IA (mensajes CORTOS) ──────────────────────────────────────────────────────
def ai_checkin_message(habit, all_state):
    week_label = get_week_label(habit)
    streak     = habit.get("streak", 0)
    return ai_call(
        f"Coach TDAH. Español mexicano. Una sola pregunta directa (máx 2 líneas, 1 emoji).\n"
        f"Hábito: {habit.get('emoji','')} {habit.get('name','')} | Nivel: {week_label} | Racha: {streak}d\n"
        f"Solo pregunta si lo hizo hoy. Sin intro ni relleno.",
        max_tokens=80,
    )

def ai_reaction(habit, done, streak):
    return ai_call(
        f"Coach TDAH. 1 línea, 1 emoji máximo. Sin frase genérica.\n"
        f"{'Completó ✅' if done else 'No completó ❌'}: {habit.get('emoji','')} {habit.get('name','')} | Racha: {streak}d",
        max_tokens=50,
    )

def ai_daily_summary(results):
    done   = sum(1 for v in results.values() if v)
    total  = len(results)
    hechos = [k for k, v in results.items() if v]
    no     = [k for k, v in results.items() if not v]
    return ai_call(
        f"Coach TDAH. Resumen del día en máx 3 líneas. Honesto, sin relleno.\n"
        f"{done}/{total} hábitos. Hizo: {hechos}. No hizo: {no}",
        max_tokens=120,
    )

def ai_weekly_analysis(done_by_habit):
    context = "\n".join([f"- {name}: {count}/7" for name, count in done_by_habit.items()])
    best  = max(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)
    worst = min(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)
    return ai_call(
        f"Coach TDAH. Análisis semanal en máx 6 líneas. Usa *negritas* para títulos. Sin relleno.\n"
        f"Resultados:\n{context}\nMejor: {best[0]} ({best[1]}/7) | A reforzar: {worst[0]} ({worst[1]}/7)\n"
        f"Estructura: 1-Esta semana fuiste: 2-Lo que construiste: 3-Una sola cosa para esta semana:",
        max_tokens=250,
    )

# ── Crear nuevo hábito ────────────────────────────────────────────────────────
def generate_habit_levels(name: str, emoji: str) -> list:
    raw = ai_call(
        f"Genera 6 niveles progresivos para el hábito '{emoji} {name}'.\n"
        f"Mejora gradual (~2% por semana, muy alcanzable para alguien con TDAH).\n"
        f"Responde SOLO JSON sin markdown:\n"
        f'[{{"week":1,"label":"NombreCorto","desc":"acción concreta"}}, ...]\n'
        f"Semanas: 1, 8, 15, 22, 29, 36. Labels en español (≤2 palabras). Descripciones ≤8 palabras.",
        max_tokens=400,
    )
    clean = raw.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def _habit_key_from_name(name: str) -> str:
    key = re.sub(r'[^a-z0-9]', '_', name.lower())
    key = re.sub(r'_+', '_', key).strip('_')[:20]
    existing = sb_get("habits", f"key=eq.{key}&select=key")
    if existing:
        key = f"{key}_{int(time.time()) % 10000}"
    return key

def _format_levels_preview(levels: list) -> str:
    if not levels:
        return "_Sin niveles_"
    lines = []
    for lvl in levels:
        lines.append(f"• Sem {lvl.get('week','?')}: *{lvl.get('label','?')}* — {lvl.get('desc','')}")
    return "\n".join(lines)

def start_new_habit_flow():
    session["flow"]      = "new_habit"
    session["flow_step"] = 0
    session["flow_data"] = {}
    send_message("➕ *Nuevo hábito*\n\n¿Cómo se llama? (ej: Ejercicio, Lectura, Meditación)")

def handle_habit_flow_text(text: str) -> bool:
    flow = session.get("flow")
    if flow not in ("new_habit", "set_reminder"):
        return False

    if flow == "new_habit":
        step = session["flow_step"]
        if step == 0:
            session["flow_data"]["name"] = text.strip()
            session["flow_step"] = 1
            send_message(
                f"¿En qué momento del día haces *{text.strip()}*?",
                {"inline_keyboard": [[
                    {"text": "☀️ Mañana", "callback_data": "hab_flow_block_morning"},
                    {"text": "🌙 Noche",  "callback_data": "hab_flow_block_night"},
                ]]}
            )
            return True
        if step == 2:
            session["flow_data"]["emoji"] = text.strip()
            _confirm_new_habit()
            return True

    if flow == "set_reminder":
        _save_reminder_from_text(text.strip())
        return True

    return False

def handle_habit_flow_callback(data: str) -> bool:
    flow = session.get("flow")
    if not flow:
        return False

    if flow == "new_habit":
        if data.startswith("hab_flow_block_"):
            session["flow_data"]["block"] = data[15:]
            session["flow_step"] = 2
            name = session["flow_data"].get("name", "el hábito")
            send_message(
                f"¿Qué emoji representa *{name}*? Escríbelo o toca Saltar.",
                {"inline_keyboard": [[{"text": "⏭️ Saltar", "callback_data": "hab_flow_skip_emoji"}]]}
            )
            return True
        if data == "hab_flow_skip_emoji":
            session["flow_data"]["emoji"] = "⭐"
            _confirm_new_habit()
            return True
        if data == "hab_flow_confirm":
            _save_new_habit()
            return True
        if data == "hab_flow_cancel":
            session["flow"]      = None
            session["flow_data"] = {}
            send_message("❌ Cancelado.")
            return True

    if flow == "set_reminder":
        if data.startswith("rem_set_"):
            key    = data[8:]
            habits = get_habits()
            habit  = next((h for h in habits if h["key"] == key), None)
            if not habit:
                return True
            session["flow_step"] = 1
            session["flow_data"] = {"habit_key": key, "habit_name": habit.get("name", key)}
            send_message(
                f"⏰ Recordatorio para *{habit.get('name', key)}*\n"
                f"Escribe la hora (ej: `21:30`) o toca Quitar:",
                {"inline_keyboard": [[{"text": "🗑️ Quitar", "callback_data": "rem_delete"}]]}
            )
            return True
        if data == "rem_delete":
            key = session["flow_data"].get("habit_key")
            if key:
                sb_patch("bot_reminders", f"habit_key=eq.{key}", {"active": False})
            session["flow"] = None
            send_message("✅ Recordatorio eliminado.")
            return True

    return False

def _confirm_new_habit():
    data  = session["flow_data"]
    name  = data.get("name", "")
    emoji = data.get("emoji", "⭐")
    block = data.get("block", "morning")
    block_label = "☀️ Mañana" if block == "morning" else "🌙 Noche"

    send_message("⏳ Generando niveles progresivos...")
    try:
        levels = generate_habit_levels(name, emoji)
        session["flow_data"]["levels"] = levels
        preview = _format_levels_preview(levels)
        session["flow_step"] = 3
        send_message(
            f"{emoji} *{name}* · {block_label}\n\n*Tus 6 niveles:*\n{preview}",
            {"inline_keyboard": [[
                {"text": "✅ Guardar",   "callback_data": "hab_flow_confirm"},
                {"text": "❌ Cancelar", "callback_data": "hab_flow_cancel"},
            ]]}
        )
    except Exception as e:
        print(f"[generate_levels ERROR] {e}", flush=True)
        send_message("❌ Error generando niveles. Intenta de nuevo.")
        session["flow"] = None

def _save_new_habit():
    data   = session["flow_data"]
    name   = data.get("name", "Nuevo hábito")
    emoji  = data.get("emoji", "⭐")
    block  = data.get("block", "morning")
    levels = data.get("levels", [])

    key    = _habit_key_from_name(name)
    result = sb_post("habits", {
        "key":        key,
        "name":       name,
        "emoji":      emoji,
        "block":      block,
        "week_levels": levels,
    })
    if isinstance(result, dict) and result.get("code"):
        send_message(f"❌ Error al guardar: {result.get('message', '')}")
        session["flow"] = None
        return

    sb_post("user_state", {
        "habit_key":    key,
        "streak":       0,
        "best_streak":  0,
        "current_week": 1,
        "updated_at":   datetime.now().isoformat(),
    })

    session["flow"]      = None
    session["flow_data"] = {}
    send_message(
        f"✅ *{emoji} {name}* guardado.\n"
        f"Aparecerá en el {'check-in de mañana' if block == 'morning' else 'check-in nocturno'}. ¡Tú puedes!"
    )

def _save_reminder_from_text(text: str):
    key  = session["flow_data"].get("habit_key")
    name = session["flow_data"].get("habit_name", key)
    try:
        parts  = text.replace(".", ":").split(":")
        hour   = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except Exception:
        send_message("⚠️ Formato inválido. Escribe la hora así: `21:30`")
        return

    existing = sb_get("bot_reminders", f"habit_key=eq.{key}&select=id")
    if existing:
        sb_patch("bot_reminders", f"habit_key=eq.{key}", {"hour": hour, "minute": minute, "active": True})
    else:
        sb_post("bot_reminders", {"habit_key": key, "hour": hour, "minute": minute, "active": True})

    session["flow"] = None
    send_message(f"✅ Recordatorio para *{name}* a las {hour:02d}:{minute:02d}")

def show_reminders_menu():
    habits    = get_habits()
    reminders = sb_get("bot_reminders", "select=*") or []
    rem_map   = {r["habit_key"]: r for r in reminders}

    lines    = ["⏰ *Recordatorios*\nToca un hábito para configurar su hora.\n"]
    kbd_rows = []
    for hb in habits:
        key = hb["key"]
        rem = rem_map.get(key)
        t_str = f"{rem['hour']:02d}:{rem['minute']:02d}" if (rem and rem.get("active")) else "sin hora"
        lines.append(f"{hb.get('emoji','')} {hb['name']}: {t_str}")
        kbd_rows.append([{"text": f"{hb.get('emoji','')} {hb['name']}", "callback_data": f"rem_set_{key}"}])

    session["flow"]      = "set_reminder"
    session["flow_step"] = 0
    session["flow_data"] = {}
    send_message("\n".join(lines), {"inline_keyboard": kbd_rows})

# ── Flujo check-in ────────────────────────────────────────────────────────────
def start_checkin(block):
    habits = get_habits(block)
    if not habits:
        return
    session["block"]             = block
    session["pending"]           = list(habits)
    session["results"]           = {}
    session["waiting"]           = False
    session["flow"]              = None
    session["active_message_id"] = None   # nuevo check-in = mensaje nuevo
    session["active_chat_id"]    = None
    ask_next_habit()

def ask_next_habit():
    if not session["pending"]:
        finish_checkin()
        return
    habit = session["pending"][0]
    session["current"] = habit
    all_state = get_all_state()

    # Para "comida": mostrar resumen del día en vez de pregunta genérica de IA
    if habit["key"] == "comida":
        try:
            from comida import build_comida_checkin_msg
            msg = build_comida_checkin_msg()
        except Exception as e:
            print(f"[comida checkin] {e}", flush=True)
            msg = ai_checkin_message(habit, all_state)
    else:
        msg = ai_checkin_message(habit, all_state)

    if habit["key"] == "comida":
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Sí",   "callback_data": f"done_{habit['key']}"},
            {"text": "〰️ +/−", "callback_data": f"partial_{habit['key']}"},
            {"text": "❌ No",   "callback_data": f"skip_{habit['key']}"},
        ]]}
    else:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Sí", "callback_data": f"done_{habit['key']}"},
            {"text": "❌ No", "callback_data": f"skip_{habit['key']}"},
        ]]}

    mid = session.get("active_message_id")
    cid = session.get("active_chat_id") or CHAT_ID

    if mid:
        # Reusar el mismo mensaje — solo se edita el texto y los botones
        edit_message(cid, mid, msg, keyboard)
    else:
        # Primera pregunta del check-in: enviar mensaje nuevo y capturar su id
        result = send_message(msg, keyboard)
        try:
            session["active_message_id"] = result["result"]["message_id"]
            session["active_chat_id"]    = result["result"]["chat"]["id"]
        except Exception as e:
            print(f"[ask_next_habit] no se pudo capturar message_id: {e}", flush=True)

    session["waiting"] = True

def finish_checkin():
    summary = ai_daily_summary(session["results"])
    done    = sum(1 for v in session["results"].values() if v)
    total   = len(session["results"])
    filled  = int((done / total) * 10) if total > 0 else 0
    bar     = "█" * filled + "░" * (10 - filled)
    final   = f"`{bar}` {done}/{total}\n\n{summary}"

    mid = session.get("active_message_id")
    cid = session.get("active_chat_id") or CHAT_ID
    if mid:
        edit_message(cid, mid, final)   # sin teclado — check-in terminado
    else:
        send_message(final)

    session["pending"]           = []
    session["current"]           = None
    session["waiting"]           = False
    session["active_message_id"] = None
    session["active_chat_id"]    = None

def handle_habit_callback(data, chat_id, message_id, original_text):
    if data.startswith("done_"):
        habit_key, done, note = data[5:], True, "sí"
    elif data.startswith("partial_"):
        habit_key, done, note = data[8:], True, "más o menos"
    elif data.startswith("skip_"):
        habit_key, done, note = data[5:], False, "no"
    else:
        return False

    all_state = get_all_state()
    habit = next((h for h in all_state if h.get("key") == habit_key), None)
    if not habit:
        return True

    week_level = habit.get("current_week", 1)
    log_habit(habit_key, done, week_level, note)
    new_week = advance_week_if_needed(habit_key)

    session["results"][habit_key] = done
    session["pending"] = [h for h in session["pending"] if h.get("key") != habit_key]

    # Guardar el mensaje activo (para que ask_next_habit lo reutilice)
    session["active_message_id"] = message_id
    session["active_chat_id"]    = chat_id

    state_list = sb_get("user_state", f"habit_key=eq.{habit_key}&select=streak")
    streak   = state_list[0]["streak"] if state_list else habit.get("streak", 0)
    reaction = ai_reaction(habit, done, streak)

    level_up = f"\n🆙 Nivel {new_week} desde mañana." if new_week else ""
    icon = "✅" if done else "❌"
    # Mostrar brevemente la reacción en el mismo mensaje
    edit_message(chat_id, message_id, f"{icon} {reaction}{level_up}")

    time.sleep(1.2)
    # ask_next_habit edita ese mismo mensaje con la siguiente pregunta (o el resumen)
    ask_next_habit()
    return True

# ── Resumen rápido ────────────────────────────────────────────────────────────
def send_resumen():
    all_state = get_all_state()
    lines = ["📊 *Hoy*\n"]
    for h in all_state:
        icon = "✅" if h.get("done_today") else "⬜"
        lines.append(f"{icon} {h.get('emoji','')} {h.get('name','')} — {h.get('streak',0)}d")
    done   = sum(1 for h in all_state if h.get("done_today"))
    total  = len(all_state)
    filled = int((done / total) * 10) if total > 0 else 0
    bar    = "█" * filled + "░" * (10 - filled)
    lines.append(f"\n`{bar}` {done}/{total}")
    send_message("\n".join(lines))

def send_rachas():
    all_state = get_all_state()
    lines = ["🔥 *Rachas*\n"]
    for h in all_state:
        fire = "🔥" if h.get("streak", 0) >= 5 else "  "
        lines.append(
            f"{fire} {h.get('emoji','')} {h.get('name','')}: "
            f"{h.get('streak',0)}d | mejor {h.get('best_streak',0)}d | nv {h.get('current_week',1)}/6"
        )
    send_message("\n".join(lines))

# ── Análisis semanal ──────────────────────────────────────────────────────────
def send_weekly_analysis():
    today     = date.today()
    week_ago  = (today - timedelta(days=7)).isoformat()
    logs_week = sb_get("habit_logs", f"logged_at=gte.{week_ago}&select=*")
    all_state = get_all_state()

    done_by_habit = {}
    for h in all_state:
        key        = h.get("key")
        done_count = sum(1 for l in logs_week if l.get("habit_key") == key and l.get("done"))
        done_by_habit[h.get("name")] = done_count

    analysis       = ai_weekly_analysis(done_by_habit)
    total_possible = len(all_state) * 7
    total_done     = sum(1 for l in logs_week if l.get("done"))
    pct    = round((total_done / total_possible * 100)) if total_possible > 0 else 0
    filled = int(pct / 10)
    bar    = "█" * filled + "░" * (10 - filled)
    send_message(f"📅 *Semana*\n`{bar}` {pct}%\n\n{analysis}")

# ── Alertas inteligentes ──────────────────────────────────────────────────────
def check_smart_alerts():
    today  = date.today()
    alerts = []

    try:
        from finanzas import get_bbva_cycle, CATS_FINANCE
        budgets_raw = sb_get("budgets", "id=eq.1&select=data")
        if budgets_raw and budgets_raw[0].get("data"):
            budgets = budgets_raw[0]["data"]
            cycle_start, cycle_end = get_bbva_cycle(today)
            exps = sb_get("expenses", f"date=gte.{cycle_start.isoformat()}&date=lte.{cycle_end.isoformat()}&select=category,amount")
            by_cat = {}
            for e in exps:
                if e.get("amount", 0) < 0:
                    cat = e.get("category", "otro")
                    by_cat[cat] = by_cat.get(cat, 0) + abs(e["amount"])
            for cat, spent in by_cat.items():
                budget = budgets.get(cat, 0)
                if budget > 0:
                    pct   = spent / budget * 100
                    label = CATS_FINANCE.get(cat, cat)
                    if pct >= 100:
                        alerts.append(f"🚨 {label} agotado (${spent:,.0f}/${budget:,.0f})")
                    elif pct >= 80:
                        alerts.append(f"⚠️ {label} al {pct:.0f}%")
    except Exception as e:
        print(f"Error alertas presupuesto: {e}", flush=True)

    for card_name, corte, pago in [("BBVA Gold", 18, 7), ("HSBC Volaris", 9, 28)]:
        day = today.day
        dc  = corte - day if day <= corte else corte + 30 - day
        dp  = pago  - day if day <= pago  else pago  + 30 - day
        if 0 < dc <= 5:
            alerts.append(f"📅 {card_name} corta en {dc}d")
        if 0 < dp <= 3:
            alerts.append(f"💳 {card_name} pago en {dp}d")

    try:
        all_state = get_all_state()
        for h in all_state:
            if h.get("streak", 0) == 0:
                logs = sb_get("habit_logs", f"habit_key=eq.{h['key']}&done=eq.false&select=logged_at&order=logged_at.desc&limit=3")
                if len(logs) >= 3:
                    alerts.append(f"💤 {h.get('emoji','')} {h.get('name','')} — {len(logs)}+ días sin hacerlo")
    except Exception as e:
        print(f"Error alertas hábitos: {e}", flush=True)

    if alerts:
        send_message("🔔 *Alertas*\n\n" + "\n".join(alerts))
