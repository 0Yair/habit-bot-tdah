"""
HABIT TRACKER BOT — Telegram + Claude AI + Supabase
=====================================================
Instala dependencias:
  pip install python-telegram-bot==20.7 supabase anthropic python-dotenv apscheduler

Variables de entorno necesarias (.env):
  TELEGRAM_TOKEN=tu_token_de_botfather
  TELEGRAM_CHAT_ID=tu_chat_id_personal
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_KEY=tu_anon_key
  ANTHROPIC_API_KEY=tu_api_key

Para obtener tu CHAT_ID: habla con @userinfobot en Telegram
"""

import os
import json
import asyncio
from datetime import datetime, date
from dotenv import load_dotenv
from postgrest import SyncPostgrestClient
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# ── Clientes ──────────────────────────────────────────────────────────────────
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
ai_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

# ── Estado en memoria (sesión del check-in actual) ────────────────────────────
session = {
    "pending_habits": [],   # hábitos que faltan confirmar en esta sesión
    "current_habit": None,  # hábito que se está confirmando ahora
    "results": {},          # {habit_key: True/False}
    "block": None,          # 'morning' o 'night'
}


# ══════════════════════════════════════════════════════════════════════════════
# SUPABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_habits(block: str) -> list[dict]:
    """Trae los hábitos del bloque con su estado actual."""
    res = supabase.table("daily_summary").select("*").eq("block", block).execute()
    return res.data


def get_all_state() -> list[dict]:
    """Trae el estado completo para el resumen."""
    res = supabase.table("daily_summary").select("*").execute()
    return res.data


def log_habit(habit_key: str, done: bool, week_level: int, note: str = None):
    """Registra un hábito completado o no."""
    supabase.table("habit_logs").insert({
        "habit_key": habit_key,
        "done": done,
        "week_level": week_level,
        "note": note,
        "logged_at": datetime.now().isoformat(),
    }).execute()

    # Actualizar racha
    state = supabase.table("user_state").select("*").eq("habit_key", habit_key).single().execute().data
    today = date.today()
    last = date.fromisoformat(state["last_logged"]) if state["last_logged"] else None

    if done:
        new_streak = (state["streak"] + 1) if last and (today - last).days == 1 else 1
    else:
        new_streak = 0

    supabase.table("user_state").update({
        "streak": new_streak,
        "best_streak": max(new_streak, state["best_streak"]),
        "last_logged": today.isoformat(),
        "updated_at": datetime.now().isoformat(),
    }).eq("habit_key", habit_key).execute()


def get_week_label(habit: dict) -> str:
    """Devuelve el label del nivel 2% actual."""
    levels = habit.get("week_levels", [])
    week = habit.get("current_week", 1)
    # Encuentra el nivel correcto según la semana actual
    active = levels[0]
    for lvl in levels:
        if week >= lvl["week"]:
            active = lvl
    return f'{active["label"]} — {active["desc"]}'


def advance_week_if_needed(habit_key: str):
    """Sube de nivel 2% si llevas 7 días completando el hábito."""
    state = supabase.table("user_state").select("*").eq("habit_key", habit_key).single().execute().data
    if state["streak"] > 0 and state["streak"] % 7 == 0:
        new_week = min(state["current_week"] + 1, 6)
        supabase.table("user_state").update({
            "current_week": new_week
        }).eq("habit_key", habit_key).execute()
        return new_week
    return None


# ══════════════════════════════════════════════════════════════════════════════
# IA COACH — Claude Haiku (más barato, ~$0.50/mes)
# ══════════════════════════════════════════════════════════════════════════════

def build_context(habits_state: list[dict]) -> str:
    """Construye contexto del historial para la IA."""
    lines = []
    for h in habits_state:
        lines.append(
            f"- {h['emoji']} {h['name']}: racha={h['streak']} días, "
            f"semana_2pct={h['current_week']}, hecho_hoy={h['done_today']}"
        )
    return "\n".join(lines)


def ai_checkin_message(habit: dict, all_state: list[dict]) -> str:
    """Genera el mensaje de check-in con personalidad de coach TDAH."""
    context = build_context(all_state)
    week_label = get_week_label(habit)

    prompt = f"""Eres un coach de hábitos especializado en TDAH. Tu nombre es Hábit.
Hablas en español mexicano, eres cálido, directo y motivador sin ser intenso.
NUNCA usas frases genéricas como "¡Tú puedes!" — eres específico y honesto.

Estado actual del usuario (Yair, 24 años, auditor en EY):
{context}

Hábito a preguntar ahora:
- Nombre: {habit['emoji']} {habit['name']}
- Nivel 2% actual: {week_label}
- Racha actual: {habit['streak']} días

Escribe UN mensaje corto (máx 3 líneas) preguntando si completó este hábito hoy.
- Si la racha es alta (7+), celebra con energía pero sin exagerar
- Si la racha es 0-2, hazlo fácil y sin presión
- Si es 3-6, menciona que está construyendo algo real
- Incluye el nivel exacto del hábito hoy ({week_label})
- Termina con la pregunta directa
- No uses emojis en exceso, máximo 1"""

    resp = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


