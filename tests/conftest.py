# tests/conftest.py
"""
Inyecta un módulo `shared` falso antes de que tareas.py lo importe,
evitando la necesidad de variables de entorno durante los tests.
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

# Módulo fake — debe estar en sys.modules ANTES de `import tareas`
_shared = MagicMock()

# session es un dict real para que los tests puedan mutarlo normalmente
_shared.session = {
    "flow": None, "task_draft": {}, "task_step": None,
    "pending_task": None,
}

# now_mx() devuelve un datetime real para que .date() y .isoformat() funcionen
_FIXED_NOW = datetime(2026, 4, 30, 9, 0, tzinfo=ZoneInfo("America/Mexico_City"))
_shared.now_mx.return_value = _FIXED_NOW

sys.modules["shared"] = _shared
