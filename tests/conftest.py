# tests/conftest.py
"""
Inyecta un módulo `shared` falso antes de que tareas.py lo importe,
evitando la necesidad de variables de entorno durante los tests.
"""
import sys
from datetime import date
from unittest.mock import MagicMock

# Módulo fake — debe estar en sys.modules ANTES de `import tareas`
_shared = MagicMock()

# session es un dict real para que los tests puedan mutarlo normalmente
_shared.session = {
    "flow": None, "task_draft": {}, "task_step": None,
    "pending_task": None,
}

# now_mx() devuelve un MagicMock (no un datetime real) para que los tests
# puedan configurar .date() e .isoformat() independientemente por test.
_now_mock = MagicMock()
_now_mock.date.return_value = date(2026, 4, 30)
_now_mock.isoformat.return_value = "2026-04-30T09:00:00-06:00"
_shared.now_mx.return_value = _now_mock

sys.modules["shared"] = _shared