def ai_reaction(habit: dict, done: bool, all_state: list[dict]) -> str:
    """Reacción de la IA después de marcar un hábito."""
    context = build_context(all_state)

    prompt = f"""Eres Hábit, coach de hábitos para TDAH. Español mexicano, cálido y directo.

El usuario acaba de {'COMPLETAR ✅' if done else 'NO completar ❌'} el hábito: {habit['emoji']} {habit['name']}
Su racha actual: {habit['streak']} días

Estado completo:
{context}

Escribe UNA reacción muy corta (1-2 líneas máximo).
- Si completó: celebra de forma específica, no genérica
- Si no completó: normaliza sin drama, recuerda que mañana cuenta
- NO uses frases hechas
- Máximo 1 emoji"""

    resp = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


def ai_daily_summary(all_state: list[dict], results: dict) -> str:
    """Resumen final del día con análisis de la IA."""
    done_count = sum(1 for v in results.values() if v)
    total = len(results)
    context = build_context(all_state)

    prompt = f"""Eres Hábit, coach de hábitos para TDAH. Español mexicano, honesto y motivador.

Yair completó {done_count} de {total} hábitos hoy.
Hábitos completados: {[k for k,v in results.items() if v]}
Hábitos no completados: {[k for k,v in results.items() if not v]}

Estado completo:
{context}

Escribe un resumen del día (máx 5 líneas):
1. Número de hábitos con algo específico que destacar
2. Una observación inteligente sobre sus patrones
3. Una sola cosa que hacer diferente mañana (muy concreta)
4. Cierra con algo genuinamente motivador (no genérico)

Usa emojis con moderación. Habla directo, como amigo que te conoce."""

    resp = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


def ai_answer_question(question: str, all_state: list[dict]) -> str:
    """Responde preguntas libres del usuario sobre sus hábitos."""
    context = build_context(all_state)

    resp = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": f"""Eres Hábit, coach de hábitos especializado en TDAH.
Español mexicano, cálido, honesto, basado en evidencia.

Estado actual de Yair:
{context}

Pregunta del usuario: {question}

