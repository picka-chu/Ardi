import io
import re
import json
import time
import asyncio
import datetime
import logging
import os
from collections import defaultdict
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ConversationHandler,
    ContextTypes,
)

from sqlalchemy import select, exc as sa_exc
from db.database import async_session
from db.models import Business, Product, BusinessConnectionModel, User, Order, OrderItem, EscalatedChat, PaymentMethod, _utcnow
from ai.gemini import identify_product, generate_sales_response, conduct_registration, classify_intent, verify_receipt, transcribe_voice
from receipts import looks_like_url, fetch_receipt_url, verify_pdf_receipt, ReceiptError, detect_bank_link, extract_bank_receipt
import chapa
from ai.embeddings import generate_caption, embed_text, find_best_match_sync, caption_and_embed
from storage import upload_product_photo
from bot.translations import _t, lang_kb
from config import RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW, GEMINI_API_KEY, ADMIN_TELEGRAM_ID, TRIAL_DAYS
from db.settings import get_plan_prices

logger = logging.getLogger(__name__)


async def _exit_if_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """If user pressed a keyboard menu button, route and end conversation."""
    if not update.message or not update.message.text:
        return None
    action = BUTTON_ACTIONS.get(update.message.text.strip())
    if action and action in BUTTON_DISPATCH:
        await BUTTON_DISPATCH[action](update, context)
        return ConversationHandler.END
    return None


# ─── Input Validation ────────────────────────────────────────────────────────

MEDIA_GROUP_CACHE: dict[str, float] = {}  # media_group_id -> timestamp
MAX_TEXT_LENGTH = 2000
MAX_PRICE = 9_999_999
MAX_QUANTITY = 99_999
MAX_VOICE_BYTES = 10 * 1024 * 1024  # Telegram voice notes cap at ~20MB; reject huge files early

VOICE_TOO_LONG = (
    "🎤 Your voice note is too long to process. Could you send a shorter one, or type your message?"
    " / የድምጽ መልእክትዎ በጣም ረጅም ነው። እባክዎ አጭር ይላኩ ወይም ይጻፉ።"
)
VOICE_UNCLEAR = (
    "🎤 I couldn't hear that clearly. Could you say it again, or type your message?"
    " / ድምጽዎን በግልጽ አልሰማሁም። እባክዎ እንደገና ይላኩ ወይም ይጻፉ።"
)
VOICE_NO_CHAT = (
    "🎤 I can listen to voice notes inside a customer chat. Open a store link to start shopping,"
    " or type your message here."
)


def _is_media_group_duplicate(update: Update) -> bool:
    """Detect duplicate messages from the same media group within 2 seconds."""
    mgid = update.message.media_group_id if update.message else None
    if not mgid:
        return False
    now = time.time()
    if mgid in MEDIA_GROUP_CACHE and now - MEDIA_GROUP_CACHE[mgid] < 2:
        return True
    MEDIA_GROUP_CACHE[mgid] = now
    # Expire old entries
    for k in list(MEDIA_GROUP_CACHE.keys()):
        if now - MEDIA_GROUP_CACHE[k] > 10:
            del MEDIA_GROUP_CACHE[k]
    return False


def _validate_text(text: str) -> str | None:
    if not text or not text.strip():
        return "Message cannot be empty."
    if len(text) > MAX_TEXT_LENGTH:
        return f"Message too long ({len(text)} chars). Max {MAX_TEXT_LENGTH}."
    return None


_PRICE_RE = re.compile(r"^\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*$")


def _parse_price(s: str) -> float | None:
    """Parse a price string, accepting only valid thousands separators."""
    s = s.strip()
    m = _PRICE_RE.match(s)
    if not m:
        return None
    num_str = m.group(1).replace(",", "")
    return float(num_str)


def _validate_price(price_str: str) -> str | None:
    val = _parse_price(price_str)
    if val is None:
        return "Invalid price format. Use numbers only (e.g. 500 or 1,200)."
    if val < 0 or val > MAX_PRICE:
        return f"Price must be between 0 and {MAX_PRICE:,} ETB."
    return None


def _money(value) -> Decimal:
    """Coerce money from any source (None, float, str, Decimal) to Decimal.

    Legacy rows may hold floats; mixing float with Decimal raises TypeError,
    so every order-total path must go through here.
    """
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0.00")


def _validate_quantity(qty_str: str) -> str | None:

    try:
        val = int(qty_str)
        if val < 1 or val > MAX_QUANTITY:
            return f"Quantity must be between 1 and {MAX_QUANTITY:,}."
    except (ValueError, TypeError):
        return "Invalid quantity. Enter a whole number (e.g. 2)."
    return None


def _has_photo(update: Update) -> bool:
    """Check the message actually has a photo (not a document, video, etc)."""
    return bool(update.message and update.message.photo)


BUSINESS_HOURS_WARNING = (
    "I'm currently offline. It's outside our business hours.\n"
    "I'll pass your message to the business owner, and they'll get back to you."
)


def _is_within_business_hours(business) -> bool:
    if not business.business_hours_enabled:
        return True
    if not business.business_hours_start or not business.business_hours_end:
        return True
    now = datetime.datetime.now().time()
    try:
        def _pad(t):
            parts = t.split(":")
            return f"{int(parts[0]):02d}:{parts[1]}"
        start = datetime.datetime.strptime(_pad(business.business_hours_start), "%H:%M").time()
        end = datetime.datetime.strptime(_pad(business.business_hours_end), "%H:%M").time()
    except (ValueError, TypeError):
        return True
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end

# Conversation states
REGISTER_CONVERSATION = range(1)
ADD_PRODUCT_PHOTO, ADD_PRODUCT_CONFIRM = range(1, 3)

BUSINESS_PREFIX = "bus_"

# ─── Identity Layer ────────────────────────────────────────────────────────

async def get_user(session, telegram_id):
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_or_create_user(session, telegram_id, business_id=None):
    user = await get_user(session, telegram_id)
    if user:
        user.last_seen = _utcnow()
        if business_id is not None:
            user.business_id = business_id
            user.role = "business_owner"
        if telegram_id == ADMIN_TELEGRAM_ID and not user.is_super_admin:
            user.is_super_admin = True
            user.role = "super_admin"
        await session.commit()
        return user, False
    try:
        is_super = telegram_id == ADMIN_TELEGRAM_ID
        user = User(
            telegram_id=telegram_id,
            role="super_admin" if is_super else ("business_owner" if business_id else "guest"),
            business_id=business_id,
            is_super_admin=is_super,
        )
        session.add(user)
        await session.commit()
        return user, True
    except sa_exc.IntegrityError:
        await session.rollback()
        user = await get_user(session, telegram_id)
        if user:
            return user, False
        raise


_user_cache: dict[int, tuple[str, float]] = {}


def _invalidate_lang_cache():
    """Clear all cached language entries."""
    _user_cache.clear()


async def get_user_language(telegram_id: int) -> str:
    """Return language code for a user, with per-process cache (60s TTL)."""
    entry = _user_cache.get(telegram_id)
    if entry:
        lang, ts = entry
        if time.monotonic() - ts < 60:
            return lang
    async with async_session() as session:
        user = await get_user(session, telegram_id)
        lang = user.language if user and user.language else "en"
    _user_cache[telegram_id] = (lang, time.monotonic())
    return lang


async def resolve_identity(telegram_id: int) -> dict:
    """Determine the user's identity: role, business_id, business_name, etc."""
    business_id = 0
    business_name = ""
    business_description = ""
    role = "guest"
    async with async_session() as session:
        user = await get_user(session, telegram_id)
        business = None
        if user and user.business_id:
            business = await session.get(Business, user.business_id)
        elif user and not user.business_id:
            business = await session.execute(
                select(Business).where(Business.telegram_chat_id == telegram_id)
            )
            business = business.scalar_one_or_none()
            if business and user:
                user.business_id = business.id
                user.role = "business_owner"
                await session.commit()
        if user and user.role:
            role = user.role
        # Copy attributes inside session to avoid detached-instance access.
        if business:
            business_id = business.id
            business_name = business.name
            business_description = business.description or ""

    if business_id:
        return {
            "telegram_id": telegram_id,
            "role": "business_owner",
            "business_id": business_id,
            "business_name": business_name,
            "business_description": business_description,
            "owner_name": business_name,
        }
    return {
        "telegram_id": telegram_id,
        "role": role,
        "business_id": 0,
        "business_name": "",
        "business_description": "",
        "owner_name": "",
    }


async def get_business(session, chat_id):
    result = await session.execute(select(Business).where(Business.telegram_chat_id == chat_id))
    return result.scalar_one_or_none()


def _parse_id_suffix(data: str, prefix: str) -> int | None:
    """Safely parse integer ID suffix from callback data. Returns None on malformed input."""
    try:
        return int(data.replace(prefix, "", 1) if data.startswith(prefix) else data.split("_")[-1])
    except (ValueError, TypeError, AttributeError):
        return None


async def _require_owner_business(session, chat_id) -> Business | None:
    """Return the caller's Business or None if not registered."""
    return await get_business(session, chat_id)


def _owns_order(business: Business | None, order) -> bool:
    return bool(business and order and order.business_id == business.id)


def _owns_product(business: Business | None, product) -> bool:
    return bool(business and product and product.business_id == business.id)


def _owns_escalation(business: Business | None, esc) -> bool:
    return bool(business and esc and esc.business_id == business.id)


def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_TELEGRAM_ID) and user_id == ADMIN_TELEGRAM_ID


def _miniapp_base() -> str:
    """Bare https service URL with no trailing slash or /business suffix.

    Accepts MINI_APP_URL as either https://host or https://host/business.
    """
    from config import MINI_APP_URL
    base = (MINI_APP_URL or "").strip().rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    if base.lower().endswith("/business"):
        base = base[: -len("/business")]
    return base


def _miniapp_url(path: str = "") -> str:
    base = _miniapp_base()
    return f"{base}{path}" if base else ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    lang = await get_user_language(chat_id)

    # Deep link: direct customer to business
    if args and args[0].startswith(BUSINESS_PREFIX):
        bus_id = _parse_id_suffix(args[0], BUSINESS_PREFIX)
        if bus_id is None:
            await update.message.reply_text(_t("business_unavailable", lang))
            return
        async with async_session() as session:
            result = await session.execute(select(Business).where(Business.id == bus_id))
            business = result.scalar_one_or_none()
            if business and business.ai_active:
                context.user_data["customer_chat_business_id"] = business.id
                context.user_data["customer_chat_active"] = True
                await update.message.reply_text(
                    _t("welcome_chat_with", lang, name=business.name),
                    parse_mode="Markdown",
                )
                return
            else:
                await update.message.reply_text(_t("business_unavailable", lang))
                return

    async with async_session() as session:
        business = await get_business(session, chat_id)
        user, is_new = await get_or_create_user(session, chat_id)

    if user and user.is_super_admin:
        await update.message.reply_text(
            "👑 *Super Admin* — Ardi AI Platform",
            reply_markup=_super_admin_kb(),
            parse_mode="Markdown",
        )
    elif business:
        await update.message.reply_text(
            _t("welcome_registered", lang, name=business.name),
            reply_markup=business_kb(),
        )
    elif is_new:
        context.user_data["just_registered"] = True
        await update.message.reply_text(
            _t("choose_language", "en"),
            reply_markup=lang_kb(),
        )
    else:
        await update.message.reply_text(
            _t("welcome_unregistered", lang) if lang == "am" else "Welcome back! What would you like to do?",
            reply_markup=guest_kb(),
        )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    lang = await get_user_language(update.effective_chat.id)

    if data == "register":
        return await cmd_register(update, context)
    elif data == "addproduct":
        return await cmd_addproduct(update, context)
    elif data == "catalog":
        return await cmd_catalog(update, context)
    elif data == "connectchannel":
        return await cmd_connectchannel(update, context)
    elif data == "ai_settings":
        return await cmd_ai_settings(update, context)
    elif data == "share":
        return await cmd_share(update, context)
    elif data == "hours":
        return await cmd_business_hours(update, context)
    elif data == "browse_businesses":
        return await cmd_businesses(update, context)
    elif data == "language":
        await update.effective_message.reply_text(
            _t("choose_language", lang),
            reply_markup=lang_kb(),
        )
        return
    elif data and data.startswith("chat_business_"):
        bus_id = _parse_id_suffix(data, "chat_business_")
        if bus_id is None:
            await query.edit_message_text("Invalid business link.")
            return
        return await start_customer_chat(update, context, bus_id)
    elif data == "main_menu":
        return await show_main_menu(update, context)
    elif data.startswith("cat_") or data == "noop":
        return await catalog_callback(update, context)


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = await get_user_language(chat_id)
    await update.message.reply_text(
        _t("choose_language", lang),
        reply_markup=lang_kb(),
    )


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    lang = "en" if "en" in query.data else "am"

    async with async_session() as session:
        user = await get_user(session, chat_id)
        if user:
            user.language = lang
            await session.commit()
    _user_cache[chat_id] = (lang, time.monotonic())
    context.user_data["lang"] = lang

    await query.edit_message_text(
        _t("lang_selected_en" if lang == "en" else "lang_selected_am", lang),
    )

    # Continue to welcome for new users
    if context.user_data.get("just_registered"):
        context.user_data.pop("just_registered", None)
        async with async_session() as session:
            business = await get_business(session, chat_id)
        if business:
            await query.message.reply_text(
                _t("welcome_registered", lang, name=business.name),
                reply_markup=business_kb(),
            )
        else:
            await query.message.reply_text(
                _t("welcome_unregistered", lang),
                reply_markup=guest_kb(),
            )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        user = await get_user(session, chat_id)
        business = await get_business(session, chat_id)

    context.user_data.pop("customer_chat_active", None)
    context.user_data.pop("customer_chat_business_id", None)

    if user and user.is_super_admin:
        text = "👑 Super Admin"
        kb = _super_admin_kb()
    elif business:
        text = f"Main Menu — {business.name}"
        kb = business_kb()
    else:
        text = "Main Menu"
        kb = guest_kb()

    await _send_or_edit(update, text, reply_markup=kb)
    return ConversationHandler.END


# ─── AI-Powered Registration ────────────────────────────────────────────────

async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        existing = await get_business(session, chat_id)
        if existing:
            await _send_or_edit(update, f"You are already registered as *{existing.name}*.", parse_mode="Markdown")
            return ConversationHandler.END

    # Start AI conversation
    context.user_data["reg_conversation"] = []
    context.user_data["reg_phase"] = "collecting"

    reply = await conduct_registration([])
    ardi_message = reply.get("reply", "Hello! I'm Ardi, your AI business assistant. What's your business name?")
    context.user_data["reg_conversation"].append({"role": "assistant", "text": ardi_message})

    await _send_or_edit(update, f"🤖 *Ardi AI Assistant*\n\n{ardi_message}", parse_mode="Markdown",
                        reply_markup=back_kb())
    return REGISTER_CONVERSATION


async def register_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    conversation = context.user_data.get("reg_conversation", [])
    if len(conversation) > 30:
        await update.message.reply_text("Let's start over — please use /register again.")
        context.user_data.pop("reg_conversation", None)
        return ConversationHandler.END

    conversation.append({"role": "user", "text": user_text})

    await update.message.reply_chat_action("typing")
    result = await conduct_registration(conversation)

    if result["type"] == "complete":
        data = result["data"]
        reply = result.get("reply", "Perfect! Let me save your information.")

        # Save to database
        try:
            async with async_session() as session:
                business = Business(
                    telegram_chat_id=chat_id,
                    name=data.get("name", "Unknown"),
                    description=data.get("description", ""),
                    address=data.get("address", ""),
                    phone=data.get("phone", ""),
                    subscription_status="trial",
                    trial_start=_utcnow(),
                    trial_end=_utcnow() + datetime.timedelta(days=7),
                )
                session.add(business)
                await session.commit()

            await update.message.reply_text(
                f"{reply}\n\n"
                f"✅ *Registration Complete!*\n"
                f"Welcome, *{data.get('name')}*!\n\n"
                f"Here's what to do next:\n"
                f"1️⃣ Add your products (tap ➕ Add Product)\n"
                f"2️⃣ Turn on Ardi AI with /ai so customers can chat with you\n"
                f"3️⃣ Get your shareable link\n\n"
                f"ለተመዘገቡ እንኳን ደህና መጡ! 🎉",
                parse_mode="Markdown",
                reply_markup=business_kb(),
            )
        except Exception as e:
            logger.error(f"Registration save error: {e}")
            await update.message.reply_text("Sorry, I couldn't save your information. Please try again with /register.")
        finally:
            context.user_data.pop("reg_conversation", None)
        return ConversationHandler.END
    elif result["type"] == "continue":
        reply = result.get("reply", "")
        conversation.append({"role": "assistant", "text": reply})
        context.user_data["reg_conversation"] = conversation
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=back_kb())
        return REGISTER_CONVERSATION
    else:
        await update.message.reply_text(result.get("reply", "Let me try again — what's your business name?"),
                                        reply_markup=back_kb())
        return REGISTER_CONVERSATION


