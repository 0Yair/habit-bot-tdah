"""
HÁBITOS — Check-ins, rachas, alertas, análisis semanal.
Mensajes cortos: máx 2 líneas en preguntas, 1 línea en reacciones.
"""
import time
from datetime import datetime, date, timedelta
from shared import session, sb_get, sb_post, sb_patch, get_all_state, send_message, edit_message, ai_call

# ── Supabase ──────────────────────────────────────────────────────────────────
def get_habits(block=None):
    params = "select=*"
    if block:
        params += f"&block=eq.{block}"
    return sb_get("habits", params)

def log_habit(habit_key, done, week_level, note=None):
    sb_post("habit_logs", {
        "habit_key": habit_key,
        "done": done,
        "week_level": week_level,
        "note": note,
        "logged_at": datetime.now().isoformat(),
    })
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
    week = habit.get("current_week", 1)
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
    """Pregunta de check-in: máx 2 líneas, directo."""
    week_label = get_week_label(habit)
    streak = habit.get("streak", 0)
    return ai_call(
        f"Coach TDAH. Español mexicano. Una sola pregunta directa (máx 2 líneas, 1 emoji).\n"
        f"Hábito: {habit.get('emoji','')} {habit.get('name','')} | Nivel: {week_label} | Racha: {streak}d\n"
        f"Solo pregunta si lo hizo hoy. Sin intro ni relleno.",
        max_tokens=80,
    )

def ai_reaction(habit, done, streak):
    """Reacción tras responder: 1 línea, sin relleno."""
    return ai_call(
        f"Coach TDAH. 1 línea, 1 emoji máximo. Sin frase genérica.\n"
        f"{'Completó ✅' if done else 'No completó ❌'}: {habit.get('emoji','')} {habit.get('name','')} | Racha: {streak}d",
        max_tokens=50,
    )

def ai_daily_summary(results):
    """Resumen del día: máx 3 líneas."""
    done = sum(1 for v in results.values() if v)
    total = len(results)
    completados = [k for k, v in results.items() if v]
    no = [k for k, v in results.items() if not v]
    return ai_call(
        f"Coach TDAH. Resumen del día en máx 3 líneas. Honesto, sin relleno.\n"
        f"{done}/{total} hábitos. Hizo: {completados}. No hizo: {no}",
        max_tokens=120,
    )

def ai_weekly_analysis(done_by_habit):
    """Análisis semanal: máx 6 líneas estructuradas."""
    context = "\n".join([f"- {name}: {count}/7" for name, count in done_by_habit.items()])
    best = max(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)
    worst = min(done_by_habit.items(), key=lambda x: x[1]) if done_by_habit else ("ninguno", 0)
    return ai_call(
        f"Coach TDAH. Análisis semanal en máx 6 líneas. Usa *negritas* para títulos. Sin relleno.\n"
        f"Resultados:\n{context}\nMejor: {best[0]} ({best[1]}/7) | A reforzar: {worst[0]} ({worst[1]}/7)\n"
        f"Estructura: 1-Esta semana fuiste: 2-Lo que construiste: 3-Una sola cosa para esta semana:",
        max_tokens=250,
    )

# ── Flujo check-in ────────────────────────────────────────────────────────────
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

    if habit["key"] == "comida":
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Sí", "callback_data": f"done_{habit['key']}"},
            {"text": "〰️ +/−", "callback_data": f"partial_{habit['key']}"},
            {"text": "❌ No", "callback_data": f"skip_{habit['key']}"},
        ]]}
    else:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Sí", "callback_data": f"done_{habit['key']}"},
            {"text": "❌ No", "callback_data": f"skip_{habit['key']}"},
        ]]}
    send_message(msg, keyboard)
    session["waiting"] = True

def finish_checkin():
    summary = ai_daily_summary(session["results"])
    done = sum(1 for v in session["results"].values() if v)
    total = len(session["results"])
    filled = int((done / total) * 10) if total > 0 else 0
    bar = "█" * filled + "░" * (10 - filled)
    send_message(f"`{bar}` {done}/{total}\n\n{summary}")
    session["pending"] = []
    session["current"] = None
    session["waiting"] = False

def handle_habit_callback(data, chat_id, message_id, original_text):
    """Maneja done_, partial_, skip_. Retorna True si lo procesó."""
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

    # Releer streak actualizado
    state_list = sb_get("user_state", f"habit_key=eq.{habit_key}&select=streak")
    streak = state_list[0]["streak"] if state_list else habit.get("streak", 0)
    reaction = ai_reaction(habit, done, streak)

    level_up = f"\n🆙 Nivel {new_week} desde mañana." if new_week else ""
    icon = "✅" if done else "❌"
    edit_message(chat_id, message_id, f"{original_text}\n\n{icon} {reaction}{level_up}")

    time.sleep(1.2)
    ask_next_habit()
    return True

# ── Resumen rápido ────────────────────────────────────────────────────────────
def send_resumen():
    all_state = get_all_state()
    lines = ["📊 *Hoy*\n"]
    for h in all_state:
        icon = "✅" if h.get("done_today") else "⬜"
        lines.append(f"{icon} {h.get('emoji','')} {h.get('name','')} — {h.get('streak',0)}d")
    done = sum(1 for h in all_state if h.get("done_today"))
    total = len(all_state)
    filled = int((done / total) * 10) if total > 0 else 0
    bar = "█" * filled + "░" * (10 - filled)
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
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    logs_week = sb_get("habit_logs", f"logged_at=gte.{week_ago}&select=*")
    all_state = get_all_state()

    done_by_habit = {}
    for h in all_state:
        key = h.get("key")
        done_count = sum(1 for l in logs_week if l.get("habit_key") == key and l.get("done"))
        done_by_habit[h.get("name")] = done_count

    analysis = ai_weekly_analysis(done_by_habit)
    total_possible = len(all_state) * 7
    total_done = sum(1 for l in logs_week if l.get("done"))
    pct = round((total_done / total_possible * 100)) if total_possible > 0 else 0
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)

    send_message(f"📅 *Semana*\n`{bar}` {pct}%\n\n{analysis}")

# ── Alertas inteligentes ──────────────────────────────────────────────────────
def check_smart_alerts():
    today = date.today()
    alerts = []

    # Presupuesto
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
                    pct = spent / budget * 100
                    label = CATS_FINANCE.get(cat, cat)
                    if pct >= 100:
                        alerts.append(f"🚨 {label} agotado (${spent:,.0f}/${budget:,.0f})")
                    elif pct >= 80:
                        alerts.append(f"⚠️ {label} al {pct:.0f}%")
    except Exception as e:
        print(f"Error alertas presupuesto: {e}", flush=True)

    # Cortes y pagos
    for card_name, corte, pago in [("BBVA Gold", 18, 7), ("HSBC Volaris", 9, 28)]:
        day = today.day
        dc = corte - day if day <= corte else corte + 30 - day
        dp = pago - day if day <= pago else pago + 30 - day
        if 0 < dc <= 5:
            alerts.append(f"📅 {card_name} corta en {dc}d")
        if 0 < dp <= 3:
            alerts.append(f"💳 {card_name} pago en {dp}d")

    # Hábitos caídos
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
