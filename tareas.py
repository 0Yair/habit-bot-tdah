"""
TAREAS — Gestión de pendientes con prioridades, fechas límite y recurrencia.
"""
import json
import time
from datetime import date, timedelta

from shared import (
    ai_call, now_mx, sb_get, sb_patch, sb_post, send_message, session,
)

# ── Constantes ────────────────────────────────────────────────────────────────
PRIO_ICONS  = {"alta": "🔴", "media": "🟡", "baja": "🟢"}
PRIO_ORDER  = {"alta": 0, "media": 1, "baja": 2}
CAT_LABELS  = {
    "trabajo":    "💼 Trabajo",
    "personas":   "👥 Personas",
    "meditacion": "🧘 Meditación",
    "salud":      "❤️ Salud",
    "otro":       "📌 Otro",
}

# ── DB helpers ────────────────────────────────────────────────────────────────
def _get_pending_tasks() -> list:
    return sb_get("tasks", "status=eq.pending&select=*") or []

def _get_projects() -> list:
    return sb_get("tasks", "is_project=eq.true&status=eq.pending&select=*") or []

def _save_task(data: dict):
    data.setdefault("id", int(time.time() * 1000))
    data.setdefault("created_at", now_mx().isoformat())
    data.setdefault("last_reminded_at", now_mx().isoformat())
    data.setdefault("status", "pending")
    sb_post("tasks", data)

def _complete_task(task_id: int):
    rows = sb_get("tasks", f"id=eq.{task_id}&select=*")
    if not rows:
        return
    task = rows[0]
    sb_patch("tasks", f"id=eq.{task_id}",
             {"status": "done", "done_at": now_mx().isoformat()})
    rec = task.get("recurrence")
    if rec:
        delta   = timedelta(weeks=1) if rec == "weekly" else timedelta(days=30)
        new_due = None
        if task.get("due_date"):
            new_due = (date.fromisoformat(task["due_date"]) + delta).isoformat()
        _save_task({
            "title":          task["title"],
            "category":       task.get("category"),
            "priority":       task.get("priority", "media"),
            "due_date":       new_due,
            "recurrence":     rec,
            "follow_up_days": task.get("follow_up_days", 4),
            "parent_id":      task.get("parent_id"),
            "is_project":     task.get("is_project", False),
        })

# ── Sorting & formatting ──────────────────────────────────────────────────────
def _sort_tasks(tasks: list) -> list:
    today = now_mx().date()

    def _key(t):
        due = t.get("due_date")
        if due:
            days_left = (date.fromisoformat(due) - today).days
            if days_left <= 3:
                tier, date_score = 0, days_left       # urgente (incluye vencidas)
            else:
                tier, date_score = 1, days_left       # tiene fecha pero no urgente
        else:
            tier, date_score = 1, 9999                # sin fecha

        prio_score = PRIO_ORDER.get(t.get("priority", "media"), 1)
        return (tier, date_score, prio_score, t.get("created_at", ""))

    return sorted(tasks, key=_key)

def _format_task_line(task: dict) -> str:
    prio  = PRIO_ICONS.get(task.get("priority", "media"), "🟡")
    title = task.get("title", "")
    due   = task.get("due_date")
    rec   = task.get("recurrence")

    if due:
        days_left = (date.fromisoformat(due) - now_mx().date()).days
        if days_left < 0:
            suffix = " · ⚠️ vencida"
        elif days_left == 0:
            suffix = " · vence hoy"
        elif days_left == 1:
            suffix = " · mañana"
        elif days_left <= 7:
            suffix = f" · {days_left}d"
        else:
            suffix = f" · {due}"
    elif rec == "weekly":
        suffix = " · 🔁 semanal"
    elif rec == "monthly":
        suffix = " · 🔁 mensual"
    else:
        suffix = ""

    return f"{prio} {title}{suffix}"


# ── AI ────────────────────────────────────────────────────────────────────────
def _is_task_intent(text: str) -> bool:
    """True si el texto parece una intención de agregar tarea."""
    try:
        result = ai_call(
            "Clasifica este texto. Responde SOLO con una palabra: 'task' o 'question'.\n"
            "'task' = quiere agregar un pendiente o tarea\n"
            "'question' = pregunta sobre estado, información o conversación\n\n"
            f"Texto: {text}",
            max_tokens=5,
        )
        return result.strip().lower().startswith("task")
    except Exception:
        return False


