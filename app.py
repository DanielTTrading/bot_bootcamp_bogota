import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Optional

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ChatAction
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV / CONFIG
# =========================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "true").lower() == "true"
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}" if BOT_TOKEN else "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_POOL: AsyncConnectionPool | None = None

LAUNCH_DATE_STR = os.getenv("LAUNCH_DATE", "")
PRELAUNCH_DAYS = int(os.getenv("PRELAUNCH_DAYS", "2"))
PRELAUNCH_MESSAGE = os.getenv(
    "PRELAUNCH_MESSAGE",
    "✨ El bot estará disponible 🔥 el día del evento. "
    "⏳ Vuelve pronto y usa /start para comenzar. 🙌"
)

# WIFI
WIFI_SSID = os.getenv("WIFI_SSID", "NombreDeRed")
WIFI_MSG = os.getenv(
    "WIFI_MESSAGE",
    "📶 *Wi-Fi del evento*\n\n• **Nombre de red (SSID):** `{ssid}`\n"
    "• *La red es abierta (no necesita clave).*"
).replace("{ssid}", WIFI_SSID)

# --- ADMINS (incluye el nuevo 7724870185) ---
ADMINS: set[int] = {
    7710920544,
    7560374352,
    7837963996,
    8465613365,
    7724870185,  # NUEVO
}

# =========================
# TEXTOS / RECURSOS
# =========================
NOMBRE_EVENTO = "Bootcamp 2025 Bogotá"
BIENVENIDA = (
    f"🎉 ¡Bienvenido/a al {NOMBRE_EVENTO}! 🎉\n\n"
    "Has sido validado correctamente.\n"
    "Usa el menú para navegar."
)
ALERTA_CONEXION = (
    "⚠️ **Aviso importante**:\n"
    "Si durante la conexión se detecta una persona **no registrada**, será **expulsada**.\n"
    "Por favor, no compartas estos accesos."
)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
AGENDA_PDF = DATA_DIR / "agenda.pdf"
DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# Ubicación y Exness
UBICACION_URL = "https://maps.app.goo.gl/GS2k9sL38zchErH89"
EXNESS_ACCOUNT_URL = "https://one.exnessonelink.com/a/s3wj0b5qry"
EXNESS_COPY_URL = "https://social-trading.exness.com/strategy/227834645/a/s3wj0b5qry?sharer=trader"

# =========================
# BASE LOCAL (JSON o embebida)
# =========================
USUARIOS_JSON = DATA_DIR / "usuarios.json"
USUARIOS_EMBEBIDOS: Dict[str, str] = {
    # "cedula_o_correo": "Nombre Apellido",
    "75106729": "Daniel Mejia sanchez",
    "furolol@gmail.com": "Daniel Mejia sanchez",
    # ... añade aquí o usa data/usuarios.json
}

def es_correo(s: str) -> bool:
    return "@" in s

def es_cedula(s: str) -> bool:
    s2 = s.replace(".", "").replace(" ", "")
    return s2.isdigit()

def normaliza(s: str) -> str:
    return (s or "").strip().lower()

