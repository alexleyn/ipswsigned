"""
IPSW Signing Monitor Bot
Monitors https://ipsw.me and https://ipsw.dev for iOS/macOS/watchOS firmware
signing status changes and notifies subscribers via Telegram.
"""
import logging
import os
from html import escape

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
)

import database as db
import ipsw_api as api
from checker import check_all

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", 1800))  # 30 min default
ITEMS_PER_PAGE = 8

# Conversation states
CHOOSING_TYPE, CHOOSING_GENERATION, CHOOSING_MODEL = range(3)


# ─────────────────────────── helpers ────────────────────────────

def _type_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for prefix, name, emoji in api.DEVICE_TYPES:
        buttons.append(InlineKeyboardButton(f"{emoji} {name}", callback_data=f"type:{prefix}"))
    # 2 columns
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _generation_keyboard(groups: list, page: int) -> InlineKeyboardMarkup:
    """Keyboard for selecting a generation/lineup. groups = [(label, [devices]), ...]"""
    total = len(groups)
    start = page * ITEMS_PER_PAGE
    end   = min(start + ITEMS_PER_PAGE, total)

    rows = []
    for idx in range(start, end):
        label, devices = groups[idx]
        suffix = f" ({len(devices)})" if len(devices) > 1 else ""
        rows.append([InlineKeyboardButton(
            label + suffix,
            callback_data=f"gen:{idx}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"gpage:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"gpage:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 Main menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def _model_list_keyboard(devices: list) -> InlineKeyboardMarkup:
    """Keyboard listing specific models within a chosen generation."""
    rows = []
    for d in devices:
        rows.append([InlineKeyboardButton(
            d["name"],
            callback_data=f"model:{d['identifier']}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Back to lineups", callback_data="back_gen")])
    rows.append([InlineKeyboardButton("🏠 Main menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def _firmware_text(device_name: str, identifier: str, release_fw: list, dev_fw: list) -> str:
    lines = [
        f"<b>📱 {escape(device_name)}</b>  <code>{escape(identifier)}</code>\n",
    ]

    if release_fw:
        lines.append("🟢 <b>Release — currently signed:</b>")
        for fw in release_fw:
            lines.append(f"  • {escape(fw['version'])} ({escape(fw['buildid'])})")
    else:
        lines.append("🔴 No signed release firmware")

    if dev_fw:
        lines.append("\n🔵 <b>Beta / Dev — currently signed:</b>")
        for fw in dev_fw:
            lines.append(f"  • {escape(fw['version'])} ({escape(fw['buildid'])})")
    elif dev_fw is not None:
        lines.append("\n⚪ No signed beta/dev firmware")

    return "\n".join(lines)


def _device_keyboard(identifier: str, user_id: int) -> InlineKeyboardMarkup:
    subscribed = db.is_subscribed(user_id, identifier)
    rows = []
    if subscribed:
        rows.append([InlineKeyboardButton(
            "🔕 Unsubscribe", callback_data=f"unfollow:{identifier}"
        )])
    else:
        rows.append([InlineKeyboardButton(
            "🔔 Notify me on changes", callback_data=f"follow:{identifier}"
        )])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{identifier}")])
    rows.append([InlineKeyboardButton("🏠 Main menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────── /start ─────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>IPSW Signing Monitor</b>\n\n"
        "I track Apple firmware signing status and notify you of any changes.\n\n"
        "Select your device type:",
        reply_markup=_type_keyboard(),
        parse_mode="HTML",
    )
    return CHOOSING_TYPE


# ─────────────────────────── /list ──────────────────────────────

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = db.get_user_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text("You have no active subscriptions.")
        return

    lines = ["<b>Your subscriptions:</b>\n"]
    for s in subs:
        lines.append(f"• {escape(s['device_name'])} <code>({escape(s['device_identifier'])})</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─────────────────────────── /check ─────────────────────────────

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Select your device type:",
        reply_markup=_type_keyboard(),
    )
    return CHOOSING_TYPE


# ─────────────────── conversation: type → generation → model ────

async def cb_select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    prefix = query.data.split(":", 1)[1]
    devices = await api.get_devices_by_prefix(prefix)

    if not devices:
        await query.edit_message_text("No devices found. Please try another type.")
        return CHOOSING_TYPE

    groups = api.group_devices_by_generation(devices, prefix)
    context.user_data["groups"] = groups
    context.user_data["prefix"] = prefix

    # If only one group — skip straight to models
    if len(groups) == 1:
        return await _show_models_for_group(query, context, 0)

    kbd = _generation_keyboard(groups, 0)
    await query.edit_message_text(
        f"Select a lineup ({len(groups)} available):",
        reply_markup=kbd,
    )
    return CHOOSING_GENERATION


async def cb_select_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split(":", 1)[1])
    return await _show_models_for_group(query, context, idx)


async def _show_models_for_group(query, context, idx: int):
    groups = context.user_data.get("groups", [])
    if idx >= len(groups):
        await query.edit_message_text("Something went wrong. Please try /start.")
        return ConversationHandler.END

    label, devices = groups[idx]
    context.user_data["gen_idx"] = idx

    # If only one device in the group, go straight to firmware display
    if len(devices) == 1:
        await _show_device(query, devices[0]["identifier"], query.from_user.id)
        return ConversationHandler.END

    kbd = _model_list_keyboard(devices)
    await query.edit_message_text(
        f"Select a model ({label}):",
        reply_markup=kbd,
    )
    return CHOOSING_MODEL


async def cb_paginate_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    page = int(query.data.split(":", 1)[1])
    groups = context.user_data.get("groups", [])
    kbd = _generation_keyboard(groups, page)
    await query.edit_message_reply_markup(reply_markup=kbd)
    return CHOOSING_GENERATION


async def cb_back_to_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from model list to generation list."""
    query = update.callback_query
    await query.answer()

    groups = context.user_data.get("groups", [])
    if not groups:
        await query.edit_message_text("Select your device type:", reply_markup=_type_keyboard())
        return CHOOSING_TYPE

    kbd = _generation_keyboard(groups, 0)
    await query.edit_message_text("Select a lineup:", reply_markup=kbd)
    return CHOOSING_GENERATION


async def cb_select_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    identifier = query.data.split(":", 1)[1]
    await _show_device(query, identifier, query.from_user.id)
    return ConversationHandler.END


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Select your device type:",
        reply_markup=_type_keyboard(),
    )
    return CHOOSING_TYPE


# ─────────────────── show device firmwares ──────────────────────

async def _show_device(query, identifier: str, user_id: int):
    await query.edit_message_text("⏳ Loading firmware data…")

    release_fw, dev_fw, device_name = await api.get_all_signed_firmwares(identifier)

    text = _firmware_text(device_name, identifier, release_fw, dev_fw)
    kbd  = _device_keyboard(identifier, user_id)

    await query.edit_message_text(text, reply_markup=kbd, parse_mode="HTML")


# ─────────────────── follow / unfollow / refresh ────────────────

async def cb_follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    identifier = query.data.split(":", 1)[1]
    user_id  = query.from_user.id
    chat_id  = query.message.chat_id
    device_name = await api.resolve_device_name(identifier)

    db.add_subscription(user_id, chat_id, identifier, device_name)

    kbd = _device_keyboard(identifier, user_id)
    await query.edit_message_reply_markup(reply_markup=kbd)
    await query.answer(f"✅ Subscribed to {device_name}", show_alert=True)


async def cb_unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    identifier = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    db.remove_subscription(user_id, identifier)

    kbd = _device_keyboard(identifier, user_id)
    await query.edit_message_reply_markup(reply_markup=kbd)
    await query.answer("🔕 Unsubscribed", show_alert=True)


async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Refreshing…")

    identifier = query.data.split(":", 1)[1]
    await _show_device(query, identifier, query.from_user.id)


# ─────────────────────────── main ───────────────────────────────

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set. Copy .env.example → .env and fill it in.")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("check", cmd_check),
        ],
        states={
            CHOOSING_TYPE: [
                CallbackQueryHandler(cb_select_type,  pattern=r"^type:"),
                CallbackQueryHandler(cb_main_menu,    pattern=r"^main$"),
            ],
            CHOOSING_GENERATION: [
                CallbackQueryHandler(cb_select_generation, pattern=r"^gen:"),
                CallbackQueryHandler(cb_paginate_gen,      pattern=r"^gpage:"),
                CallbackQueryHandler(cb_main_menu,         pattern=r"^main$"),
            ],
            CHOOSING_MODEL: [
                CallbackQueryHandler(cb_select_model, pattern=r"^model:"),
                CallbackQueryHandler(cb_back_to_gen,  pattern=r"^back_gen$"),
                CallbackQueryHandler(cb_main_menu,    pattern=r"^main$"),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("list", cmd_list))

    # Inline button handlers (outside conversation)
    app.add_handler(CallbackQueryHandler(cb_follow,   pattern=r"^follow:"))
    app.add_handler(CallbackQueryHandler(cb_unfollow, pattern=r"^unfollow:"))
    app.add_handler(CallbackQueryHandler(cb_refresh,  pattern=r"^refresh:"))

    # Background job
    app.job_queue.run_repeating(
        check_all,
        interval=CHECK_INTERVAL,
        first=60,  # first check 1 min after start
        name="ipsw_checker",
    )

    logger.info("Bot started. Check interval: %ds", CHECK_INTERVAL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
