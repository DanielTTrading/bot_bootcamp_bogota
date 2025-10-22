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

WIFI_SSID = os.getenv("WIFI_SSID", "NombreDeRed")

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
VIDEOS_DIR = DATA_DIR / "videos"

UBICACION_URL = "https://maps.app.goo.gl/zZfR7kPo9ZR1AUtu9"
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
# UI / MENÚS
# =========================
def principal_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Material de apoyo", callback_data="menu_material")],
        [InlineKeyboardButton("🔗 Enlaces y Conexión", callback_data="menu_enlaces")],
        [InlineKeyboardButton("💳 Exness cuenta demo", callback_data="menu_exness")],
        [InlineKeyboardButton("📣 Enviar mensaje (Admin)", callback_data="admin_broadcast")],
    ])

def enlaces_inline_general() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Abrir ubicación", url=UBICACION_URL)],
        [InlineKeyboardButton("⬅️ Volver", callback_data="volver_menu_principal")],
    ])

def exness_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Crear cuenta en Exness", url=EXNESS_ACCOUNT_URL)],
        #[InlineKeyboardButton("🤝 Conectar al Copy JP TACTICAL", url=EXNESS_COPY_URL)],
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
# HANDLERS
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

# --- BROADCAST por bandera en user_data ---
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
    """Si el usuario es admin y está en modo broadcast, reenvía y retorna True (manejado)."""
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in ADMINS:
        return False
    if not context.user_data.get("bcast"):
        return False

    # salimos del modo broadcast
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
# CALLBACKS MENÚ
# =========================
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

    if data == "menu_enlaces":
        await query.edit_message_text("🔗 *Enlaces y Conexión*",
                                      reply_markup=enlaces_inline_general(),
                                      parse_mode="Markdown")
        return

    if data == "admin_broadcast":
        # lo maneja broadcast_start_cb; este fallback es por si llega aquí
        await broadcast_start_cb(update, context)
        return

    if data == "menu_exness":
        texto = (
            "💳 *Apertura de cuenta demo*\n\n"
            "1) Primero crea y **verifica** tu cuenta en Exness.\n"
            "2) Empieza a disfrutar de exness.\n\n"
            "Usa los botones de abajo 👇"
        )
        await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=exness_inline())
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