Responde de forma útil, específica y breve (máx 5 líneas).
Basa tu respuesta en su historial real cuando sea relevante."""}]
    )
    return resp.content[0].text


# ══════════════════════════════════════════════════════════════════════════════
# FLUJO DEL CHECK-IN
# ══════════════════════════════════════════════════════════════════════════════

async def start_checkin(app: Application, block: str):
    """Inicia el check-in de mañana o noche."""
    habits = get_habits(block)
    if not habits:
        return

    # Reinicia sesión
    session["block"] = block
    session["pending_habits"] = list(habits)
    session["results"] = {}

    emoji_block = "🌤" if block == "morning" else "🌙"
    label = "mañana" if block == "morning" else "noche"

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=f"{emoji_block} *Check-in de {label}*\nVamos con tus {len(habits)} hábitos de hoy:",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)
    await ask_next_habit(app)


async def ask_next_habit(app: Application):
    """Pregunta el siguiente hábito pendiente."""
    if not session["pending_habits"]:
        await finish_checkin(app)
        return

    habit = session["pending_habits"][0]
    session["current_habit"] = habit
    all_state = get_all_state()

    # Mensaje de la IA
    msg = ai_checkin_message(habit, all_state)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, lo hice", callback_data=f"done_{habit['key']}"),
            InlineKeyboardButton("❌ Hoy no", callback_data=f"skip_{habit['key']}"),
        ]
    ])

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=msg,
        reply_markup=keyboard
    )


async def finish_checkin(app: Application):
    """Termina el check-in y manda resumen con IA."""
    all_state = get_all_state()
    summary = ai_daily_summary(all_state, session["results"])

    done = sum(1 for v in session["results"].values() if v)
    total = len(session["results"])

    # Barra visual de progreso
    filled = int((done / total) * 10) if total > 0 else 0
    bar = "█" * filled + "░" * (10 - filled)

    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=f"📊 *Resumen del día*\n`{bar}` {done}/{total}\n\n{summary}\n\n"
             f"_¿Tienes alguna pregunta sobre tus hábitos? Escríbeme._",
        parse_mode="Markdown"
    )

    session["pending_habits"] = []
    session["current_habit"] = None


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS DE TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones Sí/No del check-in."""
    query = update.callback_query
    await query.answer()

    data = query.data  # done_agua / skip_agua
    action, habit_key = data.split("_", 1)
    done = action == "done"

    # Encuentra el hábito
    habit = next((h for h in get_all_state() if h["key"] == habit_key), None)
    if not habit:
        return

    # Registra en Supabase
    week_level = habit.get("current_week", 1)
    log_habit(habit_key, done, week_level)

    # Revisa si sube de nivel 2%
    new_week = advance_week_if_needed(habit_key)

    session["results"][habit_key] = done
    session["pending_habits"] = [h for h in session["pending_habits"] if h["key"] != habit_key]

    # Reacción de la IA
    all_state = get_all_state()
    reaction = ai_reaction(habit, done, all_state)

    level_up_msg = ""
    if new_week:
        level_up_msg = f"\n\n🆙 *¡Subiste al nivel {new_week} en este hábito!* La versión 2% más difícil empieza mañana."

    await query.edit_message_text(
        text=f"{query.message.text}\n\n{'✅' if done else '❌'} {reaction}{level_up_msg}",
        parse_mode="Markdown"
    )

    await asyncio.sleep(1.5)
    await ask_next_habit(context.application)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde preguntas libres del usuario."""
    if update.effective_chat.id != CHAT_ID:
        return

    question = update.message.text
    all_state = get_all_state()

    await context.bot.send_chat_action(chat_id=CHAT_ID, action="typing")
    answer = ai_answer_question(question, all_state)

    await update.message.reply_text(answer)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola Yair, soy *Hábit* — tu coach de hábitos con TDAH.\n\n"
        "Comandos disponibles:\n"
        "/checkin — inicia check-in manual\n"
        "/resumen — ve tu progreso de hoy\n"
        "/racha — ver tus rachas actuales\n\n"
        "O simplemente escríbeme cualquier pregunta sobre tus hábitos.",
        parse_mode="Markdown"
    )


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check-in manual por comando."""
    hour = datetime.now().hour
    block = "morning" if hour < 15 else "night"
    await start_checkin(context.application, block)


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra resumen del día actual."""
    all_state = get_all_state()
    lines = ["📊 *Tu progreso de hoy*\n"]
    for h in all_state:
        icon = "✅" if h["done_today"] else "⬜"
        lines.append(f"{icon} {h['emoji']} {h['name']} — racha: {h['streak']}d")

    done = sum(1 for h in all_state if h["done_today"])
    total = len(all_state)
    filled = int((done / total) * 10) if total > 0 else 0
    bar = "█" * filled + "░" * (10 - filled)

    lines.append(f"\n`{bar}` {done}/{total} completados")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_racha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra rachas y nivel 2% de cada hábito."""
    all_state = get_all_state()
    lines = ["🔥 *Rachas y niveles 2%*\n"]
    for h in all_state:
        fire = "🔥" if h["streak"] >= 5 else "  "
        lines.append(
            f"{fire} {h['emoji']} {h['name']}\n"
            f"   Racha: {h['streak']}d | Mejor: {h['best_streak']}d | Nivel: {h['current_week']}/6"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — Recordatorios automáticos
# ══════════════════════════════════════════════════════════════════════════════

def setup_scheduler(app: Application):
    scheduler = AsyncIOScheduler(timezone="America/Mexico_City")

    # Check-in mañana: 9:00 AM
    scheduler.add_job(
        lambda: asyncio.create_task(start_checkin(app, "morning")),
        "cron", hour=9, minute=0
    )

    # Check-in noche: 9:30 PM
    scheduler.add_job(
        lambda: asyncio.create_task(start_checkin(app, "night")),
        "cron", hour=21, minute=30
    )

    # Recordatorio si no ha hecho check-in mañana (10:30 AM)
    async def reminder_morning():
        all_state = get_all_state()
        morning_habits = [h for h in all_state if h["block"] == "morning"]
        pending = [h for h in morning_habits if not h["done_today"]]
        if pending:
            await app.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⏰ Oye, aún tienes {len(pending)} hábitos matutinos pendientes. "
                     f"¿Empezamos? /checkin"
            )

    scheduler.add_job(reminder_morning, "cron", hour=10, minute=30)

    scheduler.start()
    return scheduler


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("racha", cmd_racha))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    setup_scheduler(app)

    print("🤖 Hábit bot corriendo... Ctrl+C para detener")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