def cargar_base_local() -> Dict[str, str]:
    if USUARIOS_JSON.exists():
        try:
            raw = json.loads(USUARIOS_JSON.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {normaliza(k): v for k, v in raw.items()}
        except Exception:
            pass
    return {normaliza(k): v for k, v in USUARIOS_EMBEBIDOS.items()}

BASE_LOCAL = cargar_base_local()

def parse_fecha(date_str: str):
    try:
        y, m, d = map(int, date_str.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except Exception:
        return None

def hoy_utc() -> datetime:
    return datetime.now(timezone.utc)

def esta_en_prelanzamiento() -> tuple[bool, str]:
    launch_dt = parse_fecha(LAUNCH_DATE_STR)
    if not launch_dt:
        return (False, "")
    habilita_dt = launch_dt - timedelta(days=PRELAUNCH_DAYS)
    now = hoy_utc()
    if now < habilita_dt:
        dias = (habilita_dt.date() - now.date()).days
        msg = (
            f"✨ El bot estará disponible 🔥 el día del evento.\n\n"
            f"⏳ Faltan {dias} días, vuelve pronto. 🙌\n\n"
            f"{PRELAUNCH_MESSAGE}"
        )
        return (True, msg)
    return (False, "")

# =========================
# MATERIAL DE APOYO (igual al primero)
# =========================
PRESENTADORES = [
    ("p1", "Juan Pablo Vieira"),
    ("p2", "Juan José Puerta"),
    ("p3", "Carlos Andrés Pérez"),
    ("p4", "Jorge Mario Rubio"),
    ("p5", "Jair Viana"),
]

# Estructura: MATERIALES[pid]["docs"][nombre] = Path(...)
MATERIALES: Dict[str, Dict[str, Dict[str, Path]]] = {
    "p1": {"videos": {}, "docs": {}},
    "p2": {"videos": {}, "docs": {}},
    "p3": {"videos": {}, "docs": {}},
    "p4": {"videos": {}, "docs": {}},
    "p5": {"videos": {}, "docs": {}},
}
# Ejemplo: agrega aquí tus archivos (debe existir el fichero en data/docs/)
# MATERIALES["p2"]["docs"]["VALORACIÓN RAPIDA JP TACTICAL"] = DOCS_DIR / "VALORACIÓN RAPIDA JP TACTICAL.xlsx"
# MATERIALES["p2"]["docs"]["VALORACIÓN RAPIDA JP TACTICAL DIDACTICA"] = DOCS_DIR / "VALORACIÓN RAPIDA- DIDACTICA-2.xlsx"

# Videos por presentador (URLs)
VIDEO_LINKS: Dict[str, Dict[str, str]] = {
    "p1": {
        "Crear Cuenta en Interactive Brokers": "https://drive.google.com/file/d/1thOot6PZdxLgutH3c3JuCrIwXwRGcxeb/view?usp=sharing",
        "Crear Cuenta en TRII": "https://drive.google.com/file/d/1thOot6PZdxLgutH3c3JuCrIwXwRGcxeb/view?usp=sharing",
    },
    "p2": {
        "DATOS DE EMPRESAS Y MACRO": "https://drive.google.com/file/d/1S-LncN3dd3eYBBCO_YgYuv5n6d2DSGAM/view?usp=sharing",
        "DATOS DE EMPRESAS": "https://drive.google.com/file/d/1Yo1CxNipafXdbcoXK6ahpGgaHdJqdbzj/view?usp=sharing",
        "FRED": "https://drive.google.com/file/d/12SRmvSbdhrS0qeM4dFE1EMSkScH4hKcL/view?usp=sharing",
        "HERRAMIENTA D.O.O.R": "https://drive.google.com/file/d/1zwejfDpdC7Z0CVsCb4t0UqQD0yqdPBBe/view?usp=sharing",
        "MORNINGSTAR": "https://drive.google.com/file/d/1POiz8YG7xYZpjxaBZ7YiZqmI7RpCQgLa/view?usp=sharing",
        "MOVIMIENTOS DE SENADORES USA": "https://drive.google.com/file/d/1zGIZWRRs3EiMAv-i9DDe5N57XxYWkqx5/view?usp=sharing",
        "PAGINA MORDOR INTELLIGENCE": "https://drive.google.com/file/d/17HMRzdBHknyxLeoB7JA0V9h-gtQrgZX4/view?usp=sharing",
        "PORTAFOLIO GRANDES INVERSORES": "https://drive.google.com/file/d/1-qcP4LNAlCaqajgepQYcREC8fdzwpgY-/view?usp=sharing",
        "SCREENER, MAPS Y DATOS": "https://drive.google.com/file/d/1Mn_SmvqXEijzAOPoNtsnoW3mWksqPdTl/view?usp=sharing",
        "SEC": "https://drive.google.com/file/d/1OwIZ_Bk94RHjQZf0zmxtlH38frrxzb70/view?usp=sharing",
        "VALORACIÓN COMPAÑIA": "https://drive.google.com/file/d/1mqG03xZB8urE7_VA1a8YcRO4nalxnSWD/view?usp=sharing",
    },
    "p3": {},
    "p4": {},
    "p5": {},
}

ENLACES_POR_PRESENTADOR: Dict[str, Dict[str, str]] = {
    "p1": {"Web": "https://ttrading.co", "YouTube": "https://www.youtube.com/@JPTacticalTrading"},
    "p2": {
        "FRED": "https://fred.stlouisfed.org/",
        "MACRO TRENDS": "https://www.macrotrends.net/",
        "MORNINGSTAR": "https://www.morningstar.com/",
        "Web": "https://ttrading.co",
        "YouTube": "https://www.youtube.com/@JPTacticalTrading",
    },
    "p3": {"Web": "https://ttrading.co", "YouTube": "https://www.youtube.com/@JPTacticalTrading"},
    "p4": {},
    "p5": {},
}

# =========================
# UI / MENÚS
# =========================
def principal_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Material de apoyo", callback_data="menu_material")],
        [InlineKeyboardButton("💳 Exness cuenta demo", callback_data="menu_exness")],
        [InlineKeyboardButton("📍 Ubicación", callback_data="menu_ubicacion")],
        [InlineKeyboardButton("📶 Conexión Wi-Fi", callback_data="menu_wifi")],
        [InlineKeyboardButton("🔗 Enlaces y Conexión", callback_data="menu_enlaces")],
        [InlineKeyboardButton("📣 Enviar mensaje (Admin)", callback_data="admin_broadcast")],
    ])

