#!/usr/bin/env python3
# client_bot.py

import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Bot
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    ConversationHandler,
    CallbackContext
)
import db
import config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(SUBSCRIPTION_PHONE, SUBSCRIPTION_CLIENT, MAIN_MENU, AWAITING_RESPONSE) = range(4)

def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Safely edits a message. If the original message is a photo (has a caption),
    it uses edit_message_caption() instead of edit_message_text().
    """
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    sub = db.get_subscription(user.id, "Client")
    if not sub:
        update.message.reply_text("أهلاً! يرجى إدخال رقم هاتفك للاشتراك (Client):")
        return SUBSCRIPTION_PHONE
    elif not sub['client']:
        update.message.reply_text("يرجى إدخال اسم العميل الذي تمثله (مثال: بيبس):")
        return SUBSCRIPTION_CLIENT
    else:
        keyboard = [[InlineKeyboardButton("عرض المشاكل", callback_data="menu_show_tickets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"مرحباً {user.first_name}", reply_markup=reply_markup)
        return MAIN_MENU

def subscription_phone(update: Update, context: CallbackContext) -> int:
    phone = update.message.text.strip()
    user = update.effective_user
    db.add_subscription(
        user.id, phone, 'Client', "Client", None,
        user.username, user.first_name, user.last_name, update.effective_chat.id
    )
    update.message.reply_text(
        "تم استقبال رقم الهاتف. الآن، يرجى إدخال اسم العميل الذي تمثله (مثال: بيبس):"
    )
    return SUBSCRIPTION_CLIENT

def subscription_client(update: Update, context: CallbackContext) -> int:
    client_name = update.message.text.strip()
    user = update.effective_user
    sub = db.get_subscription(user.id, 'Client')
    phone = sub['phone'] if sub and sub['phone'] != "unknown" else "unknown"
    db.add_subscription(
        user.id, phone, 'Client', "Client", client_name,
        user.username, user.first_name, user.last_name, update.effective_chat.id
    )
    keyboard = [[InlineKeyboardButton("عرض المشاكل", callback_data="menu_show_tickets")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ Client!", reply_markup=reply_markup)
    return MAIN_MENU

def client_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "menu_show_tickets":
        sub = db.get_subscription(query.from_user.id, "Client")
        client_name = sub['client']
        # Filter tickets for this client with status "Awaiting Client Response"
        tickets = [t for t in db.get_all_open_tickets() if t['status'] == "Awaiting Client Response" and t['client'] == client_name]

        if tickets:
            for ticket in tickets:
                # Build ticket details text with a separator line
                text = (
                    f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                    f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
                    f"<b>الوصف:</b> {ticket['issue_description']}\n"
                    f"<b>الحالة:</b> {ticket['status']}\n"
                    "-----------------------------"
                )
                # Build inline keyboard with a button to trigger the solve flow
                keyboard = [
                    [InlineKeyboardButton("ارسال حل المشكلة", callback_data=f"solve|{ticket['ticket_id']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if ticket.get('image_url'):
                    # Send a single message with the image and caption containing all details
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=ticket['image_url'],
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                else:
                    context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
        else:
            context.bot.send_message(chat_id=update.effective_chat.id, text="لا توجد تذاكر في انتظار ردك.")
        return MAIN_MENU

    elif data.startswith("solve|"):
        parts = data.split("|")
        ticket_id = int(parts[1])
        ticket = db.get_ticket(ticket_id)
        if ticket['status'] in ("Client Responded", "Client Ignored", "Closed"):
            safe_edit_message(query, text="التذكرة مغلقة أو تمت معالجتها بالفعل ولا يمكن تعديلها.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'solve'
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text="من فضلك أدخل الحل للمشكلة:"
        )
        return AWAITING_RESPONSE

    elif data.startswith("ignore|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if ticket['status'] in ("Client Responded", "Client Ignored", "Closed"):
            safe_edit_message(query, text="التذكرة مغلقة أو تمت معالجتها بالفعل ولا يمكن تعديلها.")
            return MAIN_MENU
        db.update_ticket_status(ticket_id, "Client Ignored", {"action": "client_ignored"})
        db.update_ticket_status(ticket_id, "Client Responded", {"action": "client_final_response", "message": "ignored"})
        notify_supervisors_client_response(ticket_id, ignored=True)
        safe_edit_message(query, text="تم إرسال ردك (تم تجاهل التذكرة).")
        return MAIN_MENU

    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def client_awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    solution = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    ticket = db.get_ticket(ticket_id)
    if ticket['status'] in ("Client Responded", "Client Ignored", "Closed"):
        update.message.reply_text("التذكرة مغلقة أو تمت معالجتها بالفعل ولا يمكن تعديلها.")
        return MAIN_MENU
    db.update_ticket_status(ticket_id, "Client Responded", {"action": "client_solution", "message": solution})
    notify_supervisors_client_response(ticket_id, solution=solution)
    update.message.reply_text("تم إرسال الحل إلى المشرف.")
    context.user_data.pop('ticket_id', None)
    return MAIN_MENU

def notify_supervisors_client_response(ticket_id, solution=None, ignored=False):
    ticket = db.get_ticket(ticket_id)
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)
    if ignored:
        text = (
            f"<b>تنبيه:</b> تم تجاهل التذكرة #{ticket_id} من قبل العميل.\n"
            f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
            f"<b>الوصف:</b> {ticket['issue_description']}\n"
            f"<b>الحالة:</b> {ticket['status']}"
        )
        keyboard = [[InlineKeyboardButton("إرسال الحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")]]
    else:
        text = (
            f"<b>حل من العميل للتذكرة #{ticket_id}</b>\n"
            f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
            f"<b>الوصف:</b> {ticket['issue_description']}\n"
            f"<b>الحل:</b> {solution}\n"
            f"<b>الحالة:</b> {ticket['status']}"
        )
        keyboard = [[InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for sup in db.get_supervisors():
        try:
            if ticket.get('image_url'):
                bot.send_photo(
                    chat_id=sup['chat_id'],
                    photo=ticket['image_url'],
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                bot.send_message(
                    chat_id=sup['chat_id'],
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error notifying supervisor {sup['chat_id']}: {e}")

def default_handler_client(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("عرض المشاكل", callback_data="menu_show_tickets")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU

# --- GLOBAL HANDLERS ---

def global_solve_callback(update: Update, context: CallbackContext) -> None:
    """
    Global callback to handle any 'solve|' callbacks if the conversation handler
    is not active.
    """
    query = update.callback_query
    query.answer()
    data = query.data.split("|")
    ticket_id = int(data[1])
    context.user_data['ticket_id'] = ticket_id
    context.user_data['action'] = 'solve'
    context.bot.send_message(
        chat_id=query.message.chat.id,
        text="من فضلك أدخل الحل للمشكلة:"
    )

def global_text_handler(update: Update, context: CallbackContext) -> None:
    """
    Global text handler that checks if there is a pending solution.
    """
    if context.user_data.get('ticket_id') and context.user_data.get('action') == 'solve':
        client_awaiting_response_handler(update, context)
    else:
        default_handler_client(update, context)

def main():
    updater = Updater(config.CLIENT_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SUBSCRIPTION_PHONE: [
                MessageHandler(Filters.text & ~Filters.command, subscription_phone)
            ],
            SUBSCRIPTION_CLIENT: [
                MessageHandler(Filters.text & ~Filters.command, subscription_client)
            ],
            MAIN_MENU: [
                CallbackQueryHandler(client_main_menu_callback, pattern="^(menu_show_tickets|solve\\|.*|ignore\\|.*)")
            ],
            AWAITING_RESPONSE: [
                MessageHandler(Filters.text & ~Filters.command, client_awaiting_response_handler)
            ]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("تم إلغاء العملية."))],
        allow_reentry=True
    )
    
    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(global_solve_callback, pattern="^solve\\|"))
    dp.add_handler(MessageHandler(Filters.text, global_text_handler))
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()