# ─── Customer Chat ──────────────────────────────────────────────────────────

async def cmd_businesses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.ai_active == True,
                Business.subscription_status.in_(["trial", "active"]),
            ).order_by(Business.name)
        )
        all_businesses = result.scalars().all()
        # Filter out expired subscriptions (trial or active that have passed end date)
        now = _utcnow()
        active_businesses = []
        for b in all_businesses:
            # Guard against timezone-naive DB values
            te = b.trial_end
            se = b.subscription_end
            if te is not None and te.tzinfo is None:
                te = te.replace(tzinfo=datetime.UTC)
            if se is not None and se.tzinfo is None:
                se = se.replace(tzinfo=datetime.UTC)
            if b.subscription_status == "trial" and te and now >= te:
                continue
            if b.subscription_status == "active" and se and now >= se:
                continue
            active_businesses.append(b)
        # Exclude the user's own business so they don't chat with themselves
        user_biz = await get_business(session, chat_id)
        user_biz_id = user_biz.id if user_biz else None
        businesses = [b for b in active_businesses if b.id != user_biz_id]

    if not businesses:
        await _send_or_edit(update,
            "No businesses are currently available.\nCheck back later!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]))
        return

    msg = "*Businesses you can chat with:*\n\n"
    keyboard = []
    for b in businesses:
        desc = (b.description or "")[:60]
        msg += f"• *{b.name}* — {desc}\n"
        keyboard.append([InlineKeyboardButton(f"💬 Chat with {b.name}", callback_data=f"chat_business_{b.id}")])

    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    await _send_or_edit(update, msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def start_customer_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, bus_id: int = None):
    if bus_id is None:
        bus_id = context.user_data.get("customer_chat_business_id")
        if not bus_id:
            await _send_or_edit(update, "Please select a business using /businesses")
            return

    async with async_session() as session:
        result = await session.execute(select(Business).where(Business.id == bus_id))
        business = result.scalar_one_or_none()

    if not business or not business.ai_active:
        await _send_or_edit(update, "This business is not available for chat.")
        return

    context.user_data["customer_chat_business_id"] = business.id
    context.user_data["customer_chat_active"] = True

    await _send_or_edit(update,
        f"👋 You're now chatting with *{business.name}*\n\n"
        f"Ask about products, prices, availability, or get recommendations.\n"
        f"Send /endchat to stop.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]))


async def end_customer_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("customer_chat_active", None)
    context.user_data.pop("customer_chat_business_id", None)
    await update.message.reply_text(
        "Chat ended. Thanks for visiting!\n"
        "Use /businesses to find other businesses.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Browse Businesses", callback_data="browse_businesses")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])
    )


async def handle_customer_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Match a customer's photo to a product via file_id then embedding."""
    if not _has_photo(update):
        return
    if _is_media_group_duplicate(update):
        return
    bus_id = context.user_data.get("customer_chat_business_id")
    if not bus_id:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.business_id == bus_id)
        )
        products = result.scalars().all()
        if not products:
            await update.message.reply_text("This business has no products listed yet.")
            return

        business = await session.get(Business, bus_id)

    photo = update.message.photo[-1]
    file_id = photo.file_id

    # Step 1: exact file_id match
    for p in products:
        if p.photo_file_id == file_id:
            history = context.user_data.get("customer_chat_history", [])
            history.append({"role": "user", "text": f"[sent photo of {p.name}]"})
            context.user_data["customer_chat_history"] = history[-20:]
            await update.message.reply_text(f"I see you're interested in *{p.name}*! "
                                            f"{f'It is {p.price:.0f} ETB.' if p.price else ''} "
                                            f"Would you like to order it?",
                                            parse_mode="Markdown")
            return

    # Step 2: embedding similarity fallback
    await update.message.reply_text("📸 Analyzing your photo...")
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    caption = await generate_caption(bytes(image_bytes))
    if not caption:
        history = context.user_data.get("customer_chat_history", [])
        history.append({"role": "user", "text": "[sent a photo]"})
        context.user_data["customer_chat_history"] = history[-20:]
        await update.message.reply_text("I couldn't identify that photo. Could you describe what you're looking for?")
        return

    embedding = await embed_text(caption)

    loop = asyncio.get_event_loop()
    matches = await loop.run_in_executor(None, find_best_match_sync, caption, embedding, list(products))

    if matches:
        best = matches[0]
        p = best["product"]
        sim_pct = int(best["similarity"] * 100)
        history = context.user_data.get("customer_chat_history", [])
        history.append({"role": "user", "text": f"[sent photo matched to {p.name} ({sim_pct}%)]"})
        context.user_data["customer_chat_history"] = history[-20:]
        await update.message.reply_text(
            f"I found *{p.name}* ({sim_pct}% match)! "
            f"{f'Price: {p.price:.0f} ETB.' if p.price else ''} "
            f"Would you like to order it?",
            parse_mode="Markdown",
        )
    else:
        history = context.user_data.get("customer_chat_history", [])
        history.append({"role": "user", "text": f"[sent photo: {caption[:80]}]"})
        context.user_data["customer_chat_history"] = history[-20:]
        await update.message.reply_text(
            f"I couldn't find an exact match. Are you looking for something like: {caption}? "
            "Let me know what you need!"
        )


async def handle_customer_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a customer voice note, then process it as a text message."""
    voice = update.message.voice if update.message else None
    if not voice:
        return

    # Don't process voice during payment wait — same rule as text
    if context.user_data.get("state") == "awaiting_order_payment":
        await update.message.reply_text(
            "Please send a screenshot of your payment receipt so I can confirm your order."
        )
        return

    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await update.message.reply_text(VOICE_TOO_LONG)
        return

    await update.message.reply_chat_action("typing")

    try:
        file = await voice.get_file()
        audio_bytes = bytes(await file.download_as_bytearray())
    except Exception as e:
        logger.warning("Voice download failed: %s", e)
        await update.message.reply_text(VOICE_UNCLEAR)
        return

    if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
        await update.message.reply_text(VOICE_TOO_LONG if audio_bytes else VOICE_UNCLEAR)
        return

    transcript = await transcribe_voice(audio_bytes, voice.mime_type or "audio/ogg")
    if not transcript:
        await update.message.reply_text(VOICE_UNCLEAR)
        return

    # Reuse the full text pipeline (hours, subscription, sales reply, orders, escalation)
    return await handle_customer_message(update, context, _text_override=transcript)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router for direct voice notes: payment states, customer chat, or hint."""
    if not update.message or not update.message.voice:
        return

    state = context.user_data.get("state", "")
    if state == "awaiting_order_payment":
        await update.message.reply_text(
            "Please send your receipt (photo, PDF, or bank receipt link) so I can verify it."
        )
        return

    async with async_session() as session:
        business = await get_business(session, update.effective_chat.id)

    is_subscription = business and business.subscription_status == "awaiting_payment"
    if is_subscription:
        await update.message.reply_text("Subscriptions are paid online with Chapa. Use /plans to get your checkout link.")
        return

    if context.user_data.get("customer_chat_active"):
        return await handle_customer_voice(update, context)

    await update.message.reply_text(VOICE_NO_CHAT)


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE, _text_override: str | None = None):
    if not context.user_data.get("customer_chat_active"):
        return

    # Don't process text messages during payment wait — except receipt links/IDs
    if context.user_data.get("state") == "awaiting_order_payment":
        text = (update.message.text or "").strip()
        pending = context.user_data.get("pending_order")
        bank_hit = detect_bank_link(text) if pending else None
        if bank_hit:
            return await handle_bank_receipt(update, context, *bank_hit)
        link = looks_like_url(text) if pending else None
        if link:
            return await handle_receipt_link(update, context, link)
        await update.message.reply_text(
            "Please send your receipt — a photo, PDF, or bank receipt link — so I can confirm your order."
        )
        return

    bus_id = context.user_data.get("customer_chat_business_id")
    if not bus_id:
        return

    async with async_session() as session:
        result = await session.execute(select(Business).where(Business.id == bus_id))
        business = result.scalar_one_or_none()
        if not business or not business.ai_active:
            await update.message.reply_text("This business is no longer available.")
            context.user_data.pop("customer_chat_active", None)
            return

        if not _is_within_business_hours(business):
            msg = business.ai_offline_message or BUSINESS_HOURS_WARNING
            await update.message.reply_text(msg)
            return

        sub = _get_subscription_status(business)
        if not sub["active"]:
            await update.message.reply_text(
                "This business's subscription has expired. They are currently unavailable.",
            )
            context.user_data.pop("customer_chat_active", None)
            return

        products_result = await session.execute(
            select(Product).where(Product.business_id == business.id)
        )
        products = products_result.scalars().all()

    await update.message.reply_chat_action("typing")

    business_info = {
        "name": business.name,
        "description": business.description or "",
        "address": business.address or "",
        "phone": business.phone or "",
    }
    products_list = [{"name": p.name, "price": p.price, "available": p.available, "photo_caption": p.photo_caption} for p in products]

    order_payment_info = ""
    if business.orders_enabled and business.order_bank_name and business.order_bank_account:
        order_payment_info = (
            f"PAYMENT INFO — This business requires payment before order confirmation.\n"
            f"Bank: {business.order_bank_name}\n"
            f"Account: {business.order_bank_account}\n"
            f"Holder: {business.order_account_holder or business.order_bank_name}\n"
            f"After collecting delivery info, tell the customer the total and instruct them to:\n"
            f"1. Send payment to the account above\n"
            f"2. Send a screenshot of the receipt\n"
            f"Tell the customer: 'After you pay, send me the receipt screenshot and I'll confirm your order.'\n"
            f"Then output the ===ORDER=== marker with the collected data as normal."
        )

    history = context.user_data.get("customer_chat_history", [])
    msg_text = _text_override if _text_override is not None else update.message.text
    response = await generate_sales_response(business_info, products_list, msg_text, business.ai_tone, history, order_payment_info)

    history.append({"role": "user", "text": msg_text})
    reply_text = response["reply"]

    # Handle photo request marker
    photo_url_to_send = None
    if "===PHOTO===" in reply_text:
        parts = reply_text.split("===PHOTO===")
        reply_text = parts[0].strip()
        photo_product = parts[1].strip() if len(parts) > 1 else ""
        for p in products:
            if p.name.lower() == photo_product.lower():
                photo_url_to_send = p.photo_url
                break
        if not photo_url_to_send:
            for p in products:
                if p.photo_url:
                    photo_url_to_send = p.photo_url
                    break

    # Send photo before any reply if AI requested it
    if photo_url_to_send:
        try:
            await update.message.reply_photo(photo=photo_url_to_send)
        except Exception as e:
            logger.warning("Failed to send product photo: %s", e)

    if response.get("type") == "order":
        data = response["data"]

        # Validate delivery info — if AI skipped collecting it, ask again
        missing = []
        if not data.get("customer_name", "").strip():
            missing.append("your name")
        if not data.get("customer_phone", "").strip():
            missing.append("your phone number")
        if not data.get("customer_address", "").strip():
            missing.append("your delivery address")
        if missing:
            history.append({"role": "assistant", "text": f"[Missing delivery info: {', '.join(missing)}]"})
            context.user_data["customer_chat_history"] = history[-20:]
            await update.message.reply_text(
                f"{reply_text}\n\n"
                f"Could you also tell me {missing[0]}?",
            )
            return

        if business.orders_enabled and business.order_bank_name and business.order_bank_account:
            total = Decimal("0.00")
            lines = []
            for item in data.get("items", []):
                pname = item.get("product", "")
                qty = int(item.get("quantity", 1))
                for p in products:
                    if p.name.lower() == pname.lower():
                        line_total = _money(p.price) * qty
                        total += line_total
                        lines.append(f"• {pname} × {qty} — {line_total:.2f} ETB")
                        break
            ref = f"INV-{business.id}-{int(time.time()) % 1000000:06d}"
            context.user_data["pending_order"] = {
                "business_id": business.id,
                "data": data,
                "total": total,
                "ref": ref,
            }
            context.user_data["state"] = "awaiting_order_payment"

            # Instant option: business-owned Chapa checkout (money settles
            # directly to the business's Chapa account).
            chapa_url = None
            biz_key = (business.chapa_secret_key or "").strip()
            if biz_key.startswith("CHASECK-"):
                try:
                    pre_order = await _create_order(business, update.effective_user, data, products)
                    async with async_session() as _s:
                        _o = await _s.get(Order, pre_order.id)
                        _o.status = "awaiting_payment"
                        await _s.commit()
                    from config import MINI_APP_URL as _MURL
                    _base = (_miniapp_base() or (_MURL or "").strip().rstrip("/")) or "https://t.me"
                    _co = await chapa.create_checkout(
                        business, "order", float(total),
                        return_url=f"{_base}/pay/done",
                        callback_url=f"{_base}/api/chapa/webhook",
                        secret=biz_key,
                    )
                    if _co:
                        from db.models import OrderPayment
                        async with async_session() as _s2:
                            _s2.add(OrderPayment(
                                order_id=pre_order.id, business_id=business.id,
                                amount=total, tx_ref=_co["tx_ref"],
                                checkout_url=_co["checkout_url"],
                            ))
                            await _s2.commit()
                        chapa_url = _co["checkout_url"]
                        context.user_data["pending_order"]["order_id"] = pre_order.id
                        context.user_data["pending_order"]["chapa_tx"] = _co["tx_ref"]
                except Exception as e:
                    logger.warning("Order Chapa checkout failed, bank-only invoice: %s", e)

            pay_rows = []
            if chapa_url:
                pay_rows.append([InlineKeyboardButton("💳 Pay Online with Chapa", url=chapa_url)])
            pay_rows.append([InlineKeyboardButton("✅ I've Paid — Send Receipt", callback_data="ord_paid")])
            await update.message.reply_text(
                f"{reply_text}\n\n"
                f"🧾 *Invoice {ref}*\n"
                + "\n".join(lines) + "\n"
                f"*Total: {total:.2f} ETB*\n\n"
                f"Send payment to:\n"
                f"🏦 {business.order_bank_name}\n"
                f"Account: `{business.order_bank_account}`\n"
                f"Name: {business.order_account_holder or business.order_bank_name}\n\n"
                f"Then tap *I've Paid* and send your receipt "
                f"(photo, PDF, or bank receipt link) — I'll verify it automatically!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(pay_rows),
            )
            history.append({"role": "assistant", "text": f"[Invoice {ref} sent: {total:.2f} ETB, awaiting payment]"})
            context.user_data["customer_chat_history"] = history[-20:]
            return

        order = await _create_order(business, update.effective_user, data, products)
        items_text = ", ".join(f"{i.get('product','')} × {i.get('quantity',1)}" for i in (data.get("items", [data])))
        await update.message.reply_text(
            f"{reply_text}\n\n"
            f"✅ *Order Placed!*\n"
            f"• {items_text}\n"
            f"• Name: {data.get('customer_name', '')}\n"
            f"• Phone: {data.get('customer_phone', '')}\n"
            f"• Address: {data.get('customer_address', '')}\n\n"
            f"The business will contact you soon!",
            parse_mode="Markdown",
        )
        history.append({"role": "assistant", "text": f"[Order #{order.id} placed: {items_text}]"})
        await _notify_new_order(context, business, order, update.effective_user)
        if not (business.orders_enabled and business.order_bank_name and business.order_bank_account):
            # No invoice was sent: order payments aren't configured. Nudge the owner.
            try:
                await context.bot.send_message(
                    business.telegram_chat_id,
                    "⚠️ *Order without payment setup*\n\n"
                    f"Order #{order.id} was placed, but order payments aren't configured — "
                    "the customer got no invoice.\n\n"
                    "Fix it in the mini app → More → Payments:\n"
                    "• Turn ON “Accept orders”\n"
                    "• Fill bank name, account, holder → Save\n"
                    "• Optional: add your Chapa key for instant online checkout",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Payment-setup nudge failed: %s", e)

    elif response.get("type") == "escalate":
        data = response.get("data", {})
        reason = data.get("reason", "unspecified")
        await update.message.reply_text(
            f"{reply_text}\n\n"
            f"I've notified the business owner about your request. They'll get back to you soon.",
            parse_mode="Markdown",
        )
        await _notify_escalation(context, business, update.effective_user, reason, msg_text, reply_text)
        history.append({"role": "assistant", "text": f"[Escalated: {reason}]"})

    else:
        await update.message.reply_text(reply_text, parse_mode="Markdown")
        history.append({"role": "assistant", "text": reply_text})

    context.user_data["customer_chat_history"] = history[-20:]


# ─── Order Management ──────────────────────────────────────────────────────


async def create_order(business, customer_user, data, products, session=None):
    if session:
        return await _create_order_in_session(session, business, customer_user, data, products)
    return await _create_order(business, customer_user, data, products)


async def _create_order(business, customer_user, data, products):
    async with async_session() as s:
        return await _create_order_in_session(s, business, customer_user, data, products)


async def _create_order_in_session(s, business, customer_user, data, products):
    items_list = data.get("items", [])
    if not items_list:
        items_list = [{"product": data.get("product", ""), "quantity": data.get("quantity", 1)}]
    if not any(item.get("product") for item in items_list):
        raise ValueError("Order must have at least one item")

    customer_name = (data.get("customer_name") or "").strip()
    customer_phone = (data.get("customer_phone") or "").strip()
    customer_address = (data.get("customer_address") or "").strip()
    if not customer_name or not customer_phone or not customer_address:
        raise ValueError("Missing required delivery info (name, phone, address)")
    if not items_list:
        raise ValueError("Order must have at least one item")

    total = Decimal("0.00")
    validated_items = []
    for item in items_list:
        pname = item.get("product", "")
        qty = int(item.get("quantity", 1))
        unit_price = Decimal("0.00")
        for p in products:
            if p.name.lower() == pname.lower():
                unit_price = _money(p.price)
                break
        total += unit_price * qty
        validated_items.append((pname, qty, unit_price))

    order = Order(
        business_id=business.id,
        customer_telegram_id=customer_user.id if customer_user else None,
        customer_name=data.get("customer_name", ""),
        customer_phone=data.get("customer_phone", ""),
        customer_address=data.get("customer_address", ""),
        total_price=total,
    )
    s.add(order)
    await s.flush()

    order_items = []
    for pname, qty, unit_price in validated_items:
        oi = OrderItem(
            order_id=order.id,
            product_id=None,
            product_name=pname,
            quantity=qty,
            unit_price=unit_price,
        )
        s.add(oi)
        order_items.append(oi)
    await s.commit()
    return order


async def _notify_new_order(context, business, order, customer_user):
    try:
        customer_name = order.customer_name or (customer_user.full_name if customer_user else "Customer")
        async with async_session() as s:
            result = await s.execute(
                select(OrderItem).where(OrderItem.order_id == order.id)
            )
            order_items = result.scalars().all()
        items_text = "\n".join(
            f"• {i.product_name} × {i.quantity} — {i.unit_price:.0f} ETB" for i in order_items
        ) if order_items else f"• (no items)"
        await context.bot.send_message(
            chat_id=business.telegram_chat_id,
            text=f"🆕 *New Order!*\n\n"
                 f"From: {customer_name}\n"
                 f"{items_text}\n"
                 f"• Total: {order.total_price:.2f} ETB\n"
                 f"• Phone: {order.customer_phone or '—'}\n"
                 f"• Address: {order.customer_address or '—'}\n\n"
                 f"Order #{order.id}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data=f"order_confirm_{order.id}"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"order_cancel_{order.id}")],
                [InlineKeyboardButton("📋 All Orders", callback_data="orders_list")],
            ]),
        )
    except Exception as e:
        logger.error(f"Order notification failed: {e}")