def presentadores_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(nombre, callback_data=f"{prefix}:{pid}")] for pid, nombre in PRESENTADORES]
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")])
    return InlineKeyboardMarkup(rows)

def material_presentador_menu(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Videos", callback_data=f"mat_videos_url:{pid}")],
        [InlineKeyboardButton("📄 Documentos", callback_data=f"mat_docs:{pid}")],
        [InlineKeyboardButton("⬅️ Elegir otro presentador", callback_data="menu_material")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="volver_menu_principal")],
    ])

def lista_archivos_inline(diccionario: Dict[str, Path], prefix: str, pid: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(nombre, callback_data=f"{prefix}:{pid}:{nombre}")]
            for nombre in diccionario.keys()]
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data=f"mat_pres:{pid}")])
    return InlineKeyboardMarkup(rows)

def lista_video_links_inline(pid: str) -> InlineKeyboardMarkup:
    enlaces = VIDEO_LINKS.get(pid, {})
    rows = [[InlineKeyboardButton(nombre, url=url)] for nombre, url in enlaces.items()]
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data=f"mat_pres:{pid}")])
    rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="volver_menu_principal")])
    return InlineKeyboardMarkup(rows)

def enlaces_inline_general() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Enlaces por presentador", callback_data="enlaces_por_presentador")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")],
    ])

def enlaces_presentador_lista(pid: str) -> InlineKeyboardMarkup:
    enlaces = ENLACES_POR_PRESENTADOR.get(pid, {})
    rows = [[InlineKeyboardButton(nombre, url=url)] for nombre, url in enlaces.items()]
    rows.append([InlineKeyboardButton("⬅️ Elegir otro presentador", callback_data="enlaces_por_presentador")])
    rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="volver_menu_principal")])
    return InlineKeyboardMarkup(rows)

def ubicacion_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Abrir en Google Maps", url=UBICACION_URL)],
        [InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")],
    ])

def exness_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Crear cuenta en Exness", url=EXNESS_ACCOUNT_URL)],
        [InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")],
    ])

def wifi_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")],
    ])

BTN_ENLACES = "🔗 Enlaces y Conexión"
BTN_CERRAR = "❌ Cerrar menú"

def bottom_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(BTN_ENLACES)],
            [KeyboardButton(BTN_CERRAR)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

# =========================
# AUTH (RAM)
# =========================
@dataclass
class PerfilUsuario:
    nombre: str
    autenticado: bool = False

PERFILES: Dict[int, PerfilUsuario] = {}

async def ensure_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Tuple[bool, int]:
    user_id = update.effective_user.id if update.effective_user else 0
    perfil = PERFILES.get(user_id)
    return (perfil is not None and perfil.autenticado), user_id

# =========================
# DB (PostgreSQL)
# =========================
async def get_db_pool() -> AsyncConnectionPool:
    global DB_POOL
    if DB_POOL is None:
        if not DATABASE_URL:
            raise RuntimeError("Falta DATABASE_URL para conectarse a PostgreSQL.")
        DB_POOL = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=5)
        await DB_POOL.open()
    return DB_POOL

