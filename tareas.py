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