async def _notify_escalation(context, business, customer_user, reason, customer_message, ai_reply):
    try:
        customer_name = customer_user.full_name if customer_user else "Customer"
        async with async_session() as session:
            esc = EscalatedChat(
                business_id=business.id,
                customer_telegram_id=customer_user.id if customer_user else None,
                customer_name=customer_name,
                reason=reason,
                last_customer_message=customer_message,
                last_ai_reply=ai_reply,
            )
            session.add(esc)
            await session.commit()
            esc_id = esc.id

        msg_text = (
            f"⚠️ *Customer needs help — Escalation #{esc_id}*\n\n"
            f"Customer: {customer_name}\n"
            f"Reason: {reason}\n\n"
            f"Them:\n_{customer_message}_\n\n"
            f"Ardi replied:\n_{ai_reply}_\n"
        )
        await context.bot.send_message(
            chat_id=business.telegram_chat_id,
            text=msg_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"💬 Reply to {customer_name}",
                                      callback_data=f"escalation_reply_{esc_id}")],
                [InlineKeyboardButton("✅ Resolved", callback_data=f"escalation_done_{esc_id}")],
            ]),
        )
    except Exception as e:
        logger.error(f"Escalation notification failed: {e}")


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    page = context.user_data.get("orders_page", 0)
    per_page = 10

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first with /register.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Register", callback_data="register")],
            ]))
            return

        count_result = await session.execute(
            select(Order.id).where(Order.business_id == business.id)
        )
        total = len(count_result.all())

        result = await session.execute(
            select(Order).where(Order.business_id == business.id)
            .order_by(Order.created_at.desc())
            .offset(page * per_page)
            .limit(per_page)
        )
        orders = result.scalars().all()

    if not orders and page == 0:
        await _send_or_edit(update, "No orders yet.", reply_markup=back_kb())
        return

    keyboard = []
    for o in orders:
        status_icon = {"pending": "🕐", "confirmed": "✅", "completed": "📦", "cancelled": "❌"}.get(o.status, "🕐")
        keyboard.append([InlineKeyboardButton(
            f"{status_icon} #{o.id} — {o.customer_name or 'Unknown'} — {o.total_price:.0f} ETB",
            callback_data=f"order_view_{o.id}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="orders_page_prev"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="orders_page_next"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])

    await _send_or_edit(update, f"*📋 Orders* (page {page + 1})\n\nTap an order to view details:",
                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def orders_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    direction = query.data.replace("orders_page_", "")
    page = context.user_data.get("orders_page", 0)
    if direction == "next":
        context.user_data["orders_page"] = page + 1
    elif direction == "prev" and page > 0:
        context.user_data["orders_page"] = page - 1
    return await cmd_orders(update, context)


async def order_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = _parse_id_suffix(query.data, "order_view_")
    if order_id is None:
        await query.edit_message_text("Invalid order link.")
        return

    async with async_session() as session:
        business = await _require_owner_business(session, update.effective_chat.id)
        if not business:
            await query.edit_message_text("Register your business first with /register.")
            return
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or not _owns_order(business, order):
            await query.edit_message_text("Order not found.")
            return
        items_result = await session.execute(select(OrderItem).where(OrderItem.order_id == order_id))
        items = items_result.scalars().all()

    items_text = "\n".join(f"• {i.product_name} × {i.quantity} — {i.unit_price:.0f} ETB" for i in items) or "No items"
    status_icon = {"pending": "🕐 Pending", "confirmed": "✅ Confirmed", "completed": "📦 Completed", "cancelled": "❌ Cancelled"}.get(order.status, order.status)

    await query.edit_message_text(
        f"*Order #{order.id}*\n\n"
        f"Status: {status_icon}\n"
        f"Customer: {order.customer_name or '—'}\n"
        f"Phone: {order.customer_phone or '—'}\n"
        f"Address: {order.customer_address or '—'}\n\n"
        f"*Items:*\n{items_text}\n\n"
        f"*Total:* {order.total_price:.2f} ETB\n"
        f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M')}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"order_confirm_{order.id}"),
             InlineKeyboardButton("📦 Mark Completed", callback_data=f"order_complete_{order.id}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"order_cancel_{order.id}")],
            [InlineKeyboardButton("🔙 Back to Orders", callback_data="orders_list"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]),
    )


async def order_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("Invalid order action.")
        return
    action = parts[0] + "_" + parts[1]
    order_id = _parse_id_suffix(data, f"{action}_")
    if order_id is None:
        await query.edit_message_text("Invalid order action.")
        return

    async with async_session() as session:
        business = await _require_owner_business(session, update.effective_chat.id)
        if not business:
            await query.edit_message_text("Register your business first with /register.")
            return
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or not _owns_order(business, order):
            await query.edit_message_text("Order not found.")
            return

        new_status = {"order_confirm": "confirmed", "order_complete": "completed", "order_cancel": "cancelled"}.get(action)
        if new_status:
            order.status = new_status
            await session.commit()

    await query.edit_message_text(f"✅ Order #{order_id} marked as *{new_status}*!", parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([
                                      [InlineKeyboardButton("📋 All Orders", callback_data="orders_list")],
                                      [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                                  ]))


# ─── Business AI Settings ───────────────────────────────────────────────────

async def cmd_ai_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register your business first with /register.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                ]))
            return

        status = "✅ ON" if business.ai_active else "❌ OFF"
        toggle_data = "deactivate_ai" if business.ai_active else "activate_ai"

        tone_display = business.ai_tone.capitalize()
        await _send_or_edit(update,
            f"*🤖 Ardi AI Settings*\n\n"
            f"Status: {status}\n"
            f"Tone: `{tone_display}`\n\n"
            f"When ON, Ardi AI will:\n"
            f"• Chat with your customers automatically\n"
            f"• Answer product questions and provide prices\n"
            f"• Recommend products based on what customers ask\n"
            f"• Speak in Amharic, English, or mixed — whatever the customer uses\n\n"
            f"Make sure you've added products with /addproduct first!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Turn {'OFF' if business.ai_active else 'ON'} Ardi AI",
                                      callback_data=toggle_data)],
                [InlineKeyboardButton("🎭 Change Tone", callback_data="tone_menu")],
                [InlineKeyboardButton("🔗 Get Shareable Link", callback_data="share")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
            ]),
        )


async def toggle_ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    activate = data == "activate_ai"

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await query.edit_message_text("Register first.")
            return
        business.ai_active = activate
        await session.commit()

    await cmd_ai_settings(update, context)


TONES_DISPLAY = {
    "friendly": "😊 Friendly — warm and chatty",
    "professional": "💼 Professional — polished and courteous",
    "casual": "😎 Casual — relaxed and chill",
    "formal": "🎩 Formal — proper and dignified",
    "witty": "😂 Witty — playful and fun",
}


async def cmd_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first with /register.")
            return
        current = business.ai_tone
    await _send_or_edit(update,
        f"*🎭 Ardi AI Tone*\n\n"
        f"Current: `{current.capitalize()}`\n\n"
        f"Choose how Ardi talks to your customers:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(TONES_DISPLAY[t], callback_data=f"set_tone_{t}")]
            for t in TONES_DISPLAY
        ] + [[InlineKeyboardButton("🔙 Back", callback_data="ai_settings")]]),
    )


async def tone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tone = query.data.replace("set_tone_", "")
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.ai_tone = tone
            await session.commit()
    await cmd_ai_settings(update, context)


# ─── Escalation Callbacks ──────────────────────────────────────────────────

async def escalation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data.startswith("escalation_reply_"):
        esc_id = _parse_id_suffix(data, "escalation_reply_")
        if esc_id is None:
            await query.edit_message_text("Invalid escalation link.")
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            esc = await session.get(EscalatedChat, esc_id)
            if not esc or not _owns_escalation(business, esc):
                await query.edit_message_text("Escalation not found.")
                return
        context.user_data["replying_to_escalation"] = esc_id
        await query.edit_message_text(
            f"Reply to Escalation #{esc_id}:\n\n"
            "Type your reply below. It will be sent to the customer.",
        )
        return

    if data.startswith("escalation_done_"):
        esc_id = _parse_id_suffix(data, "escalation_done_")
        if esc_id is None:
            await query.edit_message_text("Invalid escalation link.")
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            esc = await session.get(EscalatedChat, esc_id)
            if not esc or not _owns_escalation(business, esc):
                await query.edit_message_text("Escalation not found.")
                return
            esc.status = "resolved"
            await session.commit()
        await query.edit_message_text(f"✅ Escalation #{esc_id} marked as resolved.")
        return


async def handle_escalation_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    esc_id = context.user_data.get("replying_to_escalation")
    if not esc_id:
        return

    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await _require_owner_business(session, chat_id)
        esc = await session.get(EscalatedChat, esc_id)
        if not esc or not _owns_escalation(business, esc):
            await update.message.reply_text("Escalation not found.")
            context.user_data.pop("replying_to_escalation", None)
            return
        if esc.status != "open":
            await update.message.reply_text("This escalation has already been resolved.")
            context.user_data.pop("replying_to_escalation", None)
            return

        owner_business = await session.get(Business, esc.business_id)
        business_name = owner_business.name if owner_business else "the business"
        customer_telegram_id = esc.customer_telegram_id

    reply_text = update.message.text.strip()
    owner_name = update.effective_user.full_name or "Business Owner"

    try:
        if not customer_telegram_id:
            await update.message.reply_text("Cannot reply — customer info not available.")
            return
        msg = (
            f"📬 *Reply from {business_name}*\n\n"
            f"{reply_text}\n\n"
            f"— {owner_name}"
        )
        await context.bot.send_message(
            chat_id=customer_telegram_id,
            text=msg,
            parse_mode="Markdown",
        )
        await update.message.reply_text("✅ Your reply has been sent to the customer.")

        async with async_session() as session:
            esc = await session.get(EscalatedChat, esc_id)
            if esc:
                esc.status = "resolved"
                await session.commit()
    except Exception as e:
        logger.error("Escalation reply send error: %s", e)
        await update.message.reply_text("Couldn't send reply. The customer may have blocked the bot.")
    finally:
        context.user_data.pop("replying_to_escalation", None)


# ─── Business Hours ────────────────────────────────────────────────────────

async def cmd_business_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register your business first with /register.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                ]))
            return

        enabled = business.business_hours_enabled
        hours = f"{business.business_hours_start or '—'} to {business.business_hours_end or '—'}" if business.business_hours_start else "Not set"

        status = "✅ ON" if enabled else "❌ OFF"
        await _send_or_edit(update,
            f"*⏰ Business Hours Settings*\n\n"
            f"Status: {status}\n"
            f"Hours: `{hours}`\n\n"
            f"When enabled, Ardi AI will only respond during these hours.\n"
            f"Outside hours, customers get your offline message.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🕐 Set Hours", callback_data="hours_set")],
                [InlineKeyboardButton(f"Turn {'OFF' if enabled else 'ON'}", callback_data="hours_toggle")],
                [InlineKeyboardButton("✏️ Offline Message", callback_data="hours_offline_msg")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
            ]))
    return ConversationHandler.END


BUSINESS_HOURS_SET, BUSINESS_HOURS_MSG = range(10, 12)
ORDER_BANK_NAME, ORDER_BANK_ACCOUNT, ORDER_BANK_HOLDER = range(12, 15)


async def hours_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await _send_or_edit(update,
        "Enter your business hours in 24h format, e.g. `09:00-18:00`\n"
        "Or send `off` to disable.",
        parse_mode="Markdown",
        reply_markup=back_kb())
    return BUSINESS_HOURS_SET


