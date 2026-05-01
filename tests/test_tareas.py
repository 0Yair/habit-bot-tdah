# tests/test_tareas.py
import sys
from datetime import date
from unittest.mock import call

import tareas
from tareas import _sort_tasks, _format_task_line, _save_task, _complete_task

# Referencia al mock de shared para configurar retornos
shared = sys.modules["shared"]


# ── _sort_tasks ────────────────────────────────────────────────────────────────

def test_sort_tasks_urgent_due_first():
    """Tarea que vence hoy va antes que tarea sin fecha."""
    today = date(2026, 4, 30)
    shared.now_mx.return_value.date.return_value = today

    urgent   = {"id": 1, "title": "A", "due_date": "2026-04-30", "priority": "baja", "created_at": ""}
    no_date  = {"id": 2, "title": "B", "due_date": None,         "priority": "alta", "created_at": ""}

    result = _sort_tasks([no_date, urgent])
    assert result[0]["id"] == 1  # urgent primero


def test_sort_tasks_priority_tiebreak():
    """Sin due_date, alta prioridad antes que media."""
    today = date(2026, 4, 30)
    shared.now_mx.return_value.date.return_value = today

    alta  = {"id": 1, "title": "A", "due_date": None, "priority": "alta",  "created_at": "2026-04-01"}
    media = {"id": 2, "title": "B", "due_date": None, "priority": "media", "created_at": "2026-04-01"}
    baja  = {"id": 3, "title": "C", "due_date": None, "priority": "baja",  "created_at": "2026-04-01"}

    result = _sort_tasks([baja, media, alta])
    assert [t["id"] for t in result] == [1, 2, 3]


def test_sort_tasks_overdue_floats_up():
    """Tarea vencida (días negativos) aparece antes que tarea que vence mañana."""
    today = date(2026, 4, 30)
    shared.now_mx.return_value.date.return_value = today

    overdue   = {"id": 1, "title": "A", "due_date": "2026-04-28", "priority": "baja",  "created_at": ""}
    tomorrow  = {"id": 2, "title": "B", "due_date": "2026-05-01", "priority": "alta",  "created_at": ""}

    result = _sort_tasks([tomorrow, overdue])
    assert result[0]["id"] == 1


# ── _format_task_line ──────────────────────────────────────────────────────────

def test_format_task_due_today():
    today = date(2026, 4, 30)
    shared.now_mx.return_value.date.return_value = today
    task = {"title": "Llamar dentista", "priority": "alta", "due_date": "2026-04-30", "recurrence": None}
    line = _format_task_line(task)
    assert "🔴" in line
    assert "Llamar dentista" in line
    assert "hoy" in line


def test_format_task_no_date_weekly():
    shared.now_mx.return_value.date.return_value = date(2026, 4, 30)
    task = {"title": "Caso de estudio", "priority": "media", "due_date": None, "recurrence": "weekly"}
    line = _format_task_line(task)
    assert "🟡" in line
    assert "semanal" in line


def test_format_task_no_date_no_recurrence():
    shared.now_mx.return_value.date.return_value = date(2026, 4, 30)
    task = {"title": "Revisar notas", "priority": "baja", "due_date": None, "recurrence": None}
    line = _format_task_line(task)
    assert "🟢" in line
    assert "Revisar notas" in line


# ── _save_task ─────────────────────────────────────────────────────────────────

def test_save_task_calls_sb_post():
    shared.reset_mock()
    # Note: tareas.session is bound to the original shared.session dict at import time.
    # Never reassign shared.session — mutate tareas.session directly instead.
    tareas.session.clear()
    shared.now_mx.return_value.isoformat.return_value = "2026-04-30T09:00:00-06:00"

    _save_task({"title": "Test tarea", "priority": "media"})

    shared.sb_post.assert_called_once()
    args = shared.sb_post.call_args
    assert args[0][0] == "tasks"
    assert args[0][1]["title"] == "Test tarea"
    assert args[0][1]["status"] == "pending"


# ── _complete_task ─────────────────────────────────────────────────────────────

