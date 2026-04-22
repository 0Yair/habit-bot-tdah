"""
FINANZAS — Registro de gastos, tickets, resúmenes y análisis mensual.
"""
import json, time, random
from datetime import datetime, date, timedelta
from shared import session, sb_get, sb_post, send_message, ai_call

CATS_FINANCE = {
    "renta":           "🏠 Renta",
    "comida_super":    "🛒 Súper",
    "comida_fuera":    "🍽️ Comida fuera",
    "transporte":      "🚗 Transporte",
    "entretenimiento": "🎬 Entret.",
    "servicios":       "💡 Servicios",
    "salud":           "💊 Salud",
    "educacion":       "📚 Educación",
    "subscripciones":  "📱 Subs",
    "movilidad":       "🛵 Movilidad",
    "ahorros_transfer":"🏦 Ahorro",
    "otro":            "📌 Otro",
}

CARDS_FINANCE = {
    "BBVA_Gold":    "BBVA Gold",
    "HSBC_Volaris": "HSBC Volaris",
    "BBVA_Debito":  "BBVA Débito",
    "Efectivo":     "Efectivo",
}

# ── Ciclo BBVA (19 → 18) ──────────────────────────────────────────────────────
def get_bbva_cycle(ref_date=None):
    today = ref_date or date.today()
    if today.day <= 18:
        first = today.replace(day=1) - timedelta(days=1)
        return first.replace(day=19), today.replace(day=18)
    else:
        cycle_start = today.replace(day=19)
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=18)
        return cycle_start, next_month

# ── Guardar gasto ─────────────────────────────────────────────────────────────
def save_expense(exp: dict) -> bool:
    """Inserta en la tabla expenses. Retorna True si OK, False si error."""
    # ID igual al patrón del tracker: timestamp ms + random
    unique_id = int(time.time() * 1000) + random.randint(0, 999)
    row = {
        "id":          unique_id,
        "date":        exp.get("date", date.today().isoformat()),
        "amount":      -abs(float(exp.get("amount", 0))),
        "description": exp.get("description", ""),
        "category":    exp.get("category", "otro"),
        "card":        exp.get("card", "BBVA_Gold"),
        "notes":       exp.get("notes", "Registrado desde Telegram"),
        "reconciled":  "pendiente",
    }
    result = sb_post("expenses", row)
    # Supabase devuelve lista en éxito, dict con 'code' en error
    if isinstance(result, dict) and result.get("code"):
        print(f"[save_expense ERROR] {result}", flush=True)
        return False
    print(f"[save_expense OK] id={unique_id} desc={row['description']}", flush=True)
    return True

# ── Foto de ticket ────────────────────────────────────────────────────────────
def ai_extract_expense_from_photo(image_base64: str) -> dict:
    import requests as req
    from shared import ANTHROPIC_KEY
    r = req.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}},
                    {"type": "text", "text": (
                        "Extrae del ticket. Responde SOLO JSON sin markdown:\n"
                        '{"amount": número, "description": "comercio", '
                        '"category": "renta|comida_super|comida_fuera|transporte|entretenimiento|servicios|salud|educacion|subscripciones|movilidad|ahorros_transfer|otro", '
                        '"date": "YYYY-MM-DD o null"}\n'
                        'Si no se puede leer: {"error": "no_readable"}'
                    )},
                ],
            }],
        },
    )
    text = r.json()["content"][0]["text"]
    clean = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def handle_photo(update: dict):
    import requests as req
    from shared import BASE_URL, TOKEN
    msg = update.get("message", {})
    photos = msg.get("photo", [])
    if not photos:
        return
    best_photo = max(photos, key=lambda p: p.get("file_size", 0))
    send_message("📸 Leyendo ticket...")
    try:
        file_r = req.get(f"{BASE_URL}/getFile", params={"file_id": best_photo["file_id"]})
        file_path = file_r.json()["result"]["file_path"]
        img_r = req.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
        image_base64 = __import__("base64").b64encode(img_r.content).decode()
        expense = ai_extract_expense_from_photo(image_base64)

        if "error" in expense:
            send_message("❌ No pude leer el ticket. Usa:\n`/gasto 250 comida_fuera BBVA_Gold Descripción`")
            return

        if not expense.get("date"):
            expense["date"] = date.today().isoformat()

        session["pending_expense"] = expense
        cat_label = CATS_FINANCE.get(expense.get("category", "otro"), "📌 Otro")
        amount = abs(expense.get("amount", 0))

        send_message(
            f"🧾 *${amount:.0f}* — {expense.get('description', '')}\n"
            f"📂 {cat_label} · {expense.get('date')}\n\n¿En qué tarjeta?",
            {"inline_keyboard": [
                [{"text": "BBVA Gold",    "callback_data": "exp_card_BBVA_Gold"},
                 {"text": "HSBC Volaris", "callback_data": "exp_card_HSBC_Volaris"}],
                [{"text": "BBVA Débito",  "callback_data": "exp_card_BBVA_Debito"},
                 {"text": "Efectivo",     "callback_data": "exp_card_Efectivo"}],
            ]},
        )
    except Exception as e:
        print(f"Error foto: {e}", flush=True)
        send_message("❌ Error procesando ticket. Intenta de nuevo o usa `/gasto` manual.")