def _ai_parse_task(text: str) -> dict:
    """Extrae campos de tarea desde texto libre. Devuelve dict con fallback seguro."""
    today = now_mx().date().isoformat()
    raw = ai_call(
        f"Extrae info de esta tarea. HOY es {today}. "
        "Responde SOLO con JSON sin markdown:\n"
        '{"title":"texto limpio","category":"trabajo|personas|meditacion|salud|otro",'
        '"priority":"alta|media|baja","due_date":"YYYY-MM-DD o null",'
        '"recurrence":"weekly|monthly|null"}\n\n'
        f"Texto: {text}",
        max_tokens=120,
    )
    try:
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        data  = json.loads(clean)
        if data.get("due_date") in ("null", "", None):
            data["due_date"] = None
        if data.get("recurrence") in ("null", "", None):
            data["recurrence"] = None
        return data
    except Exception:
        return {"title": text, "category": "otro", "priority": "media",
                "due_date": None, "recurrence": None}


def _parse_date(text: str):
    """Convierte texto a fecha ISO. Intenta: ISO → palabras clave → IA. Devuelve None si falla."""
    # 1. ISO directo
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    # 2. Palabras clave en español
    try:
        today  = now_mx().date()
        lower  = text.lower().strip()
        _days  = {
            "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
            "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
        }
        if lower in _days:
            delta = (_days[lower] - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
        if "mañana" in lower:
            return (today + timedelta(days=1)).isoformat()
        if "pasado mañana" in lower:
            return (today + timedelta(days=2)).isoformat()
    except Exception:
        pass
    # 3. IA como último recurso
    try:
        today_str = now_mx().date().isoformat()
        result = ai_call(
            f"HOY es {today_str}. Convierte '{text}' a fecha ISO YYYY-MM-DD. "
            "Responde SOLO la fecha, sin texto extra. Si no puedes, responde 'null'.",
            max_tokens=15,
        )
        result = result.strip()
        if result != "null":
            date.fromisoformat(result)  # valida formato
            return result
    except Exception:
        pass
    return None


# ── Flujo por texto libre ─────────────────────────────────────────────────────
def handle_tareas_text(text: str) -> bool:
    """Devuelve True si detectó intención de tarea y mostró confirmación."""
    if not _is_task_intent(text):
        return False
    task = _ai_parse_task(text)
    session["pending_task"] = task
    _send_task_confirmation(task)
    return True


def _send_task_confirmation(task: dict):
    cat       = CAT_LABELS.get(task.get("category", "otro"), "📌 Otro")
    prio      = PRIO_ICONS.get(task.get("priority", "media"), "🟡")
    prio_name = task.get("priority", "media").capitalize()
    parts     = [cat, f"{prio} {prio_name}"]
    if task.get("due_date"):
        parts.append(f"📅 {task['due_date']}")
    if task.get("recurrence") == "weekly":
        parts.append("🔁 Semanal")
    elif task.get("recurrence") == "monthly":
        parts.append("🔁 Mensual")
    meta = "  |  ".join(parts)
    send_message(
        f"📌 *Nueva tarea detectada*\n\n📝 {task.get('title', '')}\n{meta}",
        {"inline_keyboard": [
            [{"text": "✅ Guardar",          "callback_data": "task_confirm"},
             {"text": "❌ Cancelar",          "callback_data": "task_cancel"}],
            [{"text": "✏️ Cambiar prioridad", "callback_data": "task_edit_prio"},
             {"text": "📂 Cambiar categoría", "callback_data": "task_edit_cat"}],
        ]},
    )


# ── Flujo guiado por botones ──────────────────────────────────────────────────
def start_task_add_flow():
    session["flow"]       = "task_add"
    session["task_draft"] = {}
    session["task_step"]  = "title"
    send_message(
        "➕ *Nueva tarea*\n\nEscribe el título:",
        {"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "task_flow_cancel"}]]},
    )


def handle_tareas_flow_text(text: str) -> bool:
    """Procesa texto durante el flujo guiado. Devuelve True si fue consumido."""
    if session.get("flow") != "task_add":
        return False
    step  = session.get("task_step")
    draft = session.setdefault("task_draft", {})

    if step == "title":
        draft["title"]       = text.strip()
        session["task_step"] = "category"
        _ask_category()
        return True
    if step == "due_date":
        draft["due_date"]    = _parse_date(text.strip())
        session["task_step"] = "recurrence"
        _ask_recurrence()
        return True
    return False


def _ask_category():
    send_message("📂 *Categoría:*", {"inline_keyboard": [
        [{"text": "💼 Trabajo",    "callback_data": "task_cat_trabajo"},
         {"text": "👥 Personas",   "callback_data": "task_cat_personas"}],
        [{"text": "🧘 Meditación", "callback_data": "task_cat_meditacion"},
         {"text": "❤️ Salud",      "callback_data": "task_cat_salud"}],
        [{"text": "📌 Otro",       "callback_data": "task_cat_otro"}],
        [{"text": "❌ Cancelar",   "callback_data": "task_flow_cancel"}],
    ]})


def _ask_priority():
    send_message("🎯 *Prioridad:*", {"inline_keyboard": [
        [{"text": "🔴 Alta",    "callback_data": "task_prio_alta"},
         {"text": "🟡 Media",   "callback_data": "task_prio_media"},
         {"text": "🟢 Baja",    "callback_data": "task_prio_baja"}],
        [{"text": "❌ Cancelar","callback_data": "task_flow_cancel"}],
    ]})


def _ask_has_due_date():
    send_message("📅 *¿Tiene fecha límite?*", {"inline_keyboard": [
        [{"text": "Sí", "callback_data": "task_hasdate_si"},
         {"text": "No", "callback_data": "task_hasdate_no"}],
        [{"text": "❌ Cancelar", "callback_data": "task_flow_cancel"}],
    ]})


def _ask_recurrence():
    send_message("🔁 *¿Se repite?*", {"inline_keyboard": [
        [{"text": "No se repite",   "callback_data": "task_rec_none"}],
        [{"text": "🔁 Cada semana", "callback_data": "task_rec_weekly"},
         {"text": "🔁 Cada mes",    "callback_data": "task_rec_monthly"}],
        [{"text": "❌ Cancelar",    "callback_data": "task_flow_cancel"}],
    ]})


def _ask_project():
    projects = _get_projects()
    rows = [[{"text": "Sin proyecto", "callback_data": "task_parent_none"}]]
    for p in projects[:5]:
        rows.append([{"text": f"📁 {p['title']}", "callback_data": f"task_parent_{p['id']}"}])
    rows.append([{"text": "❌ Cancelar", "callback_data": "task_flow_cancel"}])
    send_message("📁 *¿Es sub-tarea de un proyecto?*", {"inline_keyboard": rows})


def _flow_confirm_and_save():
    draft = session.get("task_draft", {})
    _save_task(draft)
    session["flow"]       = None
    session["task_draft"] = {}
    send_message(f"✅ Tarea guardada: *{draft.get('title', '')}*")


# ── Callbacks ─────────────────────────────────────────────────────────────────
def handle_tareas_callback(data: str) -> bool:
    """Maneja callbacks task_* y tarea_*. Devuelve True si fue consumido."""

    # Menú de tareas
    if data == "tarea_pending":
        show_pending_tasks(); return True
    if data == "tarea_projects":
        show_projects(); return True
    if data == "tarea_new":
        start_task_add_flow(); return True
    if data == "tarea_completed":
        _show_completed_tasks(); return True

    # Confirmación NL: guardar / cancelar
    if data == "task_confirm":
        task = session.pop("pending_task", None)
        if task:
            _save_task(task)
            send_message(f"✅ Tarea guardada: *{task.get('title', '')}*")
        return True
    if data == "task_cancel":
        session.pop("pending_task", None)
        send_message("❌ Cancelado.")
        return True

    # Confirmación NL: editar prioridad
    if data == "task_edit_prio":
        send_message("🎯 *Elige prioridad:*", {"inline_keyboard": [[
            {"text": "🔴 Alta",  "callback_data": "task_setprio_alta"},
            {"text": "🟡 Media", "callback_data": "task_setprio_media"},
            {"text": "🟢 Baja",  "callback_data": "task_setprio_baja"},
        ]]})
        return True
    if data == "task_edit_cat":
        items = list(CAT_LABELS.items())
        rows  = []
        for i in range(0, len(items), 2):
            row = [{"text": items[i][1], "callback_data": f"task_setcat_{items[i][0]}"}]
            if i + 1 < len(items):
                row.append({"text": items[i+1][1], "callback_data": f"task_setcat_{items[i+1][0]}"})
            rows.append(row)
        send_message("📂 *Elige categoría:*", {"inline_keyboard": rows})
        return True
    if data.startswith("task_setprio_"):
        prio = data[len("task_setprio_"):]
        if session.get("pending_task"):
            session["pending_task"]["priority"] = prio
            _send_task_confirmation(session["pending_task"])
        return True
    if data.startswith("task_setcat_"):
        cat = data[len("task_setcat_"):]
        if session.get("pending_task"):
            session["pending_task"]["category"] = cat
            _send_task_confirmation(session["pending_task"])
        return True

    # Flujo guiado — categoría
    if data.startswith("task_cat_"):
        session.setdefault("task_draft", {})["category"] = data[len("task_cat_"):]
        session["task_step"] = "priority"
        _ask_priority()
        return True
    # Flujo guiado — prioridad
    if data.startswith("task_prio_"):
        session.setdefault("task_draft", {})["priority"] = data[len("task_prio_"):]
        session["task_step"] = "has_due_date"
        _ask_has_due_date()
        return True
    # Flujo guiado — ¿tiene fecha?
    if data == "task_hasdate_si":
        session["task_step"] = "due_date"
        send_message("📅 ¿Para cuándo? Escribe la fecha:\n_(ej: viernes, 15 mayo, 2026-08-31)_")
        return True
    if data == "task_hasdate_no":
        session.setdefault("task_draft", {})["due_date"] = None
        session["task_step"] = "recurrence"
        _ask_recurrence()
        return True
    # Flujo guiado — recurrencia
    if data.startswith("task_rec_"):
        rec = data[len("task_rec_"):]
        session.setdefault("task_draft", {})["recurrence"] = None if rec == "none" else rec
        session["task_step"] = "project"
        _ask_project()
        return True
    # Flujo guiado — proyecto padre
    if data.startswith("task_parent_"):
        parent = data[len("task_parent_"):]
        session.setdefault("task_draft", {})["parent_id"] = None if parent == "none" else int(parent)
        _flow_confirm_and_save()
        return True
    # Flujo guiado — cancelar
    if data == "task_flow_cancel":
        session["flow"]       = None
        session["task_draft"] = {}
        send_message("❌ Cancelado.")
        return True

    # Acciones sobre tareas existentes
    if data.startswith("task_done_"):
        _complete_task(int(data[len("task_done_"):]))
        send_message("✅ ¡Tarea completada!")
        return True
    if data.startswith("task_snooze_"):
        task_id  = int(data[len("task_snooze_"):])
        tomorrow = (now_mx().date() + timedelta(days=1)).isoformat()
        sb_patch("tasks", f"id=eq.{task_id}", {"last_reminded_at": f"{tomorrow}T00:00:00"})
        send_message("⏭️ Te recuerdo mañana.")
        return True
    if data.startswith("task_del_"):
        task_id = int(data[len("task_del_"):])
        sb_patch("tasks", f"id=eq.{task_id}",
                 {"status": "done", "done_at": now_mx().isoformat()})
        send_message("🗑️ Tarea eliminada.")
        return True

    return False


# ── Vistas ────────────────────────────────────────────────────────────────────
def show_tareas_menu():
    send_message("📋 *Tareas*", {"inline_keyboard": [
        [{"text": "📋 Ver pendientes", "callback_data": "tarea_pending"},
         {"text": "🗂️ Proyectos",     "callback_data": "tarea_projects"}],
        [{"text": "➕ Nueva tarea",    "callback_data": "tarea_new"},
         {"text": "✅ Completadas",    "callback_data": "tarea_completed"}],
    ]})


def show_pending_tasks():
    tasks = [t for t in _sort_tasks(_get_pending_tasks()) if not t.get("is_project")]
    if not tasks:
        send_message("📋 Sin tareas pendientes. ¡Bien hecho! 🎉")
        return
    lines    = [f"📋 *Pendientes ({len(tasks)})*\n"]
    kbd_rows = []
    for i, t in enumerate(tasks[:8], 1):
        lines.append(f"{i}. {_format_task_line(t)}")
        kbd_rows.append([
            {"text": f"✅ {i}", "callback_data": f"task_done_{t['id']}"},
            {"text": f"⏭️ {i}", "callback_data": f"task_snooze_{t['id']}"},
            {"text": f"🗑️ {i}", "callback_data": f"task_del_{t['id']}"},
        ])
    send_message("\n".join(lines), {"inline_keyboard": kbd_rows})


def show_projects():
    projects = _get_projects()
    if not projects:
        send_message(
            "🗂️ Sin proyectos activos.\n\n"
            "Para crear uno, agrégalo como tarea y márcalo como proyecto."
        )
        return
    lines = ["🗂️ *Proyectos activos*\n"]
    for p in projects:
        n   = len(sb_get("tasks", f"parent_id=eq.{p['id']}&status=eq.pending&select=id") or [])
        due = f" · {p['due_date']}" if p.get("due_date") else ""
        s   = "s" if n != 1 else ""
        lines.append(f"📁 *{p['title']}*{due} · {n} pendiente{s}")
    send_message("\n".join(lines))


def _show_completed_tasks():
    week_ago = (now_mx().date() - timedelta(days=7)).isoformat()
    tasks    = sb_get("tasks", f"status=eq.done&done_at=gte.{week_ago}T00:00:00&select=title,done_at") or []
    if not tasks:
        send_message("✅ Sin tareas completadas esta semana.")
        return
    lines = [f"✅ *Completadas esta semana ({len(tasks)})*\n"]
    for t in tasks:
        lines.append(f"• {t.get('title', '')}")
    send_message("\n".join(lines))
