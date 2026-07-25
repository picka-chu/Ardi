import logging
import socket
import os
from logging.handlers import TimedRotatingFileHandler

# Force IPv4 only for Telegram API (IPv6 times out). Let all other services resolve normally.
_original_getaddrinfo = socket.getaddrinfo
_TELEGRAM_HOSTS = ("api.telegram.org", "telegram.org")
def _smart_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in _TELEGRAM_HOSTS:
        try:
            return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        except socket.gaierror:
            pass
    return _original_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _smart_getaddrinfo

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    BusinessConnectionHandler,
    DictPersistence,
    ContextTypes,
    filters,
)

from config import TELEGRAM_TOKEN, SENTRY_DSN
from db.database import init_db
import miniapp
from bot.handlers import (
    start,
    menu_callback,
    help_command,
    cancel,
    cmd_health,
    cmd_backup,
    cmd_privacy,
    cmd_terms,
    cmd_admin,
    admin_callback,
    # Registration
    cmd_register,
    register_conversation,
    REGISTER_CONVERSATION,
    # Add Product
    cmd_addproduct,
    add_product_photo,
    add_product_text_handler,
    product_confirm_callback,
    ADD_PRODUCT_PHOTO,
    ADD_PRODUCT_CONFIRM,
    # Catalog
    cmd_catalog,
    # Channel
    cmd_connectchannel,
    scan_channel,
    handle_channel_post,
    handle_forwarded_channel,
    # Customer AI Agent
    cmd_businesses,
    end_customer_chat,
    cmd_ai_settings,
    toggle_ai_callback,
    cmd_tone,
    tone_callback,
    cmd_share,
    # Orders
    cmd_orders,
    orders_page_callback,
    order_view_callback,
    order_status_callback,
    # Reply Keyboard
    keyboard_handler,
    # Business Integration
    cmd_sync_connection,
    handle_business_connection,
    handle_business_message,
    # Business Hours
    cmd_business_hours,
    hours_set_start,
    hours_set_done,
    hours_offline_start,
    hours_offline_done,
    hours_toggle_callback,
    BUSINESS_HOURS_SET,
    BUSINESS_HOURS_MSG,
    # Escalation
    escalation_callback,
    handle_escalation_reply,
    # Subscription
    cmd_trial,
    cmd_plans,
    subscription_callback,
    payment_notify_callback,
    admin_confirm_payment_callback,
    handle_payment_screenshot,
    # Order Settings
    cmd_order_settings,
    orders_toggle_callback,
    orders_set_bank_start,
    orders_set_bank_name,
    orders_set_bank_account,
    orders_set_bank_holder,
    ORDER_BANK_NAME,
    ORDER_BANK_ACCOUNT,
    ORDER_BANK_HOLDER,
    cmd_language,
    language_callback,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Rotating file handler — daily logs, keep 30 days
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(log_dir, "ardi.log"),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)


async def post_init(app):
    await init_db()

    if SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)
            logger.info("Sentry error monitoring enabled")
        except Exception as e:
            logger.warning("Failed to init Sentry: %s", e)

    bot_info = await app.bot.get_me()
    logger.info("Ardi AI started as @%s (id=%s)", bot_info.username, bot_info.id)

    # Periodic heartbeat for health check
    async def _heartbeat(_context):
        import time
        miniapp.bot_last_heartbeat = time.monotonic()

    app.job_queue.run_repeating(_heartbeat, interval=30, first=10)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error: %s", context.error, exc_info=True)