async def hours_set_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    text = update.message.text.strip().lower()
    chat_id = update.effective_chat.id

    if text == "off":
        async with async_session() as session:
            business = await get_business(session, chat_id)
            if business:
                business.business_hours_enabled = False
                await session.commit()
        await update.message.reply_text("Business hours disabled.", reply_markup=business_kb())
        return ConversationHandler.END

    match = re.match(r"^(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})$", text)
    if not match:
        await update.message.reply_text("Invalid format. Use `09:00-18:00` (24h).", parse_mode="Markdown",
                                        reply_markup=back_kb())
        return BUSINESS_HOURS_SET

    start, end = match.group(1), match.group(2)

    def _valid_time(t):
        parts = t.split(":")
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59

    if not _valid_time(start) or not _valid_time(end):
        await update.message.reply_text("Invalid time. Hours must be 00-23 and minutes 00-59.", parse_mode="Markdown",
                                        reply_markup=back_kb())
        return BUSINESS_HOURS_SET

    # Normalize single-digit hours (e.g. "9:00" → "09:00")
    def _pad_time(t):
        parts = t.split(":")
        return f"{int(parts[0]):02d}:{parts[1]}"
    start, end = _pad_time(start), _pad_time(end)
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.business_hours_start = start
            business.business_hours_end = end
            business.business_hours_enabled = True
            await session.commit()

    await update.message.reply_text(
        f"✅ Business hours set: {start} to {end}",
        reply_markup=business_kb())
    return ConversationHandler.END


async def hours_offline_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await _send_or_edit(update,
        "Send the message customers will see when you're offline.\n\n"
        "Example: *We're currently closed. We'll reply during business hours.*\n\n"
        "Tap 🔙 Main Menu to keep current message.",
        parse_mode="Markdown",
        reply_markup=back_kb())
    return BUSINESS_HOURS_MSG


async def hours_offline_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.ai_offline_message = text
            await session.commit()
    await update.message.reply_text(
        "✅ Offline message saved!",
        reply_markup=business_kb())
    return ConversationHandler.END


async def hours_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.business_hours_enabled = not business.business_hours_enabled
            await session.commit()
    return await cmd_business_hours(update, context)


# ─── Order Settings ─────────────────────────────────────────────────────

async def cmd_order_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first.")
            return
        enabled = business.orders_enabled
        bank = business.order_bank_name or "Not set"
        account = business.order_bank_account or "Not set"
        holder = business.order_account_holder or "Not set"

    text = (
        f"*Order Settings — {business.name}*\n\n"
        f"Accept orders: {'✅ ON' if enabled else '❌ OFF'}\n"
        f"Bank: {bank}\n"
        f"Account: {account}\n"
        f"Account holder: {holder}\n\n"
        "When ON, customers pay before their order is confirmed."
    )
    keyboard = [
        [InlineKeyboardButton(f"{'❌ Turn OFF' if enabled else '✅ Turn ON'} Orders", callback_data="orders_toggle")],
        [InlineKeyboardButton("🏦 Set Bank Account", callback_data="orders_set_bank")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
    ]
    await _send_or_edit(update, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def orders_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.orders_enabled = not business.orders_enabled
            await session.commit()
    return await cmd_order_settings(update, context)


async def orders_set_bank_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _send_or_edit(update, "Send the bank name (e.g. CBE, Telebirr, Dashen):",
                        reply_markup=back_kb())
    return ORDER_BANK_NAME


async def orders_set_bank_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    context.user_data["order_bank_name"] = update.message.text.strip()
    await update.message.reply_text("Now send the account number or phone:", reply_markup=back_kb())
    return ORDER_BANK_ACCOUNT


async def orders_set_bank_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    context.user_data["order_bank_account"] = update.message.text.strip()
    await update.message.reply_text("Now send the account holder name:", reply_markup=back_kb())
    return ORDER_BANK_HOLDER


async def orders_set_bank_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    bank = context.user_data.get("order_bank_name", "")
    account = context.user_data.get("order_bank_account", "")
    holder = update.message.text.strip()

    async with async_session() as session:
        biz = await get_business(session, chat_id)
        if biz:
            biz.order_bank_name = bank
            biz.order_bank_account = account
            biz.order_account_holder = holder
            await session.commit()

    context.user_data.pop("order_bank_name", None)
    context.user_data.pop("order_bank_account", None)
    await update.message.reply_text(f"✅ Payment info saved!\nBank: {bank}\nAccount: {account}\nHolder: {holder}",
                                    reply_markup=business_kb())
    return ConversationHandler.END


async def cmd_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                ]))
            return

    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={BUSINESS_PREFIX}{business.id}"

    await _send_or_edit(update,
        f"*🔗 Share Your Ardi AI Link*\n\n"
        f"Send this link to your customers:\n"
        f"`{link}`\n\n"
        f"Customers open it and chat directly with Ardi AI about your products.\n\n"
        f"Also works on your website, Instagram bio, or any social media!\n\n"
        f"*Make sure Ardi AI is ON* (use /ai to check)",
        parse_mode="Markdown",
        reply_markup=back_kb())


# ─── Add Product ────────────────────────────────────────────────────────────

async def cmd_addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Please register your business first with /register.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                                ]))
            return ConversationHandler.END

    await _send_or_edit(update,
        "📸 Send me a photo of the product.\n\n"
        "Ardi AI will automatically identify the product name.\n"
        "Add the price in the caption (e.g., *500 birr*).",
        parse_mode="Markdown",
        reply_markup=back_kb())
    context.user_data["product_business_id"] = business.id
    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    if not _has_photo(update):
        await update.message.reply_text("Please send a photo (not a document or video).",
                                        reply_markup=back_kb())
        return ADD_PRODUCT_PHOTO
    if _is_media_group_duplicate(update):
        return ADD_PRODUCT_PHOTO
    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    file = await photo.get_file()
    photo_bytes = io.BytesIO()
    await file.download_to_memory(photo_bytes)
    photo_bytes = photo_bytes.getvalue()

    await update.message.reply_chat_action("typing")
    await update.message.reply_text("🔍 Ardi AI is analyzing your product photo...")
    result = await identify_product(photo_bytes)

    product_name = result.get("name", "unknown")
    price = None

    price_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(birr|etb|br)\b", caption, re.IGNORECASE)
    if price_match:
        price = _parse_price(price_match.group(1))

    business_id = context.user_data.get("product_business_id")
    photo_url = await upload_product_photo(photo_bytes, business_id, product_name)
    if not photo_url:
        photo_url = photo.file_id
        logger.info("R2 upload failed, falling back to Telegram file_id")

    context.user_data["product_name"] = product_name
    context.user_data["product_price"] = price
    context.user_data["product_photo_id"] = photo.file_id
    context.user_data["product_photo_url"] = photo_url

    msg = f"Ardi AI identified: *{product_name}*"
    if price is not None:
        msg += f"\nPrice: *{price:.2f} ETB*"
        keyboard = [
            [InlineKeyboardButton("✅ Save Product", callback_data="product_save")],
            [InlineKeyboardButton("✏️ Change Name", callback_data="product_rename")],
            [InlineKeyboardButton("✏️ Change Price", callback_data="product_reprice")],
            [InlineKeyboardButton("❌ Cancel", callback_data="product_cancel")],
        ]
    else:
        msg += "\n\nWhat's the price? (e.g., *500 birr*)"
        keyboard = None

    if keyboard:
        await update.message.reply_text(msg, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=back_kb())
    return ADD_PRODUCT_CONFIRM


async def _save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    business_id = context.user_data.get("product_business_id")
    name = context.user_data.get("product_name", "unknown")
    price = context.user_data.get("product_price")
    photo_id = context.user_data.get("product_photo_id")
    photo_url = context.user_data.get("product_photo_url")

    if not business_id:
        await _send_or_edit(update, "Session expired. Please start again with /addproduct.")
        return ConversationHandler.END

    try:
        caption = None
        embedding = None
        if photo_id and photo_url:
            try:
                file = await context.bot.get_file(photo_id)
                image_bytes = await file.download_as_bytearray()
                caption, embedding = await caption_and_embed(bytes(image_bytes))
            except Exception as e:
                logger.warning("Caption/embed generation failed: %s", e)

        async with async_session() as session:
            session.add(Product(
                business_id=business_id, name=name, price=price,
                photo_file_id=photo_id, photo_url=photo_url,
                photo_caption=caption,
                photo_embedding=json.dumps(embedding) if embedding else None,
            ))
            await session.commit()

        msg = f"✅ *{name}* saved!"
        if price:
            msg += f"\nPrice: *{price:.2f} ETB*"
        await _send_or_edit(update, msg + "\n\nWhat next?",
                            parse_mode="Markdown",
                            reply_markup=back_kb())
        if update.callback_query:
            await update.effective_chat.send_message("Use the menu below to continue.",
                                                     reply_markup=business_kb())
    except Exception as e:
        logger.error(f"Product save error: {e}")
        await _send_or_edit(update, "Failed to save product. Please try again.")
    return ConversationHandler.END


async def product_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "product_save":
        return await _save_product(update, context)
    elif data == "product_rename":
        await query.edit_message_text("What should the product name be?",
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton("❌ Cancel", callback_data="product_cancel")]]))
        context.user_data["awaiting_rename"] = True
        return ADD_PRODUCT_CONFIRM
    elif data == "product_reprice":
        await query.edit_message_text("Enter the price (e.g., *500 birr*):", parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton("❌ Cancel", callback_data="product_cancel")]]))
        context.user_data["awaiting_reprice"] = True
        return ADD_PRODUCT_CONFIRM
    elif data == "product_cancel":
        for k in ("awaiting_rename", "awaiting_reprice", "product_name",
                  "product_price", "product_photo_id", "product_caption"):
            context.user_data.pop(k, None)
        await query.edit_message_text("Cancelled.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                      ]))
        return ConversationHandler.END


async def add_product_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _exit_if_menu(update, context):
        return ConversationHandler.END
    text = update.message.text.strip()

    if context.user_data.get("awaiting_rename"):
        context.user_data["product_name"] = text
        context.user_data["awaiting_rename"] = False
        await update.message.reply_text(f"Name updated.")
        return await _save_product(update, context)
    elif context.user_data.get("awaiting_reprice"):
        price_match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if price_match:
            context.user_data["product_price"] = _parse_price(price_match.group(1))
            context.user_data["awaiting_reprice"] = False
            return await _save_product(update, context)
        else:
            await update.message.reply_text("Please enter a valid number (e.g., *500*).", parse_mode="Markdown",
                                            reply_markup=back_kb())
            return ADD_PRODUCT_CONFIRM
    else:
        # No price in caption, user is sending price as text
        price_match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if price_match:
            context.user_data["product_price"] = _parse_price(price_match.group(1))
            msg = f"Price set to: *{context.user_data['product_price']:.2f} ETB*"
            await update.message.reply_text(msg, parse_mode="Markdown",
                                            reply_markup=InlineKeyboardMarkup([
                                                [InlineKeyboardButton("✅ Save", callback_data="product_save")],
                                                [InlineKeyboardButton("❌ Cancel", callback_data="product_cancel")],
                                            ]))
            return ADD_PRODUCT_CONFIRM
        await update.message.reply_text("Please enter a price like *500 birr*.", parse_mode="Markdown",
                                        reply_markup=back_kb())
        return ADD_PRODUCT_CONFIRM


# ─── Catalog ────────────────────────────────────────────────────────────────

PRODUCTS_PER_PAGE = 5


async def _catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                ]))
            return

        products = await session.execute(
            select(Product).where(Product.business_id == business.id).order_by(Product.created_at.desc())
        )
        products = products.scalars().all()

    if not products:
        await _send_or_edit(update, "No products yet. Add your first using the menu below.",
                            reply_markup=back_kb())
        return

    total = len(products)
    total_pages = (total + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start = page * PRODUCTS_PER_PAGE
    end = start + PRODUCTS_PER_PAGE
    page_products = products[start:end]

    lines = [f"*{business.name} — Product Catalog*  ({page + 1}/{total_pages})\n"]
    keyboard = []

    for p in page_products:
        price_str = f"{p.price:.2f} ETB" if p.price else "No price"
        stock = "✅ In stock" if p.available else "❌ Out of stock"
        lines.append(f"• *{p.name}* — {price_str} — {stock}")
        keyboard.append([
            InlineKeyboardButton(f"🗑 Delete", callback_data=f"cat_del_{p.id}"),
            InlineKeyboardButton(f"💰 Price", callback_data=f"cat_pr_{p.id}"),
            InlineKeyboardButton(f"📦 Stock", callback_data=f"cat_tog_{p.id}"),
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cat_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cat_pg_{page + 1}"))
    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("➕ Add Product", callback_data="addproduct")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    await _send_or_edit(update, "\n".join(lines), parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _catalog_page(update, context, page=0)


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data.startswith("cat_pg_"):
        page = _parse_id_suffix(data, "cat_pg_")
        if page is None or page < 0:
            page = 0
        await _catalog_page(update, context, page=page)

    elif data.startswith("cat_del_yes_"):
        prod_id = _parse_id_suffix(data, "cat_del_yes_")
        if prod_id is None:
            await _catalog_page(update, context)
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            p = await session.get(Product, prod_id)
            if p and _owns_product(business, p):
                await session.delete(p)
                await session.commit()
            else:
                await query.edit_message_text("Product not found.")
                return
        await _catalog_page(update, context)

    elif data.startswith("cat_del_no_"):
        await _catalog_page(update, context)

    elif data.startswith("cat_del_"):
        prod_id = _parse_id_suffix(data, "cat_del_")
        if prod_id is None:
            await _catalog_page(update, context)
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            p = await session.get(Product, prod_id)
            if not p or not _owns_product(business, p):
                await query.edit_message_text("Product not found.")
                return
        await _send_or_edit(update, "Are you sure you want to delete this product?",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ Yes, delete", callback_data=f"cat_del_yes_{prod_id}"),
                                 InlineKeyboardButton("❌ No", callback_data=f"cat_del_no_{prod_id}")],
                            ]))

    elif data.startswith("cat_pr_"):
        prod_id = _parse_id_suffix(data, "cat_pr_")
        if prod_id is None:
            await _catalog_page(update, context)
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            p = await session.get(Product, prod_id)
            if not p or not _owns_product(business, p):
                await query.edit_message_text("Product not found.")
                return
        context.user_data["state"] = f"awaiting_price_for_{prod_id}"
        await _send_or_edit(update, "What's the new price? (in ETB, e.g. 50)")

    elif data.startswith("cat_tog_"):
        prod_id = _parse_id_suffix(data, "cat_tog_")
        if prod_id is None:
            await _catalog_page(update, context)
            return
        async with async_session() as session:
            business = await _require_owner_business(session, chat_id)
            p = await session.get(Product, prod_id)
            if p and _owns_product(business, p):
                p.available = not p.available
                await session.commit()
            else:
                await query.edit_message_text("Product not found.")
                return
        await _catalog_page(update, context)


# ─── Connect Channel ────────────────────────────────────────────────────────

async def cmd_connectchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await _send_or_edit(update, "Register first.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚀 Register", callback_data="register")],
                                ]))
            return

    await _send_or_edit(update,
        "📢 *Connect a Telegram Channel*\n\n"
        f"1. Add @{context.bot.username} as an admin to your channel\n"
        "2. Forward any message from the channel to this chat\n"
        "3. Ardi AI will scan and save products from channel posts\n\n"
        "New product posts (photos with price captions) will be saved automatically.",
        parse_mode="Markdown",
        reply_markup=back_kb())


async def _import_channel_photo(business_id: int, photo, caption: str):
    """Save a channel (or forwarded channel) photo as a product.

    Requires a price in the caption (e.g. '500 birr'). Returns the
    Product, or None when there is no readable price / identification fails.
    """
    price_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(birr|etb|br)\b", caption or "", re.IGNORECASE)
    if not price_match:
        return None
    try:
        file = await photo.get_file()
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        photo_bytes = buf.getvalue()
    except Exception as e:
        logger.warning("Channel photo download failed: %s", e)
        return None

    result = await identify_product(photo_bytes)
    product_name = result.get("name", "unknown") or "unknown"
    price = _parse_price(price_match.group(1))
    photo_url = await upload_product_photo(photo_bytes, business_id, product_name)

    try:
        async with async_session() as session:
            p = Product(business_id=business_id, name=product_name, price=price,
                        photo_file_id=photo.file_id, photo_url=photo_url)
            session.add(p)
            await session.commit()
            return p
    except Exception as e:
        logger.error("Channel product save failed: %s", e)
        return None


