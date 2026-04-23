"""
COMIDA — Plan alimenticio semanal, recordatorios por comida y registro diario.

Tablas Supabase necesarias:
  meal_plan: id bigint, day_of_week int, meal_type text, description text,
             hour int, minute int DEFAULT 0, active boolean DEFAULT true
  meal_logs: id bigint, date date, meal_type text, status text, logged_at timestamptz
"""
import time
from shared import session, sb_get, sb_post, sb_patch, send_message, now_mx

MEAL_TYPES = {
    "desayuno": {"emoji": "🥣", "label": "Desayuno", "hour": 8,  "minute": 0},
    "comida":   {"emoji": "🍽️", "label": "Comida",   "hour": 14, "minute": 0},
    "cena":     {"emoji": "🌙", "label": "Cena",     "hour": 20, "minute": 30},
}
DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_ICONS  = {"si": "✅", "parcial": "〰️", "no": "❌"}

# ── BD ────────────────────────────────────────────────────────────────────────
def get_today_plan() -> list:
    day = now_mx().weekday()   # 0 = Lunes … 6 = Domingo
    return sb_get("meal_plan", f"day_of_week=eq.{day}&active=eq.true&select=*")

def get_today_logs() -> list:
    today = now_mx().date().isoformat()
    return sb_get("meal_logs", f"date=eq.{today}&select=*")

def log_meal(meal_type: str, status: str):
    today = now_mx().date().isoformat()
    if sb_get("meal_logs", f"date=eq.{today}&meal_type=eq.{meal_type}&select=id"):
        sb_patch("meal_logs", f"date=eq.{today}&meal_type=eq.{meal_type}",
                 {"status": status, "logged_at": now_mx().isoformat()})
    else:
        sb_post("meal_logs", {
            "id":        int(time.time() * 1000),
            "date":      today,
            "meal_type": meal_type,
            "status":    status,
            "logged_at": now_mx().isoformat(),
        })

# ── Recordatorios ─────────────────────────────────────────────────────────────
def send_meal_reminder(meal_type: str):
    plan  = get_today_plan()
    entry = next((p for p in plan if p.get("meal_type") == meal_type), None)
    info  = MEAL_TYPES[meal_type]
    body  = f"\n_{entry['description']}_" if (entry and entry.get("description")) else ""
    send_message(
        f"{info['emoji']} *{info['label']}*{body}\n\n¿Lo hiciste?",
        {"inline_keyboard": [[
            {"text": "✅ Sí",        "callback_data": f"meal_si_{meal_type}"},
            {"text": "〰️ A medias", "callback_data": f"meal_parcial_{meal_type}"},
            {"text": "❌ No",        "callback_data": f"meal_no_{meal_type}"},
        ]]}
    )

def handle_meal_callback(data: str) -> bool:
    """Maneja meal_si_X, meal_parcial_X, meal_no_X."""
    if not data.startswith("meal_"):
        return False
    parts = data.split("_", 2)
    if len(parts) < 3:
        return False
    status, meal_type = parts[1], parts[2]
    if meal_type not in MEAL_TYPES or status not in ("si", "parcial", "no"):
        return False
    log_meal(meal_type, status)
    info = MEAL_TYPES[meal_type]
    send_message(f"{_ICONS[status]} {info['emoji']} *{info['label']}* registrado.")
    return True

# ── Check-in nocturno ─────────────────────────────────────────────────────────
def build_comida_checkin_msg() -> str:
    """Resumen del día para el check-in nocturno del hábito de comida."""
    logs     = get_today_logs()
    plan     = get_today_plan()
    log_map  = {l["meal_type"]: l["status"] for l in logs}
    plan_map = {p["meal_type"]: p.get("description", "") for p in plan}

    lines = ["🍴 *Comidas de hoy*"]
    for mt, info in MEAL_TYPES.items():
        icon   = _ICONS.get(log_map.get(mt), "⬜")
        desc   = plan_map.get(mt, "")
        suffix = f" — _{desc}_" if desc else ""
        lines.append(f"{icon} {info['emoji']} {info['label']}{suffix}")

    lines.append("\n¿Cómo te fue en general?")
    return "\n".join(lines)

# ── Vista del plan del día ────────────────────────────────────────────────────
def show_today_plan():
    plan     = get_today_plan()
    logs     = get_today_logs()
    log_map  = {l["meal_type"]: l["status"] for l in logs}
    day_name = DAYS_ES[now_mx().weekday()]
    kbd      = {"inline_keyboard": [[{"text": "✏️ Editar plan", "callback_data": "msetup_start"}]]}

    if not plan:
        send_message(f"📋 Sin plan para hoy (*{day_name}*).\nCrea uno con ✏️:", kbd)
        return

    lines = [f"🗓️ *{day_name}*\n"]
    for p in plan:
        mt   = p.get("meal_type", "")
        info = MEAL_TYPES.get(mt, {"emoji": "🍴", "label": mt})
        icon = _ICONS.get(log_map.get(mt), "⬜")
        lines.append(f"{icon} {info['emoji']} *{info['label']}* — {p.get('description', '')}")

    send_message("\n".join(lines), kbd)