async def init_db():
    pool = await get_db_pool()
    async with pool.connection() as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS subscribed_users (
                user_id         BIGINT PRIMARY KEY,
                first_name      TEXT,
                last_name       TEXT,
                username        TEXT,
                language        TEXT,
                nombre          TEXT,
                cedula          TEXT,
                correo          TEXT,
                credential_used TEXT,
                first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_subscribed_users_correo ON subscribed_users (correo);")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_subscribed_users_cedula ON subscribed_users (cedula);")

async def upsert_user_seen(u) -> None:
    if not u:
        return
    pool = await get_db_pool()
    async with pool.connection() as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("""
                INSERT INTO subscribed_users (user_id, first_name, last_name, username, language, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE
                   SET first_name = EXCLUDED.first_name,
                       last_name  = EXCLUDED.last_name,
                       username   = EXCLUDED.username,
                       language   = EXCLUDED.language,
                       last_seen  = NOW();
            """, (u.id, getattr(u, "first_name", None), getattr(u, "last_name", None),
                  getattr(u, "username", None), getattr(u, "language_code", None)))

async def persistir_validacion(user_id: int, nombre: str,
                               cedula: Optional[str], correo: Optional[str],
                               credential_used: str) -> None:
    pool = await get_db_pool()
    async with pool.connection() as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("""
                INSERT INTO subscribed_users (user_id, nombre, cedula, correo, credential_used, last_seen)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE
                   SET nombre = EXCLUDED.nombre,
                       cedula = COALESCE(EXCLUDED.cedula, subscribed_users.cedula),
                       correo = COALESCE(EXCLUDED.correo, subscribed_users.correo),
                       credential_used = EXCLUDED.credential_used,
                       last_seen = NOW();
            """, (user_id, nombre, cedula, correo, credential_used))

async def fetch_broadcast_user_ids() -> list[int]:
    pool = await get_db_pool()
    async with pool.connection() as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("SELECT user_id FROM subscribed_users WHERE nombre IS NOT NULL;")
            rows = await cur.fetchall()
    return [r[0] for r in rows]