async def handle_forwarded_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message if update else None
    if not msg:
        return

    chat_id = update.effective_chat.id
    is_forward = bool(getattr(msg, "forward_date", None) or getattr(msg, "forward_from_chat", None)
                      or getattr(msg, "forward_origin", None) or getattr(msg, "forward_from", None))

    # Channel source across Bot API generations (forward_from_chat pre-7.x, forward_origin 7.x+).
    channel = getattr(msg, "forward_from_chat", None)
    if channel is None:
        origin = getattr(msg, "forward_origin", None)
        channel = getattr(origin, "chat", None)
    logger.info("forward received: chat=%s fwd=%s photo=%s channel=%s",
                chat_id, is_forward, bool(msg.photo),
                getattr(channel, "id", None) if channel else None)

    if channel is None or getattr(channel, "type", None) != "channel":
        if is_forward:
            await update.message.reply_text("Please forward a message from a channel.")
        return

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await update.message.reply_text("Register first with /register.")
            return

        try:
            member = await channel.get_member(context.bot.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    f"I need to be an admin in @{channel.username or channel.title}.\n"
                    f"Add @{context.bot.username} as admin and try again.")
                return
        except Exception:
            await update.message.reply_text(
                f"Could not verify. Make sure @{context.bot.username} is an admin in the channel.")
            return

        business.channel_id = channel.id
        await session.commit()
        biz_id = business.id

    name = channel.title or channel.username or "channel"

    # Scrape the forwarded message itself when it carries a product photo.
    fwd = update.message
    if fwd.photo:
        saved = await _import_channel_photo(biz_id, fwd.photo[-1], fwd.caption or "")
        if saved:
            await update.message.reply_text(
                f"✅ Channel '{name}' connected!\n\n"
                f"📦 Product saved: *{saved.name}*"
                f"{f' — {saved.price:.0f} ETB' if saved.price else ''}",
                parse_mode="Markdown",
                reply_markup=back_kb())
            return
        await update.message.reply_text(
            f"✅ Channel '{name}' connected!\n\n"
            "That photo has no readable price — add the price in the caption "
            "(e.g. `500 birr`) and forward it again to save it as a product.",
            parse_mode="Markdown",
            reply_markup=back_kb())
        return

    await update.message.reply_text(
        f"✅ Channel '{name}' connected! New product posts will be saved automatically.",
        reply_markup=back_kb())


async def scan_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await update.message.reply_text("Register first with /register.")
            return

    if not context.args:
        await update.message.reply_text("Usage: /scanchannel @channelusername")
        return

    username = context.args[0].lstrip("@")

    try:
        chat = await context.bot.get_chat(f"@{username}")
        member = await chat.get_member(context.bot.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("I need to be an admin in that channel.")
            return
    except Exception as e:
        logger.error(f"Channel access error: {e}")
        await update.message.reply_text("Could not access the channel. Make sure I am an admin.")
        return

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if business:
            business.channel_id = chat.id
            await session.commit()

    await update.message.reply_text(
        f"✅ Channel @{username} connected!\n\n"
        "Ardi AI will now monitor this channel for new product posts.\n"
        "To add existing posts:\n"
        "• Forward them to this chat — I'll scan and save them automatically.\n\n"
        "New posts with photos + prices (e.g. '500 birr') will be saved automatically.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Products", callback_data="catalog")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]))


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.photo:
        return

    message = update.channel_post
    caption = message.caption or ""
    channel_id = message.chat_id

    async with async_session() as session:
        result = await session.execute(select(Business).where(Business.channel_id == channel_id))
        business = result.scalar_one_or_none()
    if not business:
        return

    await _import_channel_photo(business.id, message.photo[-1], caption)


# ─── Telegram Business Integration ──────────────────────────────────────────

