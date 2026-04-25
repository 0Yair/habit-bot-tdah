# habit-bot-tdah 🤖

Bot de Telegram personal para Yair — seguimiento de hábitos, finanzas y alimentación. Diseñado para TDAH: mensajes cortos, recordatorios automáticos y check-ins de un solo mensaje.

---

## 🚀 Despliegue

**Plataforma:** [Render.com](https://render.com) — servicio tipo **Worker** (proceso continuo, sin HTTP).

**Repo GitHub:** `0Yair/habit-bot-tdah` — Render hace auto-deploy en cada push a `main`.

**Para reiniciar manualmente:**
1. Ir a render.com → el servicio `habit-bot-tdah`
2. `Manual Deploy` → `Deploy latest commit`
3. Ver logs en la pestaña `Logs` — debe aparecer `[scheduler] Hilo iniciado ✅`

---

## 🏗️ Arquitectura

```
bot.py          # Orquestador: long-polling, dispatch de mensajes/callbacks, scheduler
shared.py       # Config, Supabase REST, Telegram helpers, ai_call()
habitos.py      # Check-ins, rachas, alertas, análisis semanal, creación de hábitos
finanzas.py     # Gastos, foto de ticket (OCR con Claude Vision), resúmenes
comida.py       # Plan alimenticio semanal, recordatorios por comida, logs diarios
asistente.py    # Personas, contexto, respuestas de IA libre
seed_meal_plan.py  # Script one-shot para cargar el plan alimenticio en Supabase
```

---

## 🗄️ Supabase

**URL:** `https://gdosrvuhsnwpcdikzrck.supabase.co`

### Tablas principales

| Tabla | Descripción |
|-------|-------------|
| `habits` | Definición de hábitos (key, name, emoji, block, week_levels) |
| `user_state` | Estado actual por hábito (streak, best_streak, current_week) |
| `habit_logs` | Historial de check-ins diarios |
| `daily_summary` | Vista que une habits + user_state (usada por get_all_state) |
| `expenses` | Gastos registrados (amount negativo, category, card, date) |
| `budgets` | Presupuestos por categoría (id=1, columna `data` JSON) |
| `meal_plan` | Plan alimenticio semanal (day_of_week 0=Lun, meal_type, description) |
| `meal_logs` | Registro diario de comidas (date, meal_type, status: si/parcial/no) |
| `bot_reminders` | Recordatorios personalizados por hábito (hour, minute, active) |
| `personas` | Información de personas conocidas |

### Cargar plan alimenticio
```bash
python seed_meal_plan.py   # ejecutar UNA SOLA VEZ
```

---

## ⚙️ Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_TOKEN` | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | Chat ID de Yair |
| `ANTHROPIC_API_KEY` | API key de Claude (Haiku) |
| `SUPABASE_URL` | URL del proyecto Supabase (hardcodeada como fallback) |
| `SUPABASE_KEY` | Anon key de Supabase (hardcodeada como fallback) |

En Render: Settings → Environment Variables.
En local: archivo `.env` en la raíz del proyecto.

---

## 📅 Scheduler automático

El bot dispara acciones en horarios fijos (hora México):

| Hora | Acción |
|------|--------|
| 7:30 | Menú del día |
| 8:00 | Check-in hábito `cama` + recordatorio desayuno |
| 9:00 | Seguimiento `cama` si no completado |
| 14:00 | Recordatorio comida |
| 17:00 | Recordatorio snack |
| 19:30 | Recordatorio cena |
| 20:00 | Alertas inteligentes (dom = análisis semanal) |
| 21:00 | Check-in hábito `ejercicio` |
| 22:00 | Check-in hábito `comida` |
| día 19 | Análisis mensual de finanzas |

---

## 💰 Finanzas

- **Ciclo BBVA:** del 19 al 18 de cada mes
- **Tarjetas:** BBVA Gold (corte 18, pago 7), HSBC Volaris (corte 9, pago 28), BBVA Débito, Efectivo
- **Registrar gasto:** foto del ticket (OCR con Claude Vision) o `/gasto 250 comida_fuera BBVA_Gold Tacos`
- **Categorías:** renta, comida_super, comida_fuera, transporte, entretenimiento, servicios, salud, educacion, subscripciones, movilidad, ahorros_transfer, otro

---

## 🥗 Plan alimenticio

- **Lun/Mar (Menú A):** Bowl avena proteico · Pechuga ajillo + arroz + brócoli · Yoplait snack · Quesadilla aguacate
- **Mié/Jue (Menú B):** Toasts aguacate+pavo · Lomo cerdo + arroz + brócoli · Yoplait snack · Sopa frijol
- **Vie-Dom:** días libres, sin recordatorios

---

## 🛠️ Desarrollo local

```bash
git clone https://github.com/0Yair/habit-bot-tdah.git
cd habit-bot-tdah
pip install -r requirements.txt
# crear .env con las variables de entorno
python bot.py
```

Los logs del scheduler muestran un tick cada 30s:
```
[scheduler] Hilo iniciado ✅
[scheduler] tick 08:00
```

---

## 🧩 Stack

- **Python 3.11** — sin frameworks, solo `requests`
- **Telegram Bot API** — long-polling manual
- **Supabase** — base de datos PostgreSQL via REST API
- **Anthropic Claude Haiku** — IA para check-ins, OCR de tickets, análisis
- **Render** — despliegue como Worker (proceso continuo 24/7)