# =========================
# HELPERS
# =========================
def buscar_en_base(clave: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    c = normaliza(clave)
    nombre = BASE_LOCAL.get(c)
    if not nombre:
        return None
    cedula_detectada = c if es_cedula(c) else None
    correo_detectado = c if es_correo(c) else None
    for k, v in BASE_LOCAL.items():
        if v != nombre:
            continue
        if not cedula_detectada and es_cedula(k):
            cedula_detectada = k
        if not correo_detectado and es_correo(k):
            correo_detectado = k
        if cedula_detectada and correo_detectado:
            break
    return (nombre, cedula_detectada, correo_detectado)

async def envia_documento(upd_or_q, context: ContextTypes.DEFAULT_TYPE, ruta: Path, nombre_mostrar: str):
    if isinstance(upd_or_q, Update):
        chat = upd_or_q.effective_chat
        message = upd_or_q.effective_message
    else:
        q = upd_or_q
        chat = q.message.chat
        message = q.message

    if not ruta.exists():
        await message.reply_text(f"⚠️ No encuentro el archivo: {nombre_mostrar}")
        return

    ext = ruta.suffix.lower()
    es_video = ext in {".mp4", ".mov", ".m4v"}

    action = ChatAction.UPLOAD_VIDEO if es_video else ChatAction.UPLOAD_DOCUMENT
    texto_espera = "⏳ Preparando y enviando el video… puede tardar unos minutos." if es_video \
                   else "⏳ Preparando y enviando el archivo…"

    await chat.send_action(action=action)
    aviso = await message.reply_text(texto_espera)

    for i in range(1, 4):
        try:
            with ruta.open("rb") as f:
                if es_video:
                    await message.reply_video(video=InputFile(f, filename=ruta.name), caption=nombre_mostrar, supports_streaming=True)
                else:
                    await message.reply_document(document=InputFile(f, filename=ruta.name), caption=nombre_mostrar)
            await aviso.edit_text("✅ Archivo enviado.")
            await message.reply_text("¿Qué deseas hacer ahora?", reply_markup=principal_inline())
            return
        except (TimedOut, NetworkError) as e:
            if i < 3:
                espera = 2 ** i
                try:
                    await aviso.edit_text(f"⚠️ Conexión inestable, reintentando en {espera}s… (intento {i}/3)")
                except Exception:
                    pass
                await asyncio.sleep(espera)
                continue
            else:
                await aviso.edit_text(f"❌ No se pudo enviar el archivo. Detalle: {e}")
                return
        except Exception as e:
            await aviso.edit_text(f"❌ Error al enviar el archivo: {e}")
            return

# =========================
# HANDLERS BÁSICOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)
    en_pre, msg = esta_en_prelanzamiento()
    if en_pre:
        await update.message.reply_text(msg)
        return
    await update.message.reply_text(
        f"👋 Hola, este es el bot del {NOMBRE_EVENTO}.\n\n"
        "Por favor escribe tu **cédula** o **correo registrado** para validar tu acceso:",
        reply_markup=bottom_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)
    await update.message.reply_text(
        "/start - Iniciar/validar acceso\n"
        "/menu - Mostrar menú\n"
        "/help - Ayuda\n"
        "/broadcast - (admins) iniciar envío masivo\n"
        "/cancel - cancelar envío masivo\n"
        "/miid - ver tu ID de Telegram\n"
    )

async def miid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)
    u = update.effective_user
    uid = u.id if u else 0
    un = f"@{u.username}" if (u and u.username) else "(sin username)"
    await update.message.reply_text(
        "🆔 *Tu información de Telegram*\n"
        f"• ID: `{uid}`\n"
        f"• Username: {un}\n\n"
        "Si eres admin, asegúrate de que tu ID esté en la lista ADMINS.",
        parse_mode="Markdown"
    )

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)
    autenticado, _ = await ensure_auth(update, context)
    if not autenticado:
        await update.message.reply_text("⚠️ Debes validarte primero. Escribe tu **cédula** o **correo**.")
        return
    await update.message.reply_text("Menú principal:", reply_markup=principal_inline())

# =========================
# BROADCAST ADMIN
# =========================
async def broadcast_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await upsert_user_seen(query.from_user)
    uid = query.from_user.id
    if uid not in ADMINS:
        await query.answer("Solo para administradores.", show_alert=True)
        return
    context.user_data["bcast"] = True
    await query.edit_message_text(
        "📣 *Envío masivo*\n\nEnvía ahora el mensaje que deseas reenviar a TODOS "
        "los usuarios **validados** (texto, foto, video o documento).\n\n"
        "Escribe /cancel para cancelar.",
        parse_mode="Markdown"
    )

async def broadcast_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)
    uid = update.effective_user.id
    if uid not in ADMINS:
        await update.message.reply_text("🚫 Este comando es solo para administradores.")
        return
    context.user_data["bcast"] = True
    await update.message.reply_text(
        "📣 *Envío masivo*\n\nEnvía ahora el mensaje que deseas reenviar a TODOS "
        "los usuarios **validados** (texto, foto, video o documento).\n\n"
        "Escribe /cancel para cancelar.",
        parse_mode="Markdown"
    )

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("bcast", None)
    await update.message.reply_text("Operación cancelada.")
    await update.message.reply_text("Menú principal:", reply_markup=principal_inline())

