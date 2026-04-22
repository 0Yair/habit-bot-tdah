"""
ASISTENTE — Personas, preguntas libres, recordatorios de contexto.
"""
from datetime import datetime, date
from shared import sb_get, sb_post, sb_patch, send_message, ai_call

# ── Personas ──────────────────────────────────────────────────────────────────
def save_person(raw_input):
    extracted = ai_call(
        f"Extrae info de esta nota. SOLO JSON sin markdown:\n"
        '{"name":"Nombre","birthday":"YYYY-MM-DD o null","interests":["..."],"notes":"resumen"}\n'
        f"Texto: {raw_input}",
        max_tokens=200,
    )
    try:
        import json
        clean = extracted.strip().replace("```json","").replace("```","").strip()
        data = json.loads(clean)
        data["raw_input"] = raw_input
        data["updated_at"] = datetime.now().isoformat()
        existing = sb_get("people_notes", f"name_search=eq.{data['name'].lower()}&select=id")
        if existing:
            sb_patch("people_notes", f"id=eq.{existing[0]['id']}", data)
            return f"✅ Actualicé a *{data['name']}*."
        else:
            sb_post("people_notes", data)
            return f"✅ Guardé a *{data['name']}*."
    except Exception as e:
        print(f"Error guardando persona: {e}", flush=True)
        return "❌ No pude procesar esa info. Intenta de nuevo."

def get_person(name):
    results = sb_get("people_notes", f"name_search=like.*{name.lower()}*&select=*")
    return results[0] if results else None

def ai_person_summary(person):
    return ai_call(
        f"Resumen útil de esta persona en máx 4 líneas. Español mexicano.\n"
        f"Nombre: {person.get('name')} | Cumple: {person.get('birthday') or 'desconocido'}\n"
        f"Intereses: {', '.join(person.get('interests') or []) or 'sin registrar'}\n"
        f"Notas: {person.get('notes') or ''}",
        max_tokens=180,
    )

def ai_person_suggestions(person, context=""):
    return ai_call(
        f"3 sugerencias concretas para conectar con {person.get('name')}. Máx 5 líneas.\n"
        f"Intereses: {', '.join(person.get('interests') or [])}\n"
        f"Cumple: {person.get('birthday') or '?'} | Notas: {person.get('notes') or ''}"
        + (f"\nContexto: {context}" if context else ""),
        max_tokens=200,
    )

def handle_persona_command(text):
    parts = text.strip().split(" ", 2)
    if len(parts) < 2:
        return (
            "📋 *Personas*\n\n"
            "`/persona add Nombre — lo que sabes`\n"
            "`/persona info Nombre`\n"
            "`/persona suggest Nombre`\n"
            "`/persona list`"
        )
    subcmd = parts[1].lower()
    if subcmd == "add":
        return save_person(parts[2]) if len(parts) >= 3 else "Escribe: `/persona add Nombre — info`"

    elif subcmd == "info":
        if len(parts) < 3:
            return "Escribe: `/persona info Nombre`"
        person = get_person(parts[2])
        if not person:
            return "No encontré a esa persona. Agrégala con `/persona add`."
        summary = ai_person_summary(person)
        bday_str = ""
        if person.get("birthday"):
            try:
                bd = date.fromisoformat(person["birthday"])
                today = date.today()
                next_bd = bd.replace(year=today.year)
                if next_bd < today:
                    next_bd = bd.replace(year=today.year + 1)
                days_left = (next_bd - today).days
                if days_left <= 30:
                    bday_str = f"\n\n🎂 Cumple en {days_left}d ({bd.strftime('%d %b')})"
            except:
                pass
        return f"👤 *{person['name']}*\n\n{summary}{bday_str}"

    elif subcmd == "suggest":
        if len(parts) < 3:
            return "Escribe: `/persona suggest Nombre`"
        name_ctx = parts[2].split("—", 1)
        person = get_person(name_ctx[0].strip())
        if not person:
            return f"No encontré a *{name_ctx[0].strip()}*."
        ctx = name_ctx[1].strip() if len(name_ctx) > 1 else ""
        return f"💡 *{person['name']}*\n\n{ai_person_suggestions(person, ctx)}"

    elif subcmd == "list":
        people = sb_get("people_notes", "select=name,birthday&order=name")
        if not people:
            return "Sin personas guardadas. Usa `/persona add`."
        lines = ["📋 *Personas*\n"]
        for p in people:
            bday = f" 🎂 {p['birthday']}" if p.get("birthday") else ""
            lines.append(f"• {p['name']}{bday}")
        return "\n".join(lines)

    return "Subcomando no reconocido. Escribe `/persona` para ver opciones."

# ── Preguntas libres ──────────────────────────────────────────────────────────
def ai_answer_question(question, all_state):
    context = "\n".join([
        f"- {h.get('emoji','')} {h.get('name','')}: racha={h.get('streak',0)}d"
        for h in all_state
    ])
    return ai_call(
        f"Eres Hábit, asistente TDAH de Yair. Español mexicano, útil y directo.\n"
        f"Estado:\n{context}\n\nPregunta: {question}\nRespuesta breve (máx 4 líneas).",
        max_tokens=200,
    )
