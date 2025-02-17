#!/usr/bin/env python3
# client_bot.py
import logging
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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
(SUBSCRIPTION_PHONE, SUBSCRIPTION_CLIENT, MAIN_MENU, AWAITING_RESPONSE) = range(4)
def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Helper function that safely edits a message.
    If the original message is a photo message (has a caption),
    it uses edit_message_caption() instead of edit_message_text().
    """
    if query.message.caption:
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
    db.add_subscription(user.id, phone, 'Client', "Client", None,
                    user.username, user.first_name, user.last_name, update.effective_chat.id)
    update.message.reply_text("تم استقبال رقم الهاتف. الآن، يرجى إدخال اسم العميل الذي تمثله (مثال: بيبس):")
    return SUBSCRIPTION_CLIENT

def subscription_client(update: Update, context: CallbackContext) -> int:
    client_name = update.message.text.strip()
    user = update.effective_user
    sub = db.get_subscription(user.id, 'Client')
    phone = sub['phone'] if sub and sub['phone'] != "unknown" else "unknown"
    db.add_subscription(user.id, phone, 'Client', "Client", client_name,
                    user.username, user.first_name, user.last_name, update.effective_chat.id)
    keyboard = [[InlineKeyboardButton("عرض المشاكل", callback_data="menu_show_tickets")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ Client!", reply_markup=reply_markup)
    return MAIN_MENU

def client_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "menu_show_tickets":
        user = query.from_user
        tickets = db.get_tickets_by_client(user.id)

        if tickets:
            for ticket in tickets:
                text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                        f"🔹 <b>رقم الطلب:</b> {ticket['order_id']}\n"
                        f"🔹 <b>الوصف:</b> {ticket['issue_description']}\n"
                        f"🔹 <b>سبب المشكلة:</b> {ticket['issue_reason']}\n"
                        f"🔹 <b>نوع المشكلة:</b> {ticket['issue_type']}\n"
                        f"🔹 <b>الحالة:</b> {ticket['status']}")

                keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket['ticket_id']}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if ticket['image_url']:
                    query.bot.send_photo(chat_id=query.message.chat_id,
                                         photo=ticket['image_url'],
                                         caption=text,
                                         reply_markup=reply_markup,
                                         parse_mode="HTML")
                else:
                    query.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            query.message.reply_text("لا توجد تذاكر متاحة.")
        
        return MAIN_MENU
def send_issue_details_to_client(query, ticket_id):
    ticket = db.get_ticket(ticket_id)
    logs = ""
    if ticket.get("logs"):
        try:
            logs_list = json.loads(ticket["logs"])
            logs = "\n".join([f"{entry.get('timestamp', '')}: {entry.get('action', '')} – {entry.get('message', '')}" for entry in logs_list])
        except Exception:
            logs = "لا توجد سجلات إضافية."
        
    text = (f"<b>تفاصيل التذكرة:</b>\n"
            f"🔹 <b>رقم الطلب:</b> {ticket['order_id']}\n"
            f"🔹 <b>الوصف:</b> {ticket['issue_description']}\n"
            f"🔹 <b>سبب المشكلة:</b> {ticket['issue_reason']}\n"
            f"🔹 <b>نوع المشكلة:</b> {ticket['issue_type']}\n"
            f"🔹 <b>الحالة:</b> {ticket['status']}"
            f"📝 <b>السجلات:</b>\n{logs}")

    keyboard = [
            [InlineKeyboardButton("عرض التفاصيل", callback_data=f"notify_pref|{ticket_id}|now")],
            [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket_id}")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if ticket['image_url']:
    # If the original message is a photo message, edit its caption.
        if query.message.photo:
            query.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            query.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        query.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
def send_full_issue_details_to_client(query, ticket_id):
    ticket = db.get_ticket(ticket_id)
    logs = ""
    if ticket.get("logs"):
        try:
            logs_list = json.loads(ticket["logs"])
            logs = "\n".join([f"{entry.get('timestamp', '')}: {entry.get('action', '')} – {entry.get('message', '')}" for entry in logs_list])
        except Exception:
            logs = "لا توجد سجلات إضافية."
    text = (f"<b>تفاصيل التذكرة الكاملة:</b>\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}"
            f"📝 <b>السجلات:</b>\n{logs}")
    keyboard = [
        [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket_id}")],
        [InlineKeyboardButton("تجاهل", callback_data=f"ignore|{ticket_id}")]
    ]
    if ticket['image_url']:
        query.bot.send_photo(chat_id=query.message.chat_id, photo=ticket['image_url'])
    safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")
    safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")

def reminder_callback(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    ticket_id = job.context['ticket_id']
    text = f"تذكير: لم تقم بالرد على التذكرة #{ticket_id} بعد."
    context.bot.send_message(chat_id=chat_id, text=text)

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
    context.user_data['awaiting_response'] = False
    context.user_data.pop('ticket_id', None)
    return MAIN_MENU

def notify_supervisors_client_response(ticket_id, solution=None, ignored=False):
    ticket = db.get_ticket(ticket_id)
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)

    text = (f"<b>تفاصيل التذكرة #{ticket_id}</b>\n"
            f"🔹 <b>رقم الطلب:</b> {ticket['order_id']}\n"
            f"🔹 <b>الوصف:</b> {ticket['issue_description']}\n"
            f"🔹 <b>سبب المشكلة:</b> {ticket['issue_reason']}\n"
            f"🔹 <b>نوع المشكلة:</b> {ticket['issue_type']}\n"
            f"🔹 <b>الحالة:</b> {ticket['status']}")

    keyboard = [[InlineKeyboardButton("إرسال الحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    for sup in db.get_supervisors():
        try:
            if ticket['image_url']:
                bot.send_photo(chat_id=sup['chat_id'],
                               photo=ticket['image_url'],
                               caption=text,
                               reply_markup=reply_markup,
                               parse_mode="HTML")
            else:
                bot.send_message(chat_id=sup['chat_id'],
                                 text=text,
                                 reply_markup=reply_markup,
                                 parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ Error notifying supervisor {sup['chat_id']}: {e}")
def default_handler_client(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("عرض المشاكل", callback_data="menu_show_tickets")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU

def main():
    updater = Updater(config.CLIENT_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SUBSCRIPTION_PHONE: [MessageHandler(Filters.text & ~Filters.command, subscription_phone)],
            SUBSCRIPTION_CLIENT: [MessageHandler(Filters.text & ~Filters.command, subscription_client)],
            MAIN_MENU: [CallbackQueryHandler(client_main_menu_callback, pattern="^(menu_show_tickets)$")],
            AWAITING_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, client_awaiting_response_handler)]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("تم إلغاء العملية."))]
    )
    
    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(Filters.text, default_handler_client))
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()

