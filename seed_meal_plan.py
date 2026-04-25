"""
seed_meal_plan.py — Carga el plan alimenticio real de Yair en Supabase.

Ejecutar UNA SOLA VEZ:
    python seed_meal_plan.py

Lun = Mar (Menú A) · Mié = Jue (Menú B) · Vie-Dom = días libres (sin plan)
"""
from shared import sb_post, sb_patch

# (day_of_week, meal_type, descripcion_corta, hora, minuto)
# day_of_week: 0=Lunes, 1=Martes, 2=Miércoles, 3=Jueves  (4-6 = libres, sin entradas)
PLAN = [
    # ── LUNES (0) — Menú A ───────────────────────────────────────────────────
    (0, "desayuno", "Bowl de avena proteico con plátano y mantequilla de maní",  8,  0),
    (0, "comida",   "Pechuga al ajillo con arroz rojo y brócoli",                14,  0),
    (0, "snack",    "Yoplait Griego Fresa + berries",                            17,  0),
    (0, "cena",     "Quesadilla de queso con aguacate",                          19, 30),

    # ── MARTES (1) — Menú A (mismo que lunes) ────────────────────────────────
    (1, "desayuno", "Bowl de avena proteico con plátano y mantequilla de maní",  8,  0),
    (1, "comida",   "Pechuga al ajillo con arroz rojo y brócoli",                14,  0),
    (1, "snack",    "Yoplait Griego Fresa + berries",                            17,  0),
    (1, "cena",     "Quesadilla de queso con aguacate",                          19, 30),

    # ── MIÉRCOLES (2) — Menú B ───────────────────────────────────────────────
    (2, "desayuno", "2 toasts de aguacate con pavo y jitomate",                  8,  0),
    (2, "comida",   "Lomo de cerdo con arroz rojo y brócoli",                   14,  0),
    (2, "snack",    "Yoplait Griego Fresa + berries",                            17,  0),
    (2, "cena",     "Sopa de frijol con queso y tortilla",                       19, 30),

    # ── JUEVES (3) — Menú B (mismo que miércoles) ────────────────────────────
    (3, "desayuno", "2 toasts de aguacate con pavo y jitomate",                  8,  0),
    (3, "comida",   "Lomo de cerdo con arroz rojo y brócoli",                   14,  0),
    (3, "snack",    "Yoplait Griego Fresa + berries",                            17,  0),
    (3, "cena",     "Sopa de frijol con queso y tortilla",                       19, 30),

    # Viernes (4), Sábado (5), Domingo (6): días libres — sin entradas
]

DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def main():
    print("🌱 Cargando plan alimenticio en Supabase...\n")

    # Desactivar cualquier plan previo
    from shared import sb_get
    existing = sb_get("meal_plan", "select=id,day_of_week,meal_type")
    if isinstance(existing, list) and existing:
        print(f"   Desactivando {len(existing)} entradas previas...")
        for row in existing:
            sb_patch("meal_plan", f"id=eq.{row['id']}", {"active": False})

    ok = 0
    for i, (day, meal_type, desc, hour, minute) in enumerate(PLAN):
        result = sb_post("meal_plan", {
            "id":          2000 + i,
            "day_of_week": day,
            "meal_type":   meal_type,
            "description": desc,
            "hour":        hour,
            "minute":      minute,
            "active":      True,
        })
        day_name = DAYS[day]
        if isinstance(result, list):
            print(f"   ✅ {day_name:10} {meal_type:9} {hour:02d}:{minute:02d}  {desc[:45]}")
            ok += 1
        else:
            print(f"   ❌ {day_name} {meal_type} — error: {result}")

    print(f"\n✅ Plan cargado: {ok}/{len(PLAN)} entradas.")
    print("\nHorario de recordatorios:")
    print("   🥣 8:00   Desayuno  (Lun–Jue)")
    print("   🍽️ 14:00  Comida    (Lun–Jue)")
    print("   🍓 17:00  Snack     (Lun–Jue)")
    print("   🌙 19:30  Cena      (Lun–Jue)")
    print("   🎉 Vie–Dom sin recordatorios (días libres)")

if __name__ == "__main__":
    main()