async def cmd_sync_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sync business connection: ask user to disconnect and reconnect."""
    chat_id = update.effective_chat.id

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await update.message.reply_text("Register your business first with /register.")
            return

    # Check if already connected
    async with async_session() as session:
        existing = await session.execute(
            select(BusinessConnectionModel).where(BusinessConnectionModel.user_chat_id == chat_id)
        )
        conn = existing.scalar_one_or_none()
        if conn:
            await update.message.reply_text(
                f"✅ Already connected! Your Ardi AI is active.\n"
                f"Connection ID: `{conn.connection_id}`",
                parse_mode="Markdown",
            )
            return

    bot_username = context.bot.username
    await update.message.reply_text(
        f"📋 *Sync Ardi AI with Your Telegram Business*\n\n"
        "Step 1: Open Telegram Settings → Business → Chat Automation\n"
        f"Step 2: If @{bot_username} is already there, **disconnect it first**\n"
        f"Step 3: **Reconnect** @{bot_username}\n\n"
        "I'll detect the connection and confirm here within a few seconds. 🚀",
        parse_mode="Markdown",
    )


async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if not conn:
        return

    user_chat_id = conn.user_chat_id
    connection_id = conn.id
    logger.info(f"Business connection update: conn={connection_id} user={user_chat_id} enabled={conn.is_enabled}")

    async with async_session() as session:
        business = await get_business(session, user_chat_id)
        if not business:
            logger.warning(f"Business connection from unregistered user: {user_chat_id}")
            return

        if not conn.is_enabled:
            logger.info(f"Business disconnected: {business.name}")
            result = await session.execute(
                select(BusinessConnectionModel).where(BusinessConnectionModel.connection_id == connection_id)
            )
            existing = result.scalar_one_or_none()
            if existing:
                await session.delete(existing)
                await session.commit()
            return

        logger.info(f"Business connected: {business.name} (id={business.id})")
        # Remove any stale connections for this business
        old = await session.execute(
            select(BusinessConnectionModel).where(BusinessConnectionModel.business_id == business.id)
        )
        for stale in old.scalars().all():
            await session.delete(stale)
        session.add(BusinessConnectionModel(business_id=business.id, connection_id=connection_id, user_chat_id=user_chat_id))
        await session.commit()

        try:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text=f"✅ Ardi AI connected to your Telegram Business!\n\n"
                     f"Customers who message you will now get AI-powered replies.\n"
                     f"Make sure Ardi AI is ON with /ai",
            )
        except Exception:
            pass


async def _transcribe_business_voice(context, connection_id: str, customer_chat_id: int, voice) -> str | None:
    """Download + transcribe a voice note from a Telegram Business chat."""
    async def _tell(text: str):
        try:
            await context.bot.send_message(chat_id=customer_chat_id, text=text,
                                           business_connection_id=connection_id)
        except Exception as e:
            logger.warning("Business voice reply failed (conn=%s): %s", connection_id, e)

    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await _tell(VOICE_TOO_LONG)
        return None
    try:
        file = await voice.get_file()
        audio_bytes = bytes(await file.download_as_bytearray())
    except Exception as e:
        logger.warning("Business voice download failed (conn=%s): %s", connection_id, e)
        await _tell(VOICE_UNCLEAR)
        return None
    if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
        await _tell(VOICE_TOO_LONG if audio_bytes else VOICE_UNCLEAR)
        return None
    transcript = await transcribe_voice(audio_bytes, voice.mime_type or "audio/ogg")
    if not transcript:
        await _tell(VOICE_UNCLEAR)
        return None
    return transcript


async def handle_business_photo(context, connection_id: str, customer_chat_id: int, message) -> None:
    """Customer sent a photo first in a Business chat: match catalog + reply.

    Mirrors the direct-chat photo flow, replying through the business connection.
    """
    global _business_chat_histories
    logger.info("Business photo message from customer %s on connection %s", customer_chat_id, connection_id)

    async def _send(text: str):
        await context.bot.send_message(chat_id=customer_chat_id, text=text,
                                       parse_mode="Markdown",
                                       business_connection_id=connection_id)

    async with async_session() as session:
        conn_model = (await session.execute(
            select(BusinessConnectionModel).where(
                BusinessConnectionModel.connection_id == connection_id)
        )).scalar_one_or_none()
        if not conn_model:
            logger.warning(f"Unknown business connection: {connection_id}")
            return
        business = await session.get(Business, conn_model.business_id)
        if not business:
            logger.warning(f"Business not found for connection: {connection_id}")
            return
        if not business.ai_active:
            logger.info(f"Ardi AI is OFF for {business.name}")
            return
        if not _is_within_business_hours(business):
            try:
                await _send(business.ai_offline_message or BUSINESS_HOURS_WARNING)
            except Exception as e:
                logger.error("Offline message send error: %s", e)
            return
        products = (await session.execute(
            select(Product).where(Product.business_id == business.id)
        )).scalars().all()

    if not products:
        try:
            await _send("This business has no products listed yet.")
        except Exception as e:
            logger.error("Business message send error: %s", e)
        return

    chat_key = f"{connection_id}_{customer_chat_id}"
    history = _business_chat_histories.get(chat_key, [])

    photo = message.photo[-1]
    for p in products:
        if p.photo_file_id == photo.file_id:
            history.append({"role": "user", "text": f"[sent photo of {p.name}]"})
            history.append({"role": "assistant",
                            "text": f"[Matched {p.name} by photo]"})
            _business_chat_histories[chat_key] = history[-20:]
            try:
                await _send(f"I see you're interested in *{p.name}*! "
                            f"{f'It is {p.price:.0f} ETB.' if p.price else ''} "
                            f"Would you like to order it?")
            except Exception as e:
                logger.error("Business message send error: %s", e)
            return

    try:
        await _send("📸 Analyzing your photo...")
    except Exception:
        pass
    try:
        file = await photo.get_file()
        image_bytes = bytes(await file.download_as_bytearray())
    except Exception as e:
        logger.warning("Business photo download failed (conn=%s): %s", connection_id, e)
        return

    caption = await generate_caption(image_bytes)
    if not caption:
        history.append({"role": "user", "text": "[sent a photo]"})
        _business_chat_histories[chat_key] = history[-20:]
        try:
            await _send("I couldn't identify that photo. Could you describe what you're looking for?")
        except Exception as e:
            logger.error("Business message send error: %s", e)
        return

    embedding = await embed_text(caption)
    loop = asyncio.get_running_loop()
    matches = await loop.run_in_executor(None, find_best_match_sync, caption, embedding, list(products))

    if matches:
        best = matches[0]
        p = best["product"]
        sim_pct = int(best["similarity"] * 100)
        history.append({"role": "user", "text": f"[sent photo matched to {p.name} ({sim_pct}%)]"})
        reply = (f"I found *{p.name}* ({sim_pct}% match)! "
                 f"{f'Price: {p.price:.0f} ETB.' if p.price else ''} "
                 f"Would you like to order it?")
    else:
        history.append({"role": "user", "text": f"[sent photo: {caption[:80]}]"})
        reply = (f"I couldn't find an exact match. Are you looking for something like: {caption}? "
                 "Let me know what you need!")
    history.append({"role": "assistant", "text": reply[:200]})
    if len(_business_chat_histories) >= BUSINESS_CHAT_HISTORIES_MAX:
        _business_chat_histories.pop(next(iter(_business_chat_histories)))
    _business_chat_histories[chat_key] = history[-20:]
    try:
        await _send(reply)
    except Exception as e:
        logger.error("Business message send error: %s", e)


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _business_chat_histories
    message = update.business_message
    if not message:
        return

    connection_id = message.business_connection_id
    customer_chat_id = message.chat_id

    if message.text:
        customer_text = message.text
    elif message.photo:
        return await handle_business_photo(context, connection_id, customer_chat_id, message)
    elif message.voice:
        customer_text = await _transcribe_business_voice(context, connection_id, customer_chat_id, message.voice)
        if not customer_text:
            return
    else:
        return

    logger.info("Business text message from customer %s on connection %s", customer_chat_id, connection_id)

    async with async_session() as session:
        result = await session.execute(
            select(BusinessConnectionModel).where(BusinessConnectionModel.connection_id == connection_id)
        )
        conn_model = result.scalar_one_or_none()
        if not conn_model:
            logger.warning(f"Unknown business connection: {connection_id}")
            return

        business = await session.get(Business, conn_model.business_id)
        if not business:
            logger.warning(f"Business not found for connection: {connection_id}")
            return
        if not business.ai_active:
            logger.info(f"Ardi AI is OFF for {business.name}")
            return

        if not _is_within_business_hours(business):
            msg = business.ai_offline_message or BUSINESS_HOURS_WARNING
            try:
                await context.bot.send_message(
                    chat_id=customer_chat_id, text=msg,
                    business_connection_id=connection_id,
                )
            except Exception as e:
                logger.error("Offline message send error: %s", e)
            return

        products_result = await session.execute(
            select(Product).where(Product.business_id == business.id)
        )
        products = products_result.scalars().all()

    business_info = {
        "name": business.name,
        "description": business.description or "",
        "address": business.address or "",
        "phone": business.phone or "",
    }
    products_list = [{"name": p.name, "price": p.price, "available": p.available, "photo_caption": p.photo_caption} for p in products]

    chat_key = f"{connection_id}_{customer_chat_id}"
    history = _business_chat_histories.get(chat_key, [])

    order_payment_info = ""
    if business.orders_enabled and business.order_bank_name and business.order_bank_account:
        order_payment_info = (
            f"PAYMENT INFO — This business requires payment before order confirmation.\n"
            f"Bank: {business.order_bank_name}\n"
            f"Account: {business.order_bank_account}\n"
            f"Holder: {business.order_account_holder or business.order_bank_name}\n"
        )

    try:
        await context.bot.send_chat_action(chat_id=customer_chat_id, action="typing",
                                            business_connection_id=connection_id)
    except Exception as e:
        logger.warning("Typing indicator failed for business message (conn=%s): %s", connection_id, e)

    response = await generate_sales_response(business_info, products_list, customer_text, business.ai_tone, history, order_payment_info)

    history.append({"role": "user", "text": customer_text})
    reply_text = response["reply"]

    # Handle photo request marker
    photo_url_to_send = None
    if "===PHOTO===" in reply_text:
        parts = reply_text.split("===PHOTO===")
        reply_text = parts[0].strip()
        photo_product = parts[1].strip() if len(parts) > 1 else ""
        for p in products:
            if p.name.lower() == photo_product.lower():
                photo_url_to_send = p.photo_url
                break
        # Fallback: try first product with a photo
        if not photo_url_to_send:
            for p in products:
                if p.photo_url:
                    photo_url_to_send = p.photo_url
                    break

    if response.get("type") == "order":
        data = response["data"]
        order = await _create_order(business, None, data, products)
        items_text = ", ".join(f"{i.get('product','')} × {i.get('quantity',1)}" for i in (data.get("items", [data])))
        reply_text += (f"\n\n✅ *Order Placed!*\n"
                       f"• {items_text}\n"
                       f"• Your order #{order.id} has been received.")
        history.append({"role": "assistant", "text": f"[Order #{order.id} placed via Telegram Business: {items_text}]"})
        await _notify_new_order(context, business, order, None)

    elif response.get("type") == "escalate":
        data = response.get("data", {})
        owner_chat_id = business.telegram_chat_id
        reason = data.get("reason", "unspecified")
        customer_name = message.from_user.full_name or "Customer"
        async with async_session() as session:
            esc = EscalatedChat(
                business_id=business.id,
                customer_telegram_id=customer_chat_id,
                customer_name=customer_name,
                reason=reason,
                last_customer_message=customer_text,
                last_ai_reply=reply_text,
            )
            session.add(esc)
            await session.commit()
        try:
            await context.bot.send_message(
                chat_id=owner_chat_id,
                text=f"⚠️ *Customer needs help — escalation*\n\n"
                     f"From: {customer_name}\n"
                     f"Reason: {reason}\n\n"
                     f"Customer message:\n_{customer_text}_\n\n"
                     f"Ardi said:\n_{reply_text}_\n\n"
                     f"Reply directly to this customer through your Telegram Business settings.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Escalation notification error: %s", e)
        history.append({"role": "assistant", "text": f"[Escalated: {reason}]"})

    else:
        history.append({"role": "assistant", "text": reply_text[:200]})

    # Trim and store history
    if len(_business_chat_histories) >= BUSINESS_CHAT_HISTORIES_MAX:
        _business_chat_histories.pop(next(iter(_business_chat_histories)))
    _business_chat_histories[chat_key] = history[-20:]

    # Send photo first if AI requested it
    if photo_url_to_send:
        try:
            await context.bot.send_photo(
                chat_id=customer_chat_id,
                photo=photo_url_to_send,
                business_connection_id=connection_id,
            )
        except Exception as e:
            logger.warning("Failed to send product photo: %s", e)

    try:
        await context.bot.send_message(
            chat_id=customer_chat_id,
            text=reply_text,
            business_connection_id=connection_id,
        )
    except Exception as e:
        err_str = str(e)
        logger.error(f"Business message send error: %s", err_str)
        if "Business_peer_invalid" in err_str:
            async with async_session() as session:
                result = await session.execute(
                    select(BusinessConnectionModel).where(BusinessConnectionModel.connection_id == connection_id)
                )
                stale = result.scalar_one_or_none()
                if stale:
                    await session.delete(stale)
                    await session.commit()
                    logger.info("Deleted stale business connection: %s", connection_id)
            try:
                await context.bot.send_message(
                    chat_id=business.telegram_chat_id,
                    text=(
                        "⚠️ *Business Connection Lost*\n\n"
                        "Your Telegram Business connection is no longer valid.\n\n"
                        "Possible causes:\n"
                        "• Your Telegram Business subscription expired\n"
                        "• You disconnected the bot in Business settings\n\n"
                        "To fix: Open Telegram Settings → Business and check:\n"
                        "1. Your Business subscription is active\n"
                        "2. Chat Automation has @ardiassistantbot connected\n"
                        "3. Then use /sync to reconnect."
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass


# ─── Reply Keyboards ───────────────────────────────────────────────────────

MENU_BUTTONS = [
    ["➕ Add Product", "📋 My Products"],
    ["🤖 AI Settings", "📋 Orders"],
    ["🔗 Share Link", "⏰ Hours"],
    ["📢 Channel", "📋 Order Settings"],
    ["🌐 Open Dashboard"],
]

GUEST_BUTTONS = [
    ["🚀 Register My Business"],
    ["🔍 Browse Businesses"],
]

BUTTON_ACTIONS = {
    "➕ Add Product": "addproduct",
    "📋 My Products": "catalog",
    "🤖 AI Settings": "ai_settings",
    "📋 Orders": "orders",
    "🔗 Share Link": "share",
    "⏰ Hours": "hours",
    "📢 Channel": "connectchannel",
    "📋 Order Settings": "order_settings",
    "🌐 Open Dashboard": "open_dashboard",
    "🚀 Register My Business": "register",
    "🔍 Browse Businesses": "browse_businesses",
    "🔙 Main Menu": "main_menu",
}

# Dispatch: button action -> handler function (bypasses Gemini for keyboard buttons)
BUTTON_DISPATCH = {
    "register": cmd_register,
    "addproduct": cmd_addproduct,
    "catalog": cmd_catalog,
    "ai_settings": cmd_ai_settings,
    "orders": cmd_orders,
    "share": cmd_share,
    "hours": cmd_business_hours,
    "connectchannel": cmd_connectchannel,
    "order_settings": cmd_order_settings,
    "browse_businesses": cmd_businesses,
    "main_menu": show_main_menu,
}


# ─── Intent Handlers ─────────────────────────────────────────────────────

async def _intent_view_catalog(update, context, business, products, params):
    if not business:
        return "Register your business first with /register."
    if not products:
        return "No products yet. Add your first one!"
    lines = [f"*{business.name} — Products*"]
    for p in products:
        price = f"{p.price:.2f} ETB" if p.price else "No price"
        stock = "✅ In stock" if p.available else "❌ Out of stock"
        lines.append(f"• *{p.name}* — {price} ({stock})")
    return "\n".join(lines)


async def _intent_view_orders(update, context, business, products, params):
    if not business:
        return "Register your business first with /register."
    await cmd_orders(update, context)
    return None


async def _intent_view_settings(update, context, business, products, params):
    if not business:
        return "Register your business first with /register."
    await cmd_ai_settings(update, context)
    return None


async def _intent_toggle_ai(update, context, business, products, params):
    if not business:
        return "Register your business first."
    async with async_session() as session:
        b = await session.get(Business, business.id)
        if b:
            b.ai_active = not b.ai_active
            await session.commit()
            return f"Ardi AI is now {'ON' if b.ai_active else 'OFF'}."
    return "Couldn't toggle AI."


async def _intent_change_tone(update, context, business, products, params):
    if not business:
        return "Register your business first."
    tone = params.get("tone", "")
    valid = ["friendly", "professional", "casual", "formal", "witty"]
    if tone not in valid:
        return f"Available tones: {', '.join(valid)}"
    async with async_session() as session:
        b = await session.get(Business, business.id)
        if b:
            b.ai_tone = tone
            await session.commit()
            return f"Tone changed to *{tone}*."
    return "Couldn't change tone."


async def _intent_share_link(update, context, business, products, params):
    if not business:
        return "Register your business first."
    bot_username = context.bot.username
    return f"Share this link with customers:\n`https://t.me/{bot_username}?start=bus_{business.id}`"


async def _intent_add_product(update, context, business, products, params):
    if not business:
        return "Register your business first."

    pending = context.user_data.get("pending_product", {})
    name = params.get("product_name", "") or pending.get("name", "")

    # Detect hallucinated product names from example data
    known_fakes = {"coke", "pepsi", "sprite", "bread", "milk", "oil", "fanta"}
    if name.strip().lower() in known_fakes and not pending.get("name"):
        # Ask for confirmation — the AI may have made this up
        context.user_data["pending_product"] = {"name": name, "_flagged": True}
        context.user_data["state"] = "awaiting_product_price"
        return f"Is the product name really *{name}*? If yes, what's the price?"

    price = params.get("price") or pending.get("price")
    state = context.user_data.get("state", "idle")

    # Guard: if waiting for a price, try to extract a number from the raw message
    if state == "awaiting_product_price" and price is None:
        import re
        text = update.message.text.strip()
        m = re.search(r"(\d+(?:[.,]\d+)*)", text)
        if m:
            price = str(_parse_price(m.group(1)) or "")

    if name and price is not None:
        try:
            price_val = _parse_price(str(price))
            if price_val is None:
                raise ValueError(f"Invalid price: {price}")
            async with async_session() as session:
                session.add(Product(business_id=business.id, name=name, price=price_val))
                await session.commit()
            context.user_data.pop("state", None)
            context.user_data.pop("pending_product", None)
            return f"✅ *{name}* added for *{price_val:.2f} ETB*!"
        except (ValueError, TypeError):
            pass
        except Exception as e:
            logger.error("Add product error: %s", e)
            return "Couldn't add the product. Try using /addproduct with a photo instead."

    if name:
        context.user_data["pending_product"] = {"name": name}
        context.user_data["state"] = "awaiting_product_price"
        return f"What's the price for *{name}*?"

    if state == "awaiting_product_price" and not name:
        return "I didn't catch the price. How much does it cost in birr?"

    context.user_data["state"] = "awaiting_product_name"
    return "What's the product name?"


async def _intent_delete_product(update, context, business, products, params):
    if not business:
        return "Register your business first."
    name = params.get("product_name", "")
    if not name:
        return "Which product do you want to remove?"
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Product).where(Product.business_id == business.id)
            )
            for p in result.scalars().all():
                if p.name.lower() == name.lower():
                    await session.delete(p)
                    await session.commit()
                    return f"✅ *{p.name}* removed."
            return f"Product '{name}' not found."
    except Exception as e:
        logger.error("Delete product error: %s", e)
        return "Couldn't remove the product. Try again."


async def _intent_change_price(update, context, business, products, params):
    if not business:
        return "Register your business first."
    name = params.get("product_name", "")
    new_price = params.get("new_price")
    if not name or not new_price:
        return "Tell me the product name and new price."
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Product).where(Product.business_id == business.id)
            )
            for p in result.scalars().all():
                if p.name.lower() == name.lower():
                    p.price = float(new_price)
                    await session.commit()
                    return f"✅ *{p.name}* price updated to *{float(new_price):.2f} ETB*."
            return f"Product '{name}' not found."
    except Exception as e:
        logger.error("Change price error: %s", e)
        return "Couldn't change the price. Try again."


async def _intent_set_availability(update, context, business, products, params):
    if not business:
        return "Register your business first."
    name = params.get("product_name", "")
    available = params.get("available")
    if available is None:
        return "Tell me the product and whether it's in stock."
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Product).where(Product.business_id == business.id)
            )
            for p in result.scalars().all():
                if p.name.lower() == name.lower():
                    p.available = bool(available)
                    await session.commit()
                    status = "back in stock ✅" if p.available else "out of stock ❌"
                    return f"✅ *{p.name}* marked as {status}."
            return f"Product '{name}' not found."
    except Exception as e:
        logger.error("Set availability error: %s", e)
        return "Couldn't update the product. Try again."


async def _intent_set_hours(update, context, business, products, params):
    if not business:
        return "Register your business first."
    start = params.get("start", "")
    end = params.get("end", "")
    if not start or not end:
        return "Tell me the hours, e.g. 09:00 to 18:00"
    async with async_session() as session:
        b = await session.get(Business, business.id)
        if b:
            b.business_hours_start = start
            b.business_hours_end = end
            b.business_hours_enabled = True
            await session.commit()
            return f"✅ Business hours set: *{start}* to *{end}*."
    return "Couldn't set hours."


async def _intent_set_offline_msg(update, context, business, products, params):
    if not business:
        return "Register your business first."
    message = params.get("message", "")
    if not message:
        return "What should the offline message say?"
    async with async_session() as session:
        b = await session.get(Business, business.id)
        if b:
            b.ai_offline_message = message
            await session.commit()
            return "✅ Offline message saved!"
    return "Couldn't save message."


async def _intent_connect_channel(update, context, business, products, params):
    return "Add @{{}} as admin to your channel, then forward a post from it here.".format(context.bot.username)


async def _intent_register_business(update, context, business, products, params):
    if business:
        return f"You're already registered as *{business.name}*."
    return await cmd_register(update, context) or "Use /register to get started."


async def _intent_browse_businesses(update, context, business, products, params):
    await cmd_businesses(update, context)
    return None


async def _intent_show_help(update, context, business, products, params):
    return ("*Commands:* /register, /addproduct, /catalog, /orders, "
            "/ai, /tone, /share, /hours, /help")


def business_kb():
    return ReplyKeyboardMarkup(MENU_BUTTONS, resize_keyboard=True)


async def cmd_open_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _miniapp_base():
        await update.message.reply_text("Dashboard URL not configured.")
        return
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await update.message.reply_text("Please register your business first.")
            return
    from miniapp import generate_dash_token
    token = generate_dash_token(chat_id)
    dashboard_url = f"{_miniapp_url('/business')}?token={token}"
    keyboard = [[InlineKeyboardButton("🚀 Open Dashboard", web_app={"url": dashboard_url})]]
    await update.message.reply_text(
        "Tap the button below to open your business dashboard:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


BUTTON_DISPATCH["open_dashboard"] = cmd_open_dashboard


def guest_kb():
    return ReplyKeyboardMarkup(GUEST_BUTTONS, resize_keyboard=True)


def back_kb():
    return ReplyKeyboardMarkup([["🔙 Main Menu"]], resize_keyboard=True)


def remove_kb():
    return ReplyKeyboardRemove()


# ─── Rate limiting ────────────────────────────────────────────────────────────

_rate_limit_buckets: dict[int, list[float]] = {}
BUSINESS_CHAT_HISTORIES_MAX = 10_000
_business_chat_histories: dict[str, list[dict]] = {}


def _check_rate_limit(user_id: int) -> bool:
    global _rate_limit_buckets
    now = time.monotonic()
    window = RATE_LIMIT_WINDOW
    bucket = _rate_limit_buckets.get(user_id, [])
    bucket = [t for t in bucket if now - t < window]
    if len(bucket) >= RATE_LIMIT_CALLS:
        _rate_limit_buckets[user_id] = bucket
        return False
    bucket.append(now)
    _rate_limit_buckets[user_id] = bucket
    if len(_rate_limit_buckets) > 1000:
        _rate_limit_buckets = {
            uid: ts for uid, ts in _rate_limit_buckets.items()
            if any(now - t < window for t in ts)
        }
    return True


async def keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified router — EVERYTHING goes through the Ardi AI agent with Gemini function calling."""
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    if not _check_rate_limit(user_id):
        await update.message.reply_text("⏳ You're sending messages too fast. Please slow down.")
        return

    if context.user_data.get("replying_to_escalation"):
        return await handle_escalation_reply(update, context)

    text = update.message.text.strip()
    err = _validate_text(text)
    if err:
        await update.message.reply_text(err)
        return

    # ─── Pre-AI: route known keyboard buttons directly ──────────────────
    action = BUTTON_ACTIONS.get(text)
    if action and action in BUTTON_DISPATCH:
        # Clear customer-chat mode for business-owner buttons
        owner_keys = [k for k, v in BUTTON_DISPATCH.items() if k not in ("browse_businesses", "register")]
        if action in owner_keys:
            context.user_data.pop("customer_chat_active", None)
            context.user_data.pop("customer_chat_business_id", None)
        handler = BUTTON_DISPATCH[action]
        if action in ("addproduct", "register"):
            return await handler(update, context)
        await handler(update, context)
        return

    # ─── Handle change-price state (from catalog "💰 Price" button) ──────
    state = context.user_data.get("state", "")
    if state.startswith("awaiting_price_for_"):
        prod_id = int(state.split("_")[-1])
        try:
            price_val = float(text.replace("birr", "").replace("br", "").replace("ETB", "").strip())
            async with async_session() as session:
                p = await session.get(Product, prod_id)
                if p:
                    p.price = price_val
                    await session.commit()
                    context.user_data.pop("state", None)
                    await update.message.reply_text(f"✅ Price updated to *{price_val:.2f} ETB*!",
                                                    parse_mode="Markdown")
                    return
        except (ValueError, TypeError):
            pass
        await update.message.reply_text("Please enter a valid number (e.g. 50).")
        return

    if context.user_data.get("customer_chat_active"):
        return await handle_customer_message(update, context)

    await update.message.reply_chat_action("typing")

    # ─── Load business + products ───────────────────────────────────────
    async with async_session() as session:
        business = await get_business(session, update.effective_chat.id)
        products = []
        if business:
            result = await session.execute(
                select(Product).where(Product.business_id == business.id)
            )
            products = result.scalars().all()

    # ─── Subscription enforcement for business owners ────────────────────
    if business:
        sub = _get_subscription_status(business)
        if not sub["active"]:
            if business.subscription_status == "awaiting_payment":
                # Paid via Chapa but webhook missed? Settle silently on any message.
                if await _auto_settle_subscription(context, business):
                    return
            kb = None
            if _miniapp_base():
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Subscribe Now", web_app={"url": _miniapp_url("/business")})],
                ])
            await update.message.reply_text(
                f"⚠️ *Subscription Expired*\n\n"
                f"Your {sub['label']}. AI features are locked.\n\n"
                f"• Monthly: {(await get_plan_prices())['monthly']:,} ETB\n"
                f"• Yearly: {(await get_plan_prices())['yearly']:,} ETB (2 months free)\n\n"
                "Subscribe below to reactivate.",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

    products_text = "\n".join(
        f"- {p.name} ({p.price} ETB, {'in stock' if p.available else 'out of stock'})"
        for p in products
    ) if products else "No products yet."

    # ─── Build context for Gemini ───────────────────────────────────────
    state = context.user_data.get("state", "idle")
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    gemini_context = {
        "business_name": business.name if business else "",
        "owner_name": update.effective_user.full_name or "",
        "role": "business_owner" if business else "guest",
        "state": state,
        "date": today,
        "products_text": products_text,
    }

    # ─── Classify intent ────────────────────────────────────────────────
    try:
        result = await classify_intent(gemini_context, text)
    except Exception as e:
        logger.error("Gemini classify failed: %s", e)
        kb = business_kb() if business else guest_kb()
        await update.message.reply_text(
            "Sorry, I'm having trouble connecting. Please try again.",
            reply_markup=kb,
        )
        return

    # ─── Chat response ──────────────────────────────────────────────────
    if result.get("type") == "chat":
        reply = result.get("reply", "")
        cancel_keywords = ("cancel", "never mind", "forget it", "ignore", "start over")
        if context.user_data.get("state", "idle") != "idle" and any(k in text.lower() for k in cancel_keywords):
            context.user_data.pop("state", None)
            context.user_data.pop("pending_product", None)
        if reply:
            kb = business_kb() if business else guest_kb()
            await update.message.reply_text(reply, reply_markup=kb)
        return

    # ─── Action intent → route to handler ───────────────────────────────
    intent = result.get("intent", "")
    params = result.get("params", {})
    logger.info("Intent: %s | params: %s", intent, params)

    # ─── Role check: block business-owner intents for guests ────────────
    owner_intents = {
        "add_product", "delete_product", "change_price", "set_availability",
        "view_orders", "view_settings", "toggle_ai", "change_tone",
        "get_share_link", "set_business_hours", "set_offline_message",
    }
    if not business and intent in owner_intents:
        await update.message.reply_text(
            "You need to register a business first. Use /register or tap 🚀 Register My Business.",
            reply_markup=guest_kb(),
        )
        return

    intent_handlers = {
        "view_catalog": _intent_view_catalog,
        "view_orders": _intent_view_orders,
        "view_settings": _intent_view_settings,
        "toggle_ai": _intent_toggle_ai,
        "change_tone": _intent_change_tone,
        "get_share_link": _intent_share_link,
        "add_product": _intent_add_product,
        "delete_product": _intent_delete_product,
        "change_price": _intent_change_price,
        "set_availability": _intent_set_availability,
        "set_business_hours": _intent_set_hours,
        "set_offline_message": _intent_set_offline_msg,
        "register_business": _intent_register_business,
        "browse_businesses": _intent_browse_businesses,
        "show_help": _intent_show_help,
    }

    handler = intent_handlers.get(intent)
    if handler:
        reply = await handler(update, context, business, products, params)
    else:
        if business:
            reply = "I understood, but I couldn't process that request."
        else:
            reply = None
            await update.message.reply_text(
                "I'm not sure what you want to do. Use the buttons below or /help to see options.",
                reply_markup=guest_kb(),
            )

    if reply:
        if not isinstance(reply, str):
            reply = str(reply)
        kb = business_kb() if business else guest_kb()
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=kb)


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _send_or_edit(update: Update, text: str, **kwargs):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, **kwargs)
        except Exception as e:
            logger.warning("edit_message_text failed, falling back to reply_text: %s", e)
            await update.effective_message.reply_text(text, **kwargs)
    else:
        await update.effective_message.reply_text(text, **kwargs)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Ardi AI — Commands*\n\n"
        "*For Business Owners:*\n"
        "/register — Register your business with Ardi AI\n"
        "/addproduct — Add a product (send photo with price caption)\n"
        "/catalog — View all your products\n"
        "/ai — Turn Ardi AI on/off for customer chats\n"
        "/share — Get your shareable customer link\n"
        "/hours — Set business hours and offline message\n"
        "/connectchannel — Connect your Telegram channel\n"
        "/scanchannel — Connect a channel for auto-scanning\n"
        "/sync — Sync Telegram Business connection\n"
        "/trial — Check trial & subscription status\n"
        "/plans — View pricing and subscribe\n\n"
        "*For Customers:*\n"
        "/businesses — Browse businesses\n"
        "/endchat — End current chat\n\n"
        "/cancel — Cancel current action\n"
        "/help — Show this message",
        parse_mode="Markdown"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [k for k in context.user_data if k not in (
        "customer_chat_active", "customer_chat_business_id", "customer_chat_history",
        "pending_order", "state", "orders_page",
    )]
    for k in keys_to_clear:
        context.user_data.pop(k, None)
    async with async_session() as session:
        b = await get_business(session, update.effective_chat.id)
    await update.message.reply_text("Cancelled.", reply_markup=business_kb() if b else guest_kb())
    return ConversationHandler.END