# ── Setup del plan semanal ────────────────────────────────────────────────────
def start_meal_plan_setup():
    send_message("✏️ *Plan alimenticio*\n\n¿Cómo quieres armarlo?", {"inline_keyboard": [
        [{"text": "🔄 Igual toda la semana", "callback_data": "msetup_same"}],
        [{"text": "🗓️ Diferente cada día",   "callback_data": "msetup_daily"}],
        [{"text": "❌ Cancelar",              "callback_data": "msetup_cancel"}],
    ]})

def handle_meal_setup_callback(data: str) -> bool:
    flow = session.get("flow")

    if data == "msetup_start":
        start_meal_plan_setup()
        return True
    if data == "msetup_same":
        session.update(flow="meal_plan_setup", flow_step=0,
                       flow_data={"mode": "same", "meal_idx": 0, "entries": {}})
        _ask_step()
        return True
    if data == "msetup_daily":
        session.update(flow="meal_plan_setup", flow_step=0,
                       flow_data={"mode": "daily", "day": 0, "meal_idx": 0, "entries": {}})
        _ask_step()
        return True

    # Resto de callbacks solo durante el flujo activo
    if flow != "meal_plan_setup":
        return False

    if data == "msetup_skip":
        _advance(None)
        return True
    if data == "msetup_skip_day":
        d = session["flow_data"]
        d["day"]      = d.get("day", 0) + 1
        d["meal_idx"] = 0
        _ask_step()
        return True
    if data == "msetup_cancel":
        session["flow"] = None
        send_message("❌ Cancelado.")
        return True

    return False

def handle_meal_setup_text(text: str) -> bool:
    if session.get("flow") != "meal_plan_setup":
        return False
    _advance(text.strip())
    return True

# ── Internos del flujo ────────────────────────────────────────────────────────
def _ask_step():
    d        = session["flow_data"]
    mode     = d["mode"]
    meals    = list(MEAL_TYPES.keys())
    meal_idx = d.get("meal_idx", 0)
    day_idx  = d.get("day", 0)

    if mode == "same":
        if meal_idx >= len(meals):
            _save_plan()
            return
        info = MEAL_TYPES[meals[meal_idx]]
        send_message(
            f"{info['emoji']} *{info['label']}* — ¿Qué comes normalmente?",
            {"inline_keyboard": [[
                {"text": "⏭️ Saltar", "callback_data": "msetup_skip"},
                {"text": "❌ Cancelar", "callback_data": "msetup_cancel"},
            ]]}
        )
    else:
        if day_idx >= 7:
            _save_plan()
            return
        if meal_idx >= len(meals):
            d["meal_idx"] = 0
            d["day"]      = day_idx + 1
            _ask_step()
            return
        info = MEAL_TYPES[meals[meal_idx]]
        send_message(
            f"📅 *{DAYS_ES[day_idx]}* — {info['emoji']} {info['label']}\n¿Qué comes?",
            {"inline_keyboard": [[
                {"text": "⏭️ Saltar comida", "callback_data": "msetup_skip"},
                {"text": "📅 Saltar día",    "callback_data": "msetup_skip_day"},
                {"text": "❌ Cancelar",       "callback_data": "msetup_cancel"},
            ]]}
        )

def _advance(description):
    d        = session["flow_data"]
    mode     = d["mode"]
    meals    = list(MEAL_TYPES.keys())
    meal_idx = d.get("meal_idx", 0)

    if description:
        prefix = "all" if mode == "same" else str(d.get("day", 0))
        d["entries"][f"{prefix}_{meals[meal_idx]}"] = description

    if mode == "same":
        d["meal_idx"] = meal_idx + 1
    else:
        d["meal_idx"] = meal_idx + 1
        if d["meal_idx"] >= len(meals):
            d["meal_idx"] = 0
            d["day"]      = d.get("day", 0) + 1

    _ask_step()

def _save_plan():
    entries = session["flow_data"].get("entries", {})
    meals   = list(MEAL_TYPES.keys())

    # Desactivar plan anterior
    for day in range(7):
        for meal in meals:
            try:
                sb_patch("meal_plan", f"day_of_week=eq.{day}&meal_type=eq.{meal}", {"active": False})
            except Exception:
                pass

    saved = 0
    for key, desc in entries.items():
        prefix, meal_key = key.split("_", 1)
        days = range(7) if prefix == "all" else [int(prefix)]
        info = MEAL_TYPES.get(meal_key, {})
        for day in days:
            try:
                sb_post("meal_plan", {
                    "id":          int(time.time() * 1000) + day * 10 + meals.index(meal_key),
                    "day_of_week": day,
                    "meal_type":   meal_key,
                    "description": desc,
                    "hour":        info.get("hour", 12),
                    "minute":      info.get("minute", 0),
                    "active":      True,
                })
                saved += 1
                time.sleep(0.01)   # evitar IDs duplicados
            except Exception as e:
                print(f"[save_plan] {e}", flush=True)

    session["flow"] = None
    send_message(
        f"✅ Plan guardado — *{saved} comidas* en la semana.\n"
        f"Recordatorios automáticos: 🥣 8:00 · 🍽️ 14:00 · 🌙 20:30"
    )
