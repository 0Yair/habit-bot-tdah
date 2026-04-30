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