# ─── Health Check ──────────────────────────────────────────────────────────

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple health check — bot is alive and DB is reachable."""
    try:
        async with async_session() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        await update.message.reply_text("✅ Ardi AI is running.\nDatabase: connected.\nAI engine: ready." if GEMINI_API_KEY else "AI engine not configured.")
    except Exception as e:
        await update.message.reply_text(f"❌ Health check failed: {e}")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: trigger a database backup."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("Admin only.")
        return
    from db.backup import backup_database, prune_backups
    msg = await update.message.reply_text("⏳ Backing up database...")
    path = await backup_database()
    if path:
        await prune_backups(keep=7)
        await msg.edit_text(f"✅ Backup saved: `{path}`\nOld backups pruned (kept last 7).", parse_mode="Markdown")
    else:
        await msg.edit_text("❌ Backup failed. Check logs.")


PRIVACY_TEXT = (
    "*Ardi AI — Privacy Policy*\n\n"
    "1. We store your Telegram ID, business name, product catalog, and order history solely to operate the service.\n"
    "2. We do not sell, share, or publish your data to third parties.\n"
    "3. Payment verification screenshots are processed by AI and discarded — we do not store them.\n"
    "4. You can request deletion of your data by contacting the admin.\n"
    "5. Data is stored on secure servers and retained only as long as your account is active."
)

TERMS_TEXT = (
    "*Ardi AI — Terms of Service*\n\n"
    "1. *Service*: Ardi AI provides AI-powered sales automation for Ethiopian businesses.\n"
    "2. *Subscription*: Monthly (1,200 ETB) or yearly (12,000 ETB) with a 7-day free trial.\n"
    "3. *Payments*: Paid via Telebirr or bank transfer. Service activates after payment confirmation.\n"
    "4. *Refunds*: No refunds for partial months. Contact admin for特殊情况.\n"
    "5. *Fair Use*: You may not use the service for spam, fraud, or illegal activity.\n"
    "6. *Availability*: We strive for 99% uptime but do not guarantee uninterrupted service.\n"
    "7. *Changes*: Terms may be updated with notice via the bot."
)


async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVACY_TEXT, parse_mode="Markdown")


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TERMS_TEXT, parse_mode="Markdown")


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard: show pending subscription confirmations and backup."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("Admin only.")
        return

    kb_buttons = [
        [InlineKeyboardButton("📋 Pending Payments", callback_data="admin_pending_payments")],
        [InlineKeyboardButton("💾 Backup DB", callback_data="admin_backup")],
        [InlineKeyboardButton("📊 Health Check", callback_data="admin_health")],
    ]
    await update.message.reply_text(
        "*Admin Dashboard*\n\nChoose an action:",
        reply_markup=InlineKeyboardMarkup(kb_buttons),
        parse_mode="Markdown",
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await query.edit_message_text("Admin only.")
        return

    data = query.data

    if data == "admin_health":
        try:
            async with async_session() as s:
                from sqlalchemy import text
                await s.execute(text("SELECT 1"))
            await query.edit_message_text(
                "✅ *Health*\nBot: running\nDB: connected\nAI: ready"
                if GEMINI_API_KEY else "AI engine not configured.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Health check failed: {e}")

    elif data == "admin_backup":
        from db.backup import backup_database, prune_backups
        await query.edit_message_text("⏳ Backing up...")
        path = await backup_database()
        if path:
            await prune_backups(keep=7)
            await query.edit_message_text(f"✅ Backup: `{path}`\nPruned (kept 7).", parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Backup failed.")

    elif data == "admin_pending_payments":
        async with async_session() as s:
            businesses = await s.execute(
                select(Business).where(
                    Business.subscription_status == "awaiting_payment"
                )
            )
            businesses = businesses.scalars().all()
        if not businesses:
            await query.edit_message_text("No pending payments.")
            return
        lines = []
        for b in businesses[:20]:
            lines.append(f"• {b.name} (status: {b.subscription_status}) — /sub_confirm_{b.id}")
        await query.edit_message_text(
            "*Pending Payments / Expired Subs*\n\n" + "\n".join(lines),
            parse_mode="Markdown",
        )


from config import MINI_APP_URL


def _super_admin_kb():
    buttons = []
    if _miniapp_base():
        buttons.append([InlineKeyboardButton("🚀 Open Admin Panel", web_app={"url": _miniapp_url("/")})])
    buttons += [
        [InlineKeyboardButton("📋 Pending Payments", callback_data="admin_pending_payments")],
        [InlineKeyboardButton("💾 Backup DB", callback_data="admin_backup")],
        [InlineKeyboardButton("📊 Health", callback_data="admin_health")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(buttons)


# ─── Subscription / Billing ──────────────────────────────────────────────


async def _get_payment_methods() -> list:
    """Fetch active payment methods from DB, fallback to env vars."""
    try:
        from db.database import async_session
        from sqlalchemy import select
        async with async_session() as s:
            rows = (await s.execute(select(PaymentMethod).where(PaymentMethod.is_active == True).order_by(PaymentMethod.id))).scalars().all()
            if rows:
                return rows
    except Exception:
        pass
    # Fallback to env vars
    from config import CBE_ACCOUNT_NAME, CBE_ACCOUNT_NUMBER, TELEBIRR_ACCOUNT_NAME, TELEBIRR_ACCOUNT_NUMBER
    return [
        type("PM", (), {"name": "cbe", "bank_name": "CBE", "account_name": CBE_ACCOUNT_NAME, "account_number": str(CBE_ACCOUNT_NUMBER), "is_active": True})(),
        type("PM", (), {"name": "telebirr", "bank_name": "Telebirr", "account_name": TELEBIRR_ACCOUNT_NAME, "account_number": str(TELEBIRR_ACCOUNT_NUMBER), "is_active": True})(),
    ]


def _get_subscription_status(business) -> dict:
    now = _utcnow()
    status = business.subscription_status or "trial"
    trial_end = business.trial_end
    sub_end = business.subscription_end

    # Defensive: make naive DB datetimes timezone-aware (migration may have left them as TIMESTAMP)
    if trial_end is not None and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=datetime.UTC)
    if sub_end is not None and sub_end.tzinfo is None:
        sub_end = sub_end.replace(tzinfo=datetime.UTC)

    if status == "trial":
        if trial_end and now >= trial_end:
            return {"active": False, "label": "Trial Expired", "days_left": 0}
        days_left = (trial_end - now).days if trial_end else TRIAL_DAYS
        return {"active": True, "label": f"Trial ({days_left} days left)", "days_left": max(0, days_left)}

    if status == "active":
        if sub_end and now >= sub_end:
            return {"active": False, "label": "Expired", "days_left": 0}
        days_left = (sub_end - now).days if sub_end else 30
        plan = business.subscription_plan or "monthly"
        return {"active": True, "label": f"{plan.capitalize()} ({days_left} days left)", "days_left": max(0, days_left)}

    if status == "expired":
        return {"active": False, "label": "Expired", "days_left": 0}

    return {"active": False, "label": "Unknown", "days_left": 0}


async def _notify_admin(context, text: str, reply_markup=None):
    if ADMIN_TELEGRAM_ID:
        try:
            await context.bot.send_message(ADMIN_TELEGRAM_ID, text, parse_mode="Markdown",
                                           reply_markup=reply_markup)
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)


async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await update.message.reply_text("Register first with /register.")
            return
        info = _get_subscription_status(business)
    await update.message.reply_text(
        f"*Subscription Status*\n\n"
        f"Status: {info['label']}\n"
        f"{'✅ Features active' if info['active'] else '❌ Features locked — subscribe to continue.'}\n\n"
        "Use /plans to see pricing.",
        parse_mode="Markdown",
    )


async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await get_plan_prices()
    await update.message.reply_text(
        "*Ardi AI — Subscription Plans*\n\n"
        f"• Monthly: *{prices['monthly']:,} ETB* ({TRIAL_DAYS}-day trial)\n"
        f"• Yearly: *{prices['yearly']:,} ETB* (2 months free)\n\n"
        "Payment: secure online checkout with Chapa (Telebirr, CBE, cards).\n"
        "Your subscription activates automatically once paid.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Monthly — {prices['monthly']:,} ETB", callback_data="sub_monthly")],
            [InlineKeyboardButton(f"Yearly — {prices['yearly']:,} ETB", callback_data="sub_yearly")],
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
        ]),
    )


async def subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    plan = "monthly" if data == "sub_monthly" else "yearly"
    amount = (await get_plan_prices())[plan]

    async with async_session() as session:
        business = await get_business(session, chat_id)
        if not business:
            await query.edit_message_text("Register first.")
            return
        business.subscription_plan = plan
        business.subscription_status = "awaiting_payment"
        await session.commit()
        biz_id = business.id
        biz_name = business.name

    await query.edit_message_text(
        f"✅ *{plan.capitalize()} plan selected!*\n\n"
        f"Amount: *{amount:,} ETB*\n\n"
        "Pay securely with Chapa (Telebirr, CBE, cards).\n"
        "Your subscription activates automatically once paid.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Pay {amount:,} ETB with Chapa", callback_data=f"sub_chapa_{plan}")],
        ]),
    )


async def chapa_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a Chapa checkout for the owner's selected plan."""
    query = update.callback_query
    await query.answer()
    plan = query.data.replace("sub_chapa_", "", 1)
    if plan not in ("monthly", "yearly"):
        await query.edit_message_text("Invalid plan.")
        return
    if not chapa.chapa_configured():
        await query.edit_message_text("Online payments are temporarily unavailable. Please try again later or contact support.")
        return

    chat_id = update.effective_chat.id
    async with async_session() as session:
        business = await _require_owner_business(session, chat_id)
        if not business:
            await query.edit_message_text("Register your business first with /register.")
            return
        business.subscription_plan = plan
        business.subscription_status = "awaiting_payment"
        await session.commit()
        biz_id = business.id
        amount = (await get_plan_prices())[plan]

    from config import MINI_APP_URL
    base = (_miniapp_base() or (MINI_APP_URL or "").strip().rstrip("/")) or "https://t.me"
    co = await chapa.create_checkout(
        business, plan, amount,
        return_url=f"{base}/pay/done",
        callback_url=f"{base}/api/chapa/webhook",
    )
    if not co:
        await query.edit_message_text("Couldn't start the Chapa checkout. Please try again in a moment.")
        return

    from db.models import SubscriptionPayment
    from decimal import Decimal as _D
    async with async_session() as session:
        session.add(SubscriptionPayment(
            business_id=biz_id, plan=plan, amount=_D(amount),
            tx_ref=co["tx_ref"], checkout_url=co["checkout_url"],
        ))
        await session.commit()

    await query.edit_message_text(
        f"💳 *Pay {amount:,} ETB with Chapa*\n\n"
        f"Plan: *{plan.capitalize()}*\n\n"
        "Tap below to pay securely (Telebirr, CBE, cards).\n"
        "Once paid, your subscription activates automatically — no further tap needed.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Open Chapa Checkout", url=co["checkout_url"])],
        ]),
    )