# ── Comando manual /gasto ─────────────────────────────────────────────────────
def handle_gasto_command(text: str):
    parts = text.strip().split(" ", 4)
    if len(parts) < 3:
        send_message(
            "💸 `/gasto MONTO CATEGORÍA TARJETA Descripción`\n\n"
            "Ejemplo: `/gasto 250 comida_fuera BBVA_Gold Tacos`\n\n"
            "Cats: `renta comida_super comida_fuera transporte entretenimiento servicios salud educacion subscripciones movilidad otro`\n"
            "Tarjetas: `BBVA_Gold HSBC_Volaris BBVA_Debito Efectivo`"
        )
        return
    try:
        amount      = float(parts[1])
        category    = parts[2] if len(parts) > 2 else "otro"
        card        = parts[3] if len(parts) > 3 else "BBVA_Gold"
        description = parts[4] if len(parts) > 4 else "Gasto registrado"
        ok = save_expense({"amount": amount, "category": category, "card": card, "description": description})
        if ok:
            send_message(
                f"✅ *${amount:.0f}* — {description}\n"
                f"{CATS_FINANCE.get(category,'📌 Otro')} · {CARDS_FINANCE.get(card, card)}"
            )
        else:
            send_message("❌ Error al guardar en Supabase. Revisa los logs.")
    except Exception as e:
        send_message(f"❌ {e}\nFormato: `/gasto 250 comida_fuera BBVA_Gold Descripción`")

# ── Resumen del ciclo ─────────────────────────────────────────────────────────
def handle_gastos_resumen():
    today = date.today()
    cycle_start, cycle_end = get_bbva_cycle(today)
    exps = sb_get("expenses", f"date=gte.{cycle_start.isoformat()}&date=lte.{cycle_end.isoformat()}&select=*&order=date.desc")

    gastos = [e for e in exps if e.get("entry_type", "gasto") not in ("ingreso_nomina", "ingreso_otro", "pago_tarjeta") and e.get("amount", 0) != 0]
    if not gastos:
        send_message("📊 Sin gastos este ciclo.")
        return

    total = sum(abs(e["amount"]) for e in gastos)
    by_cat = {}
    for e in gastos:
        cat = e.get("category", "otro")
        by_cat[cat] = by_cat.get(cat, 0) + abs(e["amount"])

    lines = [f"📊 *Ciclo BBVA Gold*\n_{cycle_start.strftime('%d %b')} → {cycle_end.strftime('%d %b')}_\n💸 *${total:,.0f} MXN*\n"]
    for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:6]:
        pct = int(amt / total * 100) if total > 0 else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"{CATS_FINANCE.get(cat, cat)}\n`{bar}` ${amt:,.0f} ({pct}%)")
    lines.append(f"\n_{len(gastos)} tx · mi-tracker-xi.vercel.app_")
    send_message("\n".join(lines))

# ── Callbacks de gasto ────────────────────────────────────────────────────────
def handle_finance_callback(data) -> bool:
    """Maneja exp_card_, gasto_confirm_, gasto_cat_. Retorna True si lo procesó."""
    if data.startswith("exp_card_"):
        card = data[9:]
        if "pending_expense" in session:
            session["pending_expense"]["card"] = card
            exp = session["pending_expense"]
            if save_expense(exp):
                send_message(
                    f"✅ *${abs(exp.get('amount',0)):.0f}* — {exp.get('description','')}\n"
                    f"{CATS_FINANCE.get(exp.get('category','otro'),'📌 Otro')} · {CARDS_FINANCE.get(card, card)}\n"
                    f"_mi-tracker-xi.vercel.app_"
                )
                session.pop("pending_expense", None)
            else:
                send_message("❌ Error al guardar en Supabase. Revisa los logs.")
        else:
            send_message("❌ No hay gasto pendiente. Manda la foto de nuevo.")
        return True

    if data.startswith("gasto_confirm_"):
        try:
            exp = __import__("json").loads(data[14:])
            if save_expense(exp):
                send_message(f"✅ *{exp['description']}* — ${abs(exp['amount']):.0f} en {exp['card']}")
            else:
                send_message("❌ Error guardando el gasto.")
        except Exception as e:
            print(f"[gasto_confirm error] {e}", flush=True)
            send_message("❌ Error guardando el gasto.")
        return True

    if data.startswith("gasto_cat_"):
        parts = data[10:].split("_", 1)
        if len(parts) == 2 and "pending_expense" in session:
            session["pending_expense"]["category"] = "_".join(parts)
            exp = session["pending_expense"]
            if save_expense(exp):
                send_message(f"✅ Guardado como *{exp['category']}*: {exp['description']} — ${abs(exp['amount']):.0f}")
                session.pop("pending_expense", None)
            else:
                send_message("❌ Error guardando el gasto.")
        return True

    return False

# ── Análisis mensual (automático día 1) ───────────────────────────────────────
def send_monthly_finance_analysis():
    today = date.today()
    prev_cycle_end = today.replace(day=18) - timedelta(days=1) if today.day == 19 else today - timedelta(days=1)
    cycle_start, cycle_end = get_bbva_cycle(prev_cycle_end)

    exps = sb_get("expenses", f"date=gte.{cycle_start.isoformat()}&date=lte.{cycle_end.isoformat()}&select=*")
    if not exps:
        return

    gastos = [e for e in exps if e.get("entry_type","gasto") not in ("ingreso_nomina","ingreso_otro","pago_tarjeta") and e.get("amount",0) != 0]
    total = sum(abs(e["amount"]) for e in gastos)
    by_cat = {}
    for e in gastos:
        cat = e.get("category","otro")
        by_cat[cat] = by_cat.get(cat, 0) + abs(e["amount"])
    top = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
    context = "\n".join([f"- {CATS_FINANCE.get(k,k)}: ${v:,.0f}" for k,v in top])

    analysis = ai_call(
        f"CFO amigo. Análisis de cierre de ciclo en máx 4 líneas. Directo.\n"
        f"Total: ${total:,.0f} MXN\nTop categorías:\n{context}",
        max_tokens=200,
    )
    send_message(f"💼 *Cierre de ciclo*\nTotal: *${total:,.0f} MXN*\n\n{analysis}\n\n_mi-tracker-xi.vercel.app_")
