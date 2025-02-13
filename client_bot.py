#!/usr/bin/env python3
# client_bot.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext
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

def start(update: Update, context: CallbackContext):
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

def subscription_phone(update: Update, context: CallbackContext):
    phone = update.message.text.strip()
    user = update.effective_user
    db.add_subscription(user.id, phone, 'Client', "Client", None,
                        user.username, user.first_name, user.last_name, update.effective_chat.id)
    update.message.reply_text("تم استقبال رقم الهاتف. الآن، يرجى إدخال اسم العميل الذي تمثله (مثال: بيبس):")
    return SUBSCRIPTION_CLIENT

def subscription_client(update: Update, context: CallbackContext):
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

def client_main_menu_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "menu_show_tickets":
        sub = db.get_subscription(query.from_user.id, "Client")
        client_name = sub['client']
        tickets = [t for t in db.get_all_open_tickets() if t['status'] == "Awaiting Client Response" and t['client'] == client_name]
        if tickets:
            for ticket in tickets:
                text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                        f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
                        f"<b>الوصف:</b> {ticket['issue_description']}\n"
                        f"<b>الحالة:</b> {ticket['status']}")
                keyboard = [
                    [InlineKeyboardButton("حالياً", callback_data=f"notify_pref|{ticket['ticket_id']}|now")],
                    [InlineKeyboardButton("خلال 15 دقيقة", callback_data=f"notify_pref|{ticket['ticket_id']}|15")],
                    [InlineKeyboardButton("خلال 10 دقائق", callback_data=f"notify_pref|{ticket['ticket_id']}|10")],
                    [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket['ticket_id']}")],
                    [InlineKeyboardButton("تجاهل", callback_data=f"ignore|{ticket['ticket_id']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if ticket['image_url']:
                    query.message.reply_photo(photo=ticket['image_url'])
                safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            safe_edit_message(query, text="لا توجد تذاكر في انتظار ردك.")
        return MAIN_MENU
    elif data.startswith("notify_pref|"):
        parts = data.split("|")
        ticket_id = int(parts[1])
        pref = parts[2]
        if pref == "now":
            send_full_issue_details_to_client(query, ticket_id)
        else:
            delay = 900 if pref == "15" else 600
            context.job_queue.run_once(reminder_callback, delay,
                                       context={'chat_id': query.message.chat_id, 'ticket_id': ticket_id})
            send_issue_details_to_client(query, ticket_id)
        return MAIN_MENU
    elif data.startswith("solve|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if ticket['status'] in ("Client Responded", "Client Ignored", "Closed"):
            safe_edit_message(query, text="التذكرة مغلقة أو تمت معالجتها بالفعل ولا يمكن تعديلها.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['awaiting_response'] = True
        context.bot.send_message(chat_id=query.message.chat_id,
                                 text="أدخل الحل للمشكلة:",
                                 reply_markup=ForceReply(selective=True))
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

def send_issue_details_to_client(query, ticket_id):
    ticket = db.get_ticket(ticket_id)
    text = (f"<b>تفاصيل التذكرة:</b>\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}")
    keyboard = [
        [InlineKeyboardButton("حالياً", callback_data=f"notify_pref|{ticket_id}|now")],
        [InlineKeyboardButton("خلال 15 دقيقة", callback_data=f"notify_pref|{ticket_id}|15")],
        [InlineKeyboardButton("خلال 10 دقائق", callback_data=f"notify_pref|{ticket_id}|10")],
        [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket_id}")],
        [InlineKeyboardButton("تجاهل", callback_data=f"ignore|{ticket_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if ticket['image_url']:
        query.bot.send_photo(chat_id=query.message.chat_id, photo=ticket['image_url'])
    safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")

def send_full_issue_details_to_client(query, ticket_id):
    ticket = db.get_ticket(ticket_id)
    text = (f"<b>تفاصيل التذكرة الكاملة:</b>\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}")
    keyboard = [
        [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket_id}")],
        [InlineKeyboardButton("تجاهل", callback_data=f"ignore|{ticket_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if ticket['image_url']:
        query.bot.send_photo(chat_id=query.message.chat_id, photo=ticket['image_url'])
    safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")

def reminder_callback(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    ticket_id = job.context['ticket_id']
    text = f"تذكير: لم تقم بالرد على التذكرة #{ticket_id} بعد."
    context.bot.send_message(chat_id=chat_id, text=text)

def client_awaiting_response_handler(update: Update, context: CallbackContext):
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
    if ignored:
        text = (f"<b>تنبيه:</b> تم تجاهل التذكرة #{ticket_id} من قبل العميل.\n"
                f"رقم الطلب: {ticket['order_id']}\n"
                f"الوصف: {ticket['issue_description']}\n"
                f"الحالة: {ticket['status']}")
        keyboard = [[InlineKeyboardButton("حل المشكلة", callback_data=f"sup_resolve|{ticket_id}")]]
    else:
        text = (f"<b>حل من العميل للتذكرة #{ticket_id}</b>\n"
                f"رقم الطلب: {ticket['order_id']}\n"
                f"الوصف: {ticket['issue_description']}\n"
                f"الحل: {solution}\n"
                f"الحالة: {ticket['status']}")
        keyboard = [[InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for sup in db.get_supervisors():
        try:
            if ticket['image_url']:
                bot.send_photo(chat_id=sup['chat_id'], photo=ticket['image_url'],
                               caption=text, reply_markup=reply_markup, parse_mode="HTML")
            else:
                bot.send_message(chat_id=sup['chat_id'], text=text,
                                 reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying supervisor {sup['chat_id']}: {e}")

def default_handler_client(update: Update, context: CallbackContext):
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
            MAIN_MENU: [CallbackQueryHandler(client_main_menu_callback, pattern="^(menu_show_tickets|notify_pref\\|.*|solve\\|.*|ignore\\|.*)")],
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