def main():
    persistence = DictPersistence()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .persistence(persistence)
        .build()
    )
    app.add_error_handler(error_handler)

    # AI-powered registration conversation
    reg_conv = ConversationHandler(
        entry_points=[
            CommandHandler("register", cmd_register),
            CallbackQueryHandler(cmd_register, pattern="^register$"),
            MessageHandler(filters.Text(["🚀 Register My Business"]), cmd_register),
        ],
        states={
            REGISTER_CONVERSATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_conversation),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        per_message=False,
    )

    # Add product conversation
    product_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addproduct", cmd_addproduct),
            CallbackQueryHandler(cmd_addproduct, pattern="^addproduct$"),
            MessageHandler(filters.Text(["➕ Add Product"]), cmd_addproduct),
        ],
        states={
            ADD_PRODUCT_PHOTO: [
                MessageHandler(filters.PHOTO, add_product_photo),
            ],
            ADD_PRODUCT_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_text_handler),
                CallbackQueryHandler(product_confirm_callback,
                                     pattern="^(product_save|product_rename|product_reprice|product_cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("terms", cmd_terms))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("businesses", cmd_businesses))
    app.add_handler(CommandHandler("endchat", end_customer_chat))
    app.add_handler(CommandHandler("ai", cmd_ai_settings))
    app.add_handler(CommandHandler("tone", cmd_tone))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(reg_conv)
    app.add_handler(product_conv)
    app.add_handler(CommandHandler("catalog", cmd_catalog))
    app.add_handler(CommandHandler("connectchannel", cmd_connectchannel))
    app.add_handler(CommandHandler("scanchannel", scan_channel))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("sync", cmd_sync_connection))
    app.add_handler(CommandHandler("hours", cmd_business_hours))
    app.add_handler(CommandHandler("trial", cmd_trial))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("order_settings", cmd_order_settings))

    # Business hours conversation
    hours_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(hours_set_start, pattern="^hours_set$")],
        states={
            BUSINESS_HOURS_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, hours_set_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(menu_callback, pattern="^main_menu$")],
        per_message=False,
    )
    app.add_handler(hours_conv)

    hours_msg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(hours_offline_start, pattern="^hours_offline_msg$")],
        states={
            BUSINESS_HOURS_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, hours_offline_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(menu_callback, pattern="^main_menu$")],
        per_message=False,
    )
    app.add_handler(hours_msg_conv)

    # Order settings conversation
    orders_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(orders_set_bank_start, pattern="^orders_set_bank$")],
        states={
            ORDER_BANK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, orders_set_bank_name)],
            ORDER_BANK_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, orders_set_bank_account)],
            ORDER_BANK_HOLDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, orders_set_bank_holder)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(menu_callback, pattern="^main_menu$")],
        per_message=False,
    )
    app.add_handler(orders_conv)

    # Telegram Business
    app.add_handler(BusinessConnectionHandler(handle_business_connection))
    app.add_handler(MessageHandler(
        filters.UpdateType.BUSINESS_MESSAGE & filters.TEXT,
        handle_business_message,
    ))

    # Customer messages + Reply keyboard (single router)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & ~filters.UpdateType.BUSINESS_MESSAGE,
        keyboard_handler,
    ))

    # Payment receipt screenshots (photos from users awaiting payment)
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE,
        handle_payment_screenshot,
    ))

    # Callbacks
    app.add_handler(CallbackQueryHandler(toggle_ai_callback, pattern="^(activate_ai|deactivate_ai)$"))
    app.add_handler(CallbackQueryHandler(cmd_tone, pattern="^tone_menu$"))
    app.add_handler(CallbackQueryHandler(tone_callback, pattern="^set_tone_"))
    app.add_handler(CallbackQueryHandler(cmd_orders, pattern="^orders_list$"))
    app.add_handler(CallbackQueryHandler(orders_page_callback, pattern="^orders_page_"))
    app.add_handler(CallbackQueryHandler(order_view_callback, pattern="^order_view_"))
    app.add_handler(CallbackQueryHandler(order_status_callback, pattern="^order_(confirm|complete|cancel)_"))
    app.add_handler(CallbackQueryHandler(hours_toggle_callback, pattern="^hours_toggle$"))
    app.add_handler(CallbackQueryHandler(escalation_callback, pattern="^escalation_"))
    app.add_handler(CallbackQueryHandler(subscription_callback, pattern="^sub_(monthly|yearly)$"))
    app.add_handler(CallbackQueryHandler(payment_notify_callback, pattern="^sub_paid_"))
    app.add_handler(CallbackQueryHandler(admin_confirm_payment_callback, pattern="^sub_confirm_"))
    app.add_handler(CallbackQueryHandler(orders_toggle_callback, pattern="^orders_toggle$"))
    app.add_handler(CallbackQueryHandler(cmd_order_settings, pattern="^order_settings$"))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))



    # Channel
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST & filters.PHOTO, handle_channel_post))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded_channel))

    logger.info("Ardi AI Agent started!")
    app.run_polling(allowed_updates=[
        "message", "callback_query", "channel_post",
        "my_chat_member", "business_connection", "business_message",
    ], stop_signals=[])


if __name__ == "__main__":
    main()