def test_complete_task_marks_done():
    shared.reset_mock()
    shared.sb_get.return_value = [{
        "id": 42, "title": "T", "status": "pending",
        "recurrence": None, "due_date": None,
        "priority": "media", "category": "otro",
        "follow_up_days": 4, "parent_id": None, "is_project": False,
    }]
    shared.now_mx.return_value.isoformat.return_value = "2026-04-30T09:00:00-06:00"

    _complete_task(42)

    shared.sb_patch.assert_called_once_with(
        "tasks", "id=eq.42",
        {"status": "done", "done_at": "2026-04-30T09:00:00-06:00"}
    )
    shared.sb_post.assert_not_called()  # no recurrencia


def test_complete_task_weekly_creates_next():
    shared.reset_mock()
    shared.sb_get.return_value = [{
        "id": 7, "title": "Caso de estudio", "status": "pending",
        "recurrence": "weekly", "due_date": None,
        "priority": "alta", "category": "trabajo",
        "follow_up_days": 7, "parent_id": None, "is_project": False,
    }]
    shared.now_mx.return_value.isoformat.return_value = "2026-04-30T09:00:00-06:00"

    _complete_task(7)

    shared.sb_patch.assert_called_once()  # marca done
    shared.sb_post.assert_called_once()   # crea siguiente
    new_task = shared.sb_post.call_args[0][1]
    assert new_task["title"] == "Caso de estudio"
    assert new_task["recurrence"] == "weekly"
    assert new_task["status"] == "pending"


# ── _is_task_intent ────────────────────────────────────────────────────────────

def test_is_task_intent_returns_true_for_task():
    shared.ai_call.return_value = "task"
    assert tareas._is_task_intent("pendiente hablar con mamá") is True


def test_is_task_intent_returns_false_for_question():
    shared.ai_call.return_value = "question"
    assert tareas._is_task_intent("cuánto gasté esta semana") is False


def test_is_task_intent_returns_false_on_ai_exception():
    shared.ai_call.side_effect = Exception("timeout")
    result = tareas._is_task_intent("algo")
    shared.ai_call.side_effect = None  # limpiar para tests posteriores
    assert result is False


# ── _ai_parse_task ─────────────────────────────────────────────────────────────

def test_ai_parse_task_valid_json():
    shared.ai_call.return_value = (
        '{"title":"Hablar con Diego","category":"personas",'
        '"priority":"alta","due_date":"2026-05-01","recurrence":null}'
    )
    shared.now_mx.return_value.date.return_value = date(2026, 4, 30)
    result = tareas._ai_parse_task("hablar con Diego antes del viernes")
    assert result["title"] == "Hablar con Diego"
    assert result["priority"] == "alta"
    assert result["due_date"] == "2026-05-01"
    assert result["recurrence"] is None


def test_ai_parse_task_fallback_on_bad_json():
    shared.ai_call.return_value = "no es json"
    shared.now_mx.return_value.date.return_value = date(2026, 4, 30)
    result = tareas._ai_parse_task("tarea sin parsear")
    assert result["title"] == "tarea sin parsear"
    assert result["category"] == "otro"
    assert result["priority"] == "media"


# ── handle_tareas_text ─────────────────────────────────────────────────────────

def test_handle_tareas_text_task_intent_saves_to_session():
    shared.reset_mock()
    # Mutate tareas.session directly — do NOT reassign shared.session,
    # because tareas.session is bound to the original dict at import time.
    tareas.session.clear()
    tareas.session.update({"flow": None, "pending_task": None})
    shared.ai_call.side_effect = [
        "task",           # _is_task_intent
        '{"title":"Hacer ejercicio","category":"salud","priority":"alta",'
        '"due_date":null,"recurrence":"weekly"}',  # _ai_parse_task
    ]
    shared.now_mx.return_value.date.return_value = date(2026, 4, 30)
    shared.now_mx.return_value.isoformat.return_value = "2026-04-30T09:00:00"

    result = tareas.handle_tareas_text("ejercicio todos los días")

    assert result is True
    assert tareas.session["pending_task"]["title"] == "Hacer ejercicio"
    shared.send_message.assert_called_once()


def test_handle_tareas_text_question_returns_false():
    shared.reset_mock()
    shared.ai_call.return_value = "question"

    result = tareas.handle_tareas_text("cuánto gasté esta semana")

    assert result is False
    shared.send_message.assert_not_called()
