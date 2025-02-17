#!/usr/bin/env python3
# supervisor_bot.py
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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
(SUBSCRIPTION_PHONE, MAIN_MENU, SEARCH_TICKETS, AWAITING_RESPONSE, WAITING_FOR_ACTION) = range(5)

def search_order_callback(update: Update, context: CallbackContext):
    """Handles searching for tickets by order ID."""
    query = update.callback_query
    query.answer()
    order_id = query.data.split("|")[1]

    tickets = db.search_tickets_by_order(order_id)  # Fetch tickets from the database

    if not tickets:
        query.edit_message_text(text="⚠️ لا توجد تذاكر لهذا الطلب.")
        return WAITING_FOR_ACTION

    for ticket in tickets:
        ticket_id = ticket['ticket_id']
        description = ticket['issue_description']
        status = ticket['status']
        image_url = ticket.get('image_url')

        text = (
            f"<b>تذكرة #{ticket_id}</b>\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {description}\n"
            f"الحالة: {status}"
        )

        keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view_ticket|{ticket_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if image_url:
            context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=image_url,
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

    return WAITING_FOR_ACTION
# supervisor_bot.py

def view_ticket_callback(update: Update, context: CallbackContext):
    """Handles viewing a specific ticket's details."""
    query = update.callback_query
    query.answer()
    ticket_id = int(query.data.split("|")[1])

    ticket = db.get_ticket(ticket_id)  # Fetch the ticket details from the database

    if not ticket:
        query.edit_message_text(text="⚠️ لم يتم العثور على هذه التذكرة.")
        return WAITING_FOR_ACTION

    description = ticket['issue_description']
    status = ticket['status']
    image_url = ticket.get('image_url')

    text = (
        f"<b>تفاصيل التذكرة #{ticket_id}</b>\n"
        f"رقم الطلب: {ticket['order_id']}\n"
        f"الوصف: {description}\n"
        f"الحالة: {status}"
    )

    keyboard = [
        [InlineKeyboardButton("إغلاق التذكرة", callback_data=f"close_ticket|{ticket_id}")],
        [InlineKeyboardButton("الرجوع", callback_data="back_to_tickets")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if ticket.get("logs"):
        try:
            logs_list = json.loads(ticket["logs"])
            logs_text = "\n".join(
                [f"{entry.get('timestamp', '')}: {entry.get('action', '')} - {entry.get('message', '')}"
                 for entry in logs_list]
            )
            text += f"\n\n📝 <b>السجلات:</b>\n{logs_text}"
        except Exception as e:
            logger.error(f"Error parsing logs for ticket #{ticket['ticket_id']}: {e}")
    # Then send using edit or send_photo as appropriate…
    if ticket.get('image_url'):
        query.message.reply_photo(
            photo=ticket['image_url'],
            caption=text,
            reply_markup=query.message.reply_markup,  # if you want to keep existing buttons, or omit if none
            parse_mode="HTML"
        )
    else:
        query.message.reply_text(text=text, parse_mode="HTML")

    return WAITING_FOR_ACTION
def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Helper function that edits a message.
    If the original message is a photo message (i.e. has a caption),
    it uses edit_message_caption() instead of edit_message_text().
    """
    if query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    sub = db.get_subscription(user.id, "Supervisor")
    if not sub:
        update.message.reply_text("أهلاً! يرجى إدخال رقم هاتفك للاشتراك (Supervisor):")  # Fixed pdate to update
        return SUBSCRIPTION_PHONE
    else:
        keyboard = [[InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
                InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"مرحباً {user.first_name}", reply_markup=reply_markup)
        return MAIN_MENU

def subscription_phone(update: Update, context: CallbackContext) -> int:
    phone = update.message.text.strip()
    user = update.effective_user
    db.add_subscription(user.id, phone, 'Supervisor', "Supervisor", None,
                    user.username, user.first_name, user.last_name, update.effective_chat.id)
    keyboard = [[InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
            InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ Supervisor!", reply_markup=reply_markup)
    return MAIN_MENU

def supervisor_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "menu_show_all":
        tickets = db.get_all_open_tickets()
        if tickets:
            for ticket in tickets:
                text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                        f"رقم الطلب: {ticket['order_id']}\n"
                        f"العميل: {ticket['client']}\n"
                        f"الوصف: {ticket['issue_description']}\n"
                        f"الحالة: {ticket['status']}")
                keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket['ticket_id']}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)  # ✅ Ensure reply_markup is defined

                # If the ticket has an image, send it as a photo message
                if ticket['image_url']:
                    query.message.reply_photo(photo=ticket['image_url'], caption=text, parse_mode="HTML", reply_markup=reply_markup)
                else:
                    safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")  # ✅ Now it will always be defined
        else:
            safe_edit_message(query, text="لا توجد تذاكر مفتوحة حالياً.", reply_markup=None)  # ✅ Explicitly passing None
        return MAIN_MENU
    elif data == "menu_query_issue":
        safe_edit_message(query, text="أدخل رقم الطلب:")
        return SEARCH_TICKETS
    elif data.startswith("view|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)  # ✅ This should now work correctly

        if ticket:
            logs = ""
            if ticket["logs"]:
                try:
                    logs_list = json.loads(ticket["logs"])
                    logs = "\n".join([f"{entry.get('timestamp', '')}: {entry.get('action', '')} - {entry.get('message', '')}"
                                    for entry in logs_list])
                except Exception:
                    logs = "لا توجد سجلات إضافية."

            text = (f"<b>تفاصيل التذكرة #{ticket['ticket_id']}</b>\n"
                    f"رقم الطلب: {ticket['order_id']}\n"
                    f"العميل: {ticket['client']}\n"
                    f"الوصف: {ticket['issue_description']}\n"
                    f"سبب المشكلة: {ticket['issue_reason']}\n"
                    f"نوع المشكلة: {ticket['issue_type']}\n"
                    f"الحالة: {ticket['status']}\n\n"
                    f"📝 <b>السجلات:</b>\n{logs}")
                    
            

            keyboard = [
                [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket_id}")],
                [InlineKeyboardButton("طلب معلومات إضافية", callback_data=f"moreinfo|{ticket_id}")],
                [InlineKeyboardButton("إرسال إلى العميل", callback_data=f"sendclient|{ticket_id}")]
            ]

            if ticket["status"] == "Client Responded":
                keyboard.insert(0, [InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(query, text=text, reply_markup=reply_markup, parse_mode="HTML")

        else:
            safe_edit_message(query, text="التذكرة غير موجودة.")

        return MAIN_MENU
    elif data.startswith("solve|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'solve'
        context.user_data['awaiting_response'] = True
        context.bot.send_message(chat_id=query.message.chat_id,
                                 text="أدخل رسالة الحل للمشكلة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE
    elif data.startswith("moreinfo|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'moreinfo'
        context.user_data['awaiting_response'] = True
        context.bot.send_message(chat_id=query.message.chat_id,
                                 text="أدخل المعلومات الإضافية المطلوبة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE
    elif data.startswith("sendclient|"):
        ticket_id = int(data.split("|")[1])
        keyboard = [[InlineKeyboardButton("نعم", callback_data=f"confirm_sendclient|{ticket_id}"),
                     InlineKeyboardButton("لا", callback_data=f"cancel_sendclient|{ticket_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="هل أنت متأكد من إرسال التذكرة إلى العميل؟", reply_markup=reply_markup)
        return MAIN_MENU
    elif data.startswith("confirm_sendclient|"):
        ticket_id = int(data.split("|")[1])
        send_to_client(ticket_id)
        safe_edit_message(query, text="تم إرسال التذكرة إلى العميل.")
        return MAIN_MENU
    elif data.startswith("cancel_sendclient|"):
        ticket_id = int(data.split("|")[1])
        safe_edit_message(query, text="تم إلغاء الإرسال إلى العميل.")
        return MAIN_MENU
    elif data.startswith("sendto_da|"):
        ticket_id = int(data.split("|")[1])
        keyboard = [[InlineKeyboardButton("نعم", callback_data=f"confirm_sendto_da|{ticket_id}"),
                     InlineKeyboardButton("لا", callback_data=f"cancel_sendto_da|{ticket_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="هل أنت متأكد من إرسال الحل إلى الوكيل؟", reply_markup=reply_markup)
        return MAIN_MENU
    elif data.startswith("confirm_sendto_da|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        client_solution = None
        if ticket["logs"]:
            try:
                logs = json.loads(ticket["logs"])
                for log in logs:
                    if log.get("action") == "client_solution":
                        client_solution = log.get("message")
                        break
            except Exception:
                client_solution = None
        if not client_solution:
            client_solution = "لا يوجد حل من العميل."
        db.update_ticket_status(ticket_id, "Pending DA Action", {"action": "supervisor_forward", "message": client_solution})
        notify_da(ticket_id, client_solution, info_request=False)
        safe_edit_message(query, text="تم إرسال التذكرة إلى الوكيل.")
        return MAIN_MENU
    elif data.startswith("cancel_sendto_da|"):
        ticket_id = int(data.split("|")[1])
        safe_edit_message(query, text="تم إلغاء إرسال التذكرة إلى الوكيل.")
        return MAIN_MENU
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def search_tickets(update: Update, context: CallbackContext) -> int:
    query_text = update.message.text.strip()
    
    # ✅ Ensure function exists in db.py
    tickets = db.search_tickets_by_order(query_text)

    if tickets:
        for ticket in tickets:
            text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                    f"رقم الطلب: {ticket['order_id']}\n"
                    f"العميل: {ticket['client']}\n"
                    f"الوصف: {ticket['issue_description']}\n"
                    f"الحالة: {ticket['status']}")
            keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket['ticket_id']}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        update.message.reply_text("لم يتم العثور على تذاكر مطابقة.")
    
    return MAIN_MENU

def awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    response = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    action = context.user_data.get('action')
    if not ticket_id or not action:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    if action == 'solve':
        db.update_ticket_status(ticket_id, "Pending DA Action", {"action": "supervisor_solution", "message": response})
        notify_da(ticket_id, response, info_request=False)
        update.message.reply_text("تم إرسال الحل إلى الوكيل.")
    elif action == 'moreinfo':
        db.update_ticket_status(ticket_id, "Pending DA Response", {"action": "request_more_info", "message": response})
        notify_da(ticket_id, response, info_request=True)
        update.message.reply_text("تم إرسال الطلب إلى الوكيل.")
    context.user_data.pop('ticket_id', None)
    context.user_data.pop('action', None)
    return MAIN_MENU

def notify_da(ticket_id, message, info_request=False):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        logger.error(f"⚠️ notify_da: Ticket {ticket_id} not found")
        return

    da_id = ticket.get('da_id')  # Ensure this retrieves the correct DA ID
    if not da_id:
        logger.error(f"⚠️ notify_da: No DA assigned to ticket #{ticket_id}")
        return

    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)

    if info_request:
        text = (f"🔹 <b>طلب معلومات إضافية</b> للتذكرة #{ticket_id}\n"
                f"📦 رقم الطلب: {ticket['order_id']}\n"
                f"📌 الوصف: {ticket['issue_description']}\n"
                f"📢 الحالة: {ticket['status']}\n\n"
                f"❓ المعلومات المطلوبة: {message}")
        keyboard = [[InlineKeyboardButton("إرسال المعلومات", callback_data=f"da_moreinfo|{ticket_id}")]]
    else:
        text = (f"✅ <b>حل مشكلة التذكرة #{ticket_id}</b>\n"
                f"📦 رقم الطلب: {ticket['order_id']}\n"
                f"📌 الوصف: {ticket['issue_description']}\n"
                f"📢 الحالة: {ticket['status']}\n\n"
                f"📝 الحل: {message}")
        keyboard = [[InlineKeyboardButton("إغلاق التذكرة", callback_data=f"close|{ticket_id}")]]

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        da_sub = db.get_subscription(da_id, "DA")  # Ensure DA exists
        if da_sub:
            chat_id = da_sub.get('chat_id')
            if not chat_id:
                logger.error(f"⚠️ notify_da: No chat_id found for DA {da_id}")
                return
            
            bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
            logger.info(f"✅ notify_da: Sent request to DA (Chat ID: {chat_id})")
        else:
            logger.error(f"⚠️ notify_da: No subscription found for DA {da_id}")

    except Exception as e:
        logger.error(f"❌ notify_da: Error notifying DA: {e}")
def send_to_client(ticket_id):
    ticket = db.get_ticket(ticket_id)
    client_name = ticket['client']
    clients = db.get_clients_by_name(client_name)
    bot = Bot(token=config.CLIENT_BOT_TOKEN)
    message = (f"<b>تذكرة من المشرف</b>\n"
            f"تذكرة #{ticket['ticket_id']}\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}")
    keyboard = [
        [InlineKeyboardButton("حالياً", callback_data=f"notify_pref|{ticket['ticket_id']}|now")],
        [InlineKeyboardButton("خلال 15 دقيقة", callback_data=f"notify_pref|{ticket['ticket_id']}|15")],
        [InlineKeyboardButton("خلال 10 دقائق", callback_data=f"notify_pref|{ticket['ticket_id']}|10")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for client in clients:
        try:
            if ticket['image_url']:
                bot.send_photo(chat_id=client['chat_id'], photo=ticket['image_url'],
                                caption=message, reply_markup=reply_markup, parse_mode="HTML")
            else:
                bot.send_message(chat_id=client['chat_id'], text=message,
                                reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying client {client['chat_id']}: {e}")

def default_handler_supervisor(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
            InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU
def close_ticket_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    ticket_id = int(query.data.split("|")[1])
    db.update_ticket_status(ticket_id, "Closed", {"action": "closed"})
    query.message.edit_text(text=f"تم إغلاق التذكرة #{ticket_id}.")
    return MAIN_MENU
def error_handler(update: Update, context: CallbackContext):
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        update.message.reply_text("⚠️ حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى.")
def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END
def main():
    updater = Updater(config.SUPERVISOR_BOT_TOKEN, use_context=True)
    
    dp = updater.dispatcher
    dp.add_error_handler(error_handler)

# Define the conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBSCRIPTION_PHONE: [
                MessageHandler(Filters.text & ~Filters.command, subscription_phone)
            ],
            WAITING_FOR_ACTION: [
                CallbackQueryHandler(search_order_callback, pattern="^search_order|"),
                CallbackQueryHandler(view_ticket_callback, pattern="^view_ticket|"),
                CallbackQueryHandler(close_ticket_callback, pattern="^close_ticket|"),
                CallbackQueryHandler(back_to_tickets_callback, pattern="^back_to_tickets")
            ],
            MAIN_MENU: [
            CallbackQueryHandler(supervisor_main_menu_callback, 
                pattern="^(menu_show_all|menu_query_issue|view\\|.*|solve\\|.*|moreinfo\\|.*|sendclient\\|.*|sendto_da\\|.*)$")
            ],
            SEARCH_TICKETS: [
                MessageHandler(Filters.text & ~Filters.command, search_tickets)
            ],
            AWAITING_RESPONSE: [
                MessageHandler(Filters.text & ~Filters.command, awaiting_response_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
        )
    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(Filters.text, default_handler_supervisor))
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