async def chapa_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner tapped 'I've paid' — verify server-side and activate."""
    query = update.callback_query
    await query.answer()
    pay_id = _parse_id_suffix(query.data, "sub_chapacheck_")
    if pay_id is None:
        await query.edit_message_text("Invalid payment link.")
        return

    from db.models import SubscriptionPayment
    async with async_session() as session:
        business = await _require_owner_business(session, update.effective_chat.id)
        pay = await session.get(SubscriptionPayment, pay_id)
        if not business or not pay or pay.business_id != business.id:
            await query.edit_message_text("Payment not found.")
            return
        tx_ref, plan, biz_id = pay.tx_ref, pay.plan, pay.business_id

    verdict = await chapa.verify_payment(tx_ref)
    if not verdict.get("paid"):
        await query.edit_message_text(
            "⏳ Payment not confirmed yet.\n\n"
            "If you just paid, wait a minute and tap Verify again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Verify Again", callback_data=f"sub_chapacheck_{pay_id}")],
            ]),
        )
        return

    async with async_session() as session:
        pay = await session.get(SubscriptionPayment, pay_id)
        if pay.status != "paid":
            pay.status = "paid"
            pay.chapa_ref = (verdict.get("chapa_ref") or "")[:100]
            await session.commit()
    ok = await _activate_subscription(context, biz_id, plan)
    if ok:
        await query.edit_message_text(
            f"🎉 *Subscription Activated!*\n\nPlan: *{plan.capitalize()}*\nPayment received via Chapa.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("Payment confirmed — activation hit a snag. Contact support.")


async def admin_confirm_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _is_admin(update.effective_user.id):
        await query.edit_message_text("Admin only.")
        return
    data = query.data
    parts = data.split("_")
    if len(parts) < 4:
        await query.edit_message_text("Invalid payment link.")
        return
    try:
        biz_id = int(parts[2])
    except (ValueError, TypeError):
        await query.edit_message_text("Invalid payment link.")
        return
    plan = parts[3]
    if plan not in ("monthly", "yearly"):
        await query.edit_message_text("Invalid plan.")
        return

    ok = await _activate_subscription(context, biz_id, plan)
    if ok:
        await query.edit_message_text(
            f"✅ Subscription activated!\n"
            f"Business ID: {biz_id}\n"
            f"Plan: {plan.capitalize()}",
        )


# ─── Payment Screenshot Auto-Verification ─────────────────────────────────


async def _activate_subscription(context, biz_id: int, plan: str, chat_id: int = None):
    """Activate a subscription and notify the owner."""
    now = _utcnow()
    sub_end = now + datetime.timedelta(days=365 if plan == "yearly" else 30)
    async with async_session() as session:
        result = await session.execute(select(Business).where(Business.id == biz_id))
        business = result.scalar_one_or_none()
        if not business:
            return False
        business.subscription_status = "active"
        business.subscription_plan = plan
        business.subscription_end = sub_end
        await session.commit()
        target_chat = chat_id or business.telegram_chat_id
        try:
            await context.bot.send_message(
                target_chat,
                f"🎉 *Subscription Activated!*\n\n"
                f"Your *{plan.capitalize()}* plan is now active.\n"
                f"Expires: {sub_end.strftime('%Y-%m-%d')}\n\n"
                "Thank you for choosing Ardi AI!",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Failed to notify business owner: %s", e)
    return True


async def _auto_settle_subscription(context, business) -> bool:
    """Webhook safety net: if the owner paid via Chapa but no webhook arrived,
    any message they send settles their latest pending payment automatically."""
    try:
        from db.models import SubscriptionPayment
        async with async_session() as session:
            row = (await session.execute(
                select(SubscriptionPayment)
                .where(SubscriptionPayment.business_id == business.id,
                       SubscriptionPayment.status == "pending")
                .order_by(SubscriptionPayment.id.desc()).limit(1)
            )).scalar_one_or_none()
            if not row:
                return False
            pay_id, tx_ref, plan, biz_id = row.id, row.tx_ref, row.plan, row.business_id
        verdict = await chapa.verify_payment(tx_ref)
        if not verdict.get("paid"):
            return False
        async with async_session() as session:
            pay = await session.get(SubscriptionPayment, pay_id)
            if pay and pay.status != "paid":
                pay.status = "paid"
                pay.chapa_ref = (verdict.get("chapa_ref") or "")[:100]
                await session.commit()
        ok = await _activate_subscription(context, biz_id, plan)
        if ok:
            logger.info("Auto-settled subscription: business=%s plan=%s", biz_id, plan)
        return ok
    except Exception as e:
        logger.warning("Auto-settle failed: %s", e)
        return False


def _amount_sufficient(paid: float, required: float) -> bool:
    return paid >= required - 1.0  # allow 1 ETB tolerance for OCR rounding


def _match_order_receipt(biz, amount_needed, got_amount, got_account, got_name) -> tuple:
    """Match a receipt (photo OCR, PDF text, or link) against the invoice.

    Returns (ok, issues). Empty expected account/name never matches.
    """
    try:
        need = float(amount_needed or 0)
    except (ValueError, TypeError):
        need = 0.0
    try:
        got = float(got_amount or 0)
    except (ValueError, TypeError):
        got = 0.0
    expected_account = (biz.order_bank_account or "").replace(" ", "")
    expected_name = (biz.order_account_holder or "").strip().lower()
    got_account = str(got_account or "").replace(" ", "")
    got_name = str(got_name or "").strip().lower()
    amount_ok = _amount_sufficient(got, need)
    account_ok = (bool(expected_account) and expected_account in got_account) or \
                 (bool(expected_name) and expected_name in got_name)
    issues = []
    if not amount_ok:
        issues.append(f"• Expected at least *{need:.2f} ETB*, found *{got:.2f} ETB*")
    if not account_ok:
        issues.append("• Receiver account doesn't match the business's account")
    return (amount_ok and account_ok), issues


async def _confirm_paid_order(update, context, biz, order_data, receipt_amount, receipt_ref, order_id=None):
    """Confirm payment: reuse the pre-created invoice order when present,
    otherwise create it. Clears payment state, notifies customer + owner."""
    order = None
    if order_id:
        async with async_session() as session:
            order = await session.get(Order, order_id)
            if order and order.business_id == biz.id and order.status in ("awaiting_payment", "review"):
                order.status = "pending"
                await session.commit()
            else:
                order = None
    if order is None:
        async with async_session() as session:
            products_result = await session.execute(
                select(Product).where(Product.business_id == biz.id)
            )
            products = products_result.scalars().all()
        order = await _create_order(biz, update.effective_user, order_data, products)
    items_text = ", ".join(
        f"{i.get('product','')} × {i.get('quantity',1)}"
        for i in (order_data.get("items", [order_data]))
    )
    context.user_data.pop("state", None)
    context.user_data.pop("pending_order", None)
    await update.message.reply_text(
        f"✅ *Payment Verified!*\n\n"
        f"Amount: *{float(receipt_amount or 0):.2f} ETB*\n"
        f"Ref: {receipt_ref or 'N/A'}\n\n"
        f"✅ *Order Confirmed!*\n"
        f"• {items_text}\n"
        f"• Name: {order_data.get('customer_name', '')}\n"
        f"• Phone: {order_data.get('customer_phone', '')}\n"
        f"• Address: {order_data.get('customer_address', '')}\n\n"
        f"The business will prepare your order.",
        parse_mode="Markdown",
    )
    await _notify_new_order(context, biz, order, update.effective_user)
    history = context.user_data.get("customer_chat_history", [])
    history.append({"role": "assistant", "text": f"[Order #{order.id} confirmed with payment: {items_text}]"})
    context.user_data["customer_chat_history"] = history[-20:]
    return order


async def ord_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Customer tapped 'I've Paid' — ask for the receipt (photo, PDF, or bank receipt link)."""
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get("pending_order")
    if not pending or context.user_data.get("state") != "awaiting_order_payment":
        await query.edit_message_text(
            "I don't have a pending invoice for you. Send me what you'd like to order first!"
        )
        return
    await query.edit_message_text(
        f"Great! Send your receipt for *{pending.get('total', 0):.2f} ETB* "
        f"(invoice `{pending.get('ref', '')}`) — a clear *photo*, a *PDF*, or a *link* to it.",
        parse_mode="Markdown",
    )


async def handle_receipt_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify a PDF receipt (text-extracted) against the pending invoice."""
    from receipts import MAX_RECEIPT_BYTES
    doc = update.message.document
    pending = context.user_data.get("pending_order")
    if not pending or context.user_data.get("state") != "awaiting_order_payment":
        await update.message.reply_text(
            "I don't have a pending invoice for you. Tell me what you'd like to order first!"
        )
        return
    if doc.file_size and doc.file_size > MAX_RECEIPT_BYTES:
        await update.message.reply_text("That PDF is too large (max 6 MB). Send a photo instead.")
        return

    await update.message.reply_text("📄 Reading your receipt...")
    try:
        file = await doc.get_file()
        raw = await file.download_as_bytearray()
        pdf_bytes = bytes(raw)
        if len(pdf_bytes) > MAX_RECEIPT_BYTES:
            await update.message.reply_text("That PDF is too large (max 6 MB). Send a photo instead.")
            return
    except Exception as e:
        logger.warning("PDF download failed: %s", e)
        await update.message.reply_text("Couldn't download that PDF. Try a photo instead.")
        return

    async with async_session() as session:
        biz = await session.execute(select(Business).where(Business.id == pending["business_id"]))
        biz = biz.scalar_one_or_none()
    if not biz:
        await update.message.reply_text("The business is no longer available.")
        context.user_data.pop("state", None)
        context.user_data.pop("pending_order", None)
        return

    verdict = verify_pdf_receipt(pdf_bytes, biz.order_bank_account, biz.order_account_holder,
                                 pending["total"])
    if verdict["ok"]:
        await _confirm_paid_order(update, context, biz, pending["data"],
                                  verdict["amount"], "PDF receipt",
                                  pending.get("order_id"))
        return

    # Inconclusive — create a review order so the owner can confirm manually.
    async with async_session() as session:
        products = (await session.execute(
            select(Product).where(Product.business_id == biz.id))).scalars().all()
    order = await _create_order(biz, update.effective_user, pending["data"], products)
    async with async_session() as session:
        o = await session.get(Order, order.id)
        o.status = "review"
        await session.commit()
    context.user_data.pop("state", None)
    context.user_data.pop("pending_order", None)
    await update.message.reply_text(
        f"🔍 I couldn't fully verify that receipt ({verdict.get('reason', 'unclear')}).\n\n"
        f"Your order *#{order.id}* is saved and sent to the business for review — "
        "they'll confirm it shortly.",
        parse_mode="Markdown",
    )
    history = context.user_data.get("customer_chat_history", [])
    history.append({"role": "assistant", "text": f"[Order #{order.id} awaiting owner review: {verdict.get('reason', '')}]"})
    context.user_data["customer_chat_history"] = history[-20:]
    try:
        await context.bot.send_message(
            biz.telegram_chat_id,
            f"🔍 *Receipt needs review — Order #{order.id}*\n\n"
            f"Customer: {pending['data'].get('customer_name', '')}\n"
            f"Phone: {pending['data'].get('customer_phone', '')}\n"
            f"Total: *{float(pending['total']):.2f} ETB*\n"
            f"Check: {verdict.get('reason', 'unclear')}\n\n"
            "Confirm it from /orders once verified.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Review notify failed: %s", e)


async def handle_bank_receipt(update, context, bank: str, key: str):
    """Verify via the bank itself (authoritative), then match the invoice."""
    pending = context.user_data.get("pending_order")
    if not pending or context.user_data.get("state") != "awaiting_order_payment":
        return
    await update.message.reply_text("🏦 Checking with the bank...")

    async with async_session() as session:
        biz = await session.execute(select(Business).where(Business.id == pending["business_id"]))
        biz = biz.scalar_one_or_none()
    if not biz:
        await update.message.reply_text("The business is no longer available.")
        context.user_data.pop("state", None)
        context.user_data.pop("pending_order", None)
        return

    verdict = await extract_bank_receipt(bank, key, biz.order_bank_account or "")
    if not verdict["ok"] and not verdict.get("amount"):
        await update.message.reply_text(
            f"⚠️ {verdict.get('reason', 'Bank lookup failed.')}\n\n"
            "Send a clear photo of the receipt instead.",
        )
        return

    ok, issues = _match_order_receipt(biz, pending["total"], verdict["amount"],
                                      verdict["account"], verdict["name"])
    if ok:
        await _confirm_paid_order(update, context, biz, pending["data"],
                                  verdict["amount"], verdict.get("ref") or "bank",
                                  pending.get("order_id"))
    else:
        _acct = f" to `{verdict['account']}`" if verdict.get("account") else ""
        await update.message.reply_text(
            "⚠️ *Bank Receipt Doesn't Match*\n\n"
            f"Bank shows *{float(verdict['amount'] or 0):.2f} ETB*{_acct}.\n"
            + "\n".join(issues) +
            "\n\nIf this is the wrong receipt, send the correct one.",
            parse_mode="Markdown",
        )


async def handle_receipt_link(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str):
    """Verify a receipt URL — bank share links go to the bank, anything else
    is fetched SSRF-safely as image/PDF."""
    pending = context.user_data.get("pending_order")
    if not pending or context.user_data.get("state") != "awaiting_order_payment":
        return
    bank_hit = detect_bank_link(link)
    if bank_hit:
        return await handle_bank_receipt(update, context, *bank_hit)
    await update.message.reply_text("🔗 Opening your receipt link...")
    try:
        kind, data = await fetch_receipt_url(link)
    except ReceiptError as e:
        await update.message.reply_text(str(e))
        return

    async with async_session() as session:
        biz = await session.execute(select(Business).where(Business.id == pending["business_id"]))
        biz = biz.scalar_one_or_none()
    if not biz:
        await update.message.reply_text("The business is no longer available.")
        context.user_data.pop("state", None)
        context.user_data.pop("pending_order", None)
        return

    if kind == "image":
        receipt = await verify_receipt(data)
        if receipt.get("status") in ("UNREADABLE", "ERROR"):
            await update.message.reply_text("I couldn't read that receipt. Try a clearer photo or PDF.")
            return
        ok, issues = _match_order_receipt(biz, pending["total"], receipt.get("amount", 0),
                                          str(receipt.get("receiver_account", "")),
                                          receipt.get("receiver_name", ""))
        if ok:
            await _confirm_paid_order(update, context, biz, pending["data"],
                                      receipt.get("amount", 0), receipt.get("reference", "link"),
                                      pending.get("order_id"))
        else:
            await update.message.reply_text(
                "⚠️ *Receipt Doesn't Match*\n\n" + "\n".join(issues) +
                "\n\nPlease check and send the correct receipt.",
                parse_mode="Markdown",
            )
        return

    verdict = verify_pdf_receipt(data, biz.order_bank_account, biz.order_account_holder,
                                 pending["total"])
    if verdict["ok"]:
        await _confirm_paid_order(update, context, biz, pending["data"],
                                  verdict["amount"], "receipt link",
                                  pending.get("order_id"))
    else:
        await update.message.reply_text(
            f"🔍 That receipt needs owner review ({verdict.get('reason', 'unclear')}). "
            "Send it as a clear photo, or wait — I've notified the business.",
            parse_mode="Markdown",
        )


async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Business-chat media arrives as update.business_message (message is None).
    if not update.message:
        logger.info("Non-message update in receipt router — ignored")
        return
    msg = update.message if update else None
    is_pdf = bool(msg and msg.document and (
        (msg.document.mime_type or "") == "application/pdf"
        or (msg.document.file_name or "").lower().endswith(".pdf")))
    if not _has_photo(update) and not is_pdf:
        await update.message.reply_text("Please send your receipt as a photo or PDF.")
        return
    if _is_media_group_duplicate(update):
        return
    chat_id = update.effective_chat.id
    state = context.user_data.get("state", "")

    async with async_session() as session:
        business = await get_business(session, chat_id)

    is_subscription = business and business.subscription_status == "awaiting_payment"
    is_order = state == "awaiting_order_payment" and context.user_data.get("pending_order")

    # Route non-payment photos during customer chat
    if not is_subscription and not is_order:
        if context.user_data.get("customer_chat_active"):
            return await handle_customer_photo(update, context)
        return

    if is_subscription:
        # Subscriptions are Chapa-only. Receipt screenshots are for order payments.
        await update.message.reply_text(
            "Subscriptions are paid online with Chapa.\n\n"
            "Use /plans to get a secure checkout link — "
            "your subscription activates automatically once paid."
        )
        return

    if is_pdf:
        return await handle_receipt_pdf(update, context)

    await update.message.reply_text("📄 Reading your receipt...")

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    receipt = await verify_receipt(bytes(image_bytes))

    if receipt.get("status") in ("UNREADABLE", "ERROR"):
        await update.message.reply_text(
            "I couldn't read that receipt clearly. Please send a clearer screenshot.",
        )
        return

    amount = receipt.get("amount", 0)
    receiver_account = str(receipt.get("receiver_account", "")).strip().replace(" ", "")
    receiver_name = (receipt.get("receiver_name", "") or "").strip().lower()

    if is_order:
        pending = context.user_data["pending_order"]
        biz_id = pending["business_id"]
        order_data = pending["data"]
        amount_needed = pending["total"]

        async with async_session() as session:
            result = await session.execute(select(Business).where(Business.id == biz_id))
            biz = result.scalar_one_or_none()

        if not biz:
            await update.message.reply_text("The business is no longer available.")
            context.user_data.pop("state", None)
            context.user_data.pop("pending_order", None)
            return

        ok, issues = _match_order_receipt(biz, amount_needed, amount,
                                          receiver_account, receiver_name)
        if ok:
            await _confirm_paid_order(update, context, biz, order_data,
                                      amount, receipt.get("reference", "N/A"),
                                      context.user_data.get("pending_order", {}).get("order_id"))
        else:
            await update.message.reply_text(
                f"⚠️ *Receipt Doesn't Match*\n\n" + "\n".join(issues) +
                f"\n\nPlease check and send the correct receipt (photo, PDF, or bank receipt link).",
                parse_mode="Markdown",
            )
