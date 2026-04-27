"""
seed_habits.py — Crea o actualiza los hábitos de ejercicio y comida en Supabase.

Ejecutar UNA SOLA VEZ (o cuando quieras actualizar niveles):
    python seed_habits.py
"""
from shared import sb_get, sb_post, sb_patch
from datetime import datetime

HABITS = [
    {
        "key":   "ejercicio",
        "name":  "Ejercicio",
        "emoji": "🏋️",
        "block": "night",
        "week_levels": [
            {"week": 1, "label": "1h al día", "desc": "1 hora · meta semanal 6 horas"},
        ],
        # Meta diaria: 1 hora   |   Meta semanal: 6 horas (6 días)
    },
    {
        "key":   "comida",
        "name":  "Comida",
        "emoji": "🍽️",
        "block": "night",
        "week_levels": [
            {"week": 1, "label": "Balanceado", "desc": "5 días/semana consciente, balanceado y proteico"},
        ],
        # Meta diaria: comer balanceado según la dieta
        # Meta semanal: 5 días consciente, balanceado y con porciones correctas
    },
]

# ── helpers ───────────────────────────────────────────────────────────────────
def upsert_habit(h: dict):
    key      = h["key"]
    existing = sb_get("habits", f"key=eq.{key}&select=key")
    payload  = {k: v for k, v in h.items() if k != "key"}   # sin el key en el body

    if isinstance(existing, list) and existing:
        sb_patch("habits", f"key=eq.{key}", payload)
        print(f"  ✏️  Actualizado : {h['emoji']} {h['name']}")
    else:
        sb_post("habits", h)
        print(f"  ✅ Creado      : {h['emoji']} {h['name']}")

def upsert_state(key: str):
    existing = sb_get("user_state", f"habit_key=eq.{key}&select=habit_key")
    if isinstance(existing, list) and existing:
        print(f"  ℹ️  Estado ya existe para '{key}'")
    else:
        sb_post("user_state", {
            "habit_key":    key,
            "streak":       0,
            "best_streak":  0,
            "current_week": 1,
            "updated_at":   datetime.now().isoformat(),
        })
        print(f"  ✅ Estado inicial creado para '{key}'")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("🌱 Actualizando hábitos en Supabase...\n")
    for h in HABITS:
        print(f"── {h['emoji']} {h['name']} ──")
        upsert_habit(h)
        upsert_state(h["key"])
        print()

    print("✅ Listo.\n")
    print("Metas:")
    print("  🏋️  Ejercicio — 1 hora/día · 6 horas/semana")
    print("  🍽️  Comida    — comer balanceado/día · 5 días consciente y proteico/semana")
    print("\nCheck-ins: Ejercicio a las 21:00 · Comida a las 22:00")

if __name__ == "__main__":
    main()