async def intentar_broadcast_si_corresponde(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in ADMINS:
        return False
    if not context.user_data.get("bcast"):
        return False

    context.user_data["bcast"] = False

    targets = await fetch_broadcast_user_ids()
    if not targets:
        await update.message.reply_text("⚠️ Aún no hay usuarios validados en la base de datos.")
        await update.message.reply_text("Menú principal:", reply_markup=principal_inline())
        return True

    ok, fail = 0, 0
    for tid in targets:
        try:
            await context.bot.copy_message(
                chat_id=tid,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.03)

    await update.message.reply_text(f"✅ Enviado a {ok} usuarios. ❌ Fallidos: {fail}")
    await update.message.reply_text("Menú principal:", reply_markup=principal_inline())
    return True

# =========================
# ACCIONES / MENÚ TEXTO
# =========================
async def text_ingreso_o_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user_seen(update.effective_user)

    # 1) Si admin está en modo broadcast, se maneja aquí y termina
    if await intentar_broadcast_si_corresponde(update, context):
        return

    # 2) Normal
    en_pre, msg = esta_en_prelanzamiento()
    if en_pre:
        await update.message.reply_text(msg)
        return

    autenticado, user_id = await ensure_auth(update, context)
    texto = (update.message.text or "").strip()

    if autenticado:
        if texto == BTN_ENLACES:
            await update.message.reply_text(
                "🔗 *Enlaces y Conexión*",
                reply_markup=enlaces_inline_general(),
                parse_mode="Markdown",
            )
            return
        if texto == BTN_CERRAR:
            await update.message.reply_text(
                "Menú ocultado. Usa /menu para volver a mostrarlo.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        await update.message.reply_text("Estás autenticado. Usa el menú:", reply_markup=principal_inline())
        return

    # Validación contra base local (JSON/embebida)
    clave = normaliza(texto)
    if not clave:
        await update.message.reply_text("❗ Por favor escribe tu **cédula** o **correo**.")
        return

    encontrado = buscar_en_base(clave)
    if not encontrado:
        await update.message.reply_text(
            "🚫 No encuentro tu registro en la base.\n\n"
            "Verifica que hayas escrito tu **cédula** o **correo** tal como lo registraste."
        )
        return

    nombre, cedula, correo = encontrado
    PERFILES[user_id] = PerfilUsuario(nombre=nombre, autenticado=True)

    await persistir_validacion(
        user_id=user_id,
        nombre=nombre,
        cedula=cedula,
        correo=correo,
        credential_used=clave
    )

    primer_nombre = nombre.split()[0]
    await update.message.reply_text(
        f"¡Hola, {primer_nombre}! 😊\n{BIENVENIDA}",
        reply_markup=bottom_keyboard()
    )
    await update.message.reply_text("Menú principal:", reply_markup=principal_inline())

# =========================
# CALLBACKS MENÚ (incluye Material, Ubicación y Wi-Fi)
# =========================
async def accion_ubicacion(upd_or_q, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(upd_or_q, Update):
        msg = upd_or_q.message
        await msg.reply_text("📍 *Ubicación del evento*\nToca el botón para abrir en Google Maps.",
                             parse_mode="Markdown", reply_markup=ubicacion_inline())
    else:
        q = upd_or_q
        await q.edit_message_text("📍 *Ubicación del evento*\nToca el botón para abrir en Google Maps.",
                                  parse_mode="Markdown", reply_markup=ubicacion_inline())

async def accion_wifi(upd_or_q, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(upd_or_q, Update):
        msg = upd_or_q.message
        await msg.reply_text(WIFI_MSG, parse_mode="Markdown", reply_markup=wifi_inline())
    else:
        q = upd_or_q
        await q.edit_message_text(WIFI_MSG, parse_mode="Markdown", reply_markup=wifi_inline())

async def menu_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await upsert_user_seen(query.from_user)

    en_pre, msg = esta_en_prelanzamiento()
    if en_pre:
        await query.message.reply_text(msg)
        return

    autenticado, _ = await ensure_auth(update, context)
    if not autenticado:
        await query.edit_message_text("⚠️ Debes validarte primero. Escribe tu **cédula** o **correo**.")
        return

    data = query.data

    if data == "volver_menu_principal":
        await query.edit_message_text("Menú principal:", reply_markup=principal_inline())
        return

    # Material de apoyo
    if data == "menu_material":
        await query.edit_message_text(
            "📚 *Material de apoyo*\nElige un presentador:",
            reply_markup=presentadores_keyboard("mat_pres"),
            parse_mode="Markdown",
        )
        return

    if data.startswith("mat_pres:"):
        pid = data.split(":", 1)[1]
        nombre = next((n for (i, n) in PRESENTADORES if i == pid), "Presentador")
        await query.edit_message_text(
            f"📚 *Material de {nombre}*",
            reply_markup=material_presentador_menu(pid),
            parse_mode="Markdown",
        )
        return

    if data.startswith("mat_videos_url:"):
        pid = data.split(":", 1)[1]
        enlaces = VIDEO_LINKS.get(pid, {})
        if not enlaces:
            await query.edit_message_text("🎥 No hay videos por ahora.",
                                          reply_markup=material_presentador_menu(pid))
        else:
            await query.edit_message_text("🎥 *Videos:*",
                                          reply_markup=lista_video_links_inline(pid),
                                          parse_mode="Markdown")
        return

    if data.startswith("mat_docs:"):
        pid = data.split(":", 1)[1]
        docs = MATERIALES.get(pid, {}).get("docs", {})
        if not docs:
            await query.edit_message_text("📄 No hay documentos disponibles por ahora.",
                                          reply_markup=material_presentador_menu(pid))
        else:
            await query.edit_message_text("📄 *Documentos:*",
                                          reply_markup=lista_archivos_inline(docs, "doc", pid),
                                          parse_mode="Markdown")
        return

    if data.startswith("doc:"):
        _, pid, titulo = data.split(":", 2)
        ruta = MATERIALES.get(pid, {}).get("docs", {}).get(titulo)
        if ruta:
            await envia_documento(update, context, ruta, titulo)
        else:
            await query.message.reply_text("No se encontró el documento solicitado.")
        return

    # Enlaces generales
    if data == "menu_enlaces":
        await query.edit_message_text("🔗 *Enlaces y Conexión*",
                                      reply_markup=enlaces_inline_general(),
                                      parse_mode="Markdown")
        return

    if data == "enlaces_por_presentador":
        await query.edit_message_text("⭐ Elige un presentador:",
                                      reply_markup=presentadores_keyboard("link_pres"))
        return

    if data.startswith("link_pres:"):
        pid = data.split(":", 1)[1]
        nombre = next((n for (i, n) in PRESENTADORES if i == pid), "Presentador")
        enlaces = ENLACES_POR_PRESENTADOR.get(pid, {})
        if not enlaces:
            await query.edit_message_text(
                f"⭐ *Enlaces de {nombre}*\n(No hay enlaces por ahora.)",
                reply_markup=enlaces_presentador_lista(pid),
                parse_mode="Markdown")
        else:
            await query.edit_message_text(
                f"⭐ *Enlaces de {nombre}*:",
                reply_markup=enlaces_presentador_lista(pid),
                parse_mode="Markdown")
        return

    # Ubicación y Wi-Fi
    if data == "menu_ubicacion":
        await accion_ubicacion(query, context)
        return

    if data == "menu_wifi":
        await accion_wifi(query, context)
        return

    # Exness
    if data == "menu_exness":
        texto = (
            "💳 *Apertura de cuenta demo*\n\n"
            "1) Primero crea y **verifica** tu cuenta en Exness.\n"
            "2) Empieza a disfrutar de Exness.\n\n"
            "Usa los botones de abajo 👇"
        )
        await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=exness_inline())
        return

    # Broadcast (por si cae aquí)
    if data == "admin_broadcast":
        await broadcast_start_cb(update, context)
        return

# =========================
# ARRANQUE
# =========================
def build_app() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("Falta la variable de entorno BOT_TOKEN.")

    async def _post_init(app: Application):
        await init_db()
        global BASE_LOCAL
        BASE_LOCAL = cargar_base_local()

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("miid", miid_cmd))

    # Broadcast simple por bandera
    app.add_handler(CommandHandler("broadcast", broadcast_start_cmd))
    app.add_handler(CommandHandler("cancel", broadcast_cancel))
    app.add_handler(CallbackQueryHandler(broadcast_start_cb, pattern="^admin_broadcast$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_ingreso_o_menu))
    app.add_handler(CallbackQueryHandler(menu_callbacks))

    return app


if __name__ == "__main__":
    application = build_app()

    if USE_WEBHOOK and WEBHOOK_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=WEBHOOK_URL,
        )
    else:
        print("Iniciando en modo polling. Establece USE_WEBHOOK=true y WEBHOOK_HOST=https://<...> para prod.")
        application.run_polling(drop_pending_updates=True)
