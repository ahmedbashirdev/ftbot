#!/usr/bin/env python3
# supervisor_bot.py
import logging
import json
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ForceReply,
    Bot
)
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
from notifier import notify_da_moreinfo, notify_da
# Initialize logging and bot instances
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
da_bot = Bot(token=config.DA_BOT_TOKEN)

# Conversation states
(SUBSCRIPTION_PHONE, MAIN_MENU, SEARCH_TICKETS, AWAITING_RESPONSE, EDIT_DETAILS) = range(5)

def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Helper function that edits a message.
    If the message is a photo (has a caption),
    uses edit_message_caption(), otherwise edit_message_text().
    """
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    sub = db.get_subscription(user.id, "Supervisor")
    if not sub:
        update.message.reply_text("أهلاً! يرجى إدخال رقم هاتفك للاشتراك (Supervisor):")
        return SUBSCRIPTION_PHONE
    else:
        keyboard = [
            [InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
             InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"مرحباً {user.first_name}", reply_markup=reply_markup)
        return MAIN_MENU

def subscription_phone(update: Update, context: CallbackContext) -> int:
    phone = update.message.text.strip()
    user = update.effective_user
    db.add_subscription(user.id, phone, 'Supervisor', "Supervisor", None,
                        user.username, user.first_name, user.last_name, update.effective_chat.id)
    keyboard = [
        [InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ Supervisor!", reply_markup=reply_markup)
    return MAIN_MENU

def supervisor_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("supervisor_main_menu_callback: Received data: %s", data)
    
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
                reply_markup = InlineKeyboardMarkup(keyboard)
                if ticket.get('image_url'):
                    query.message.reply_photo(photo=ticket['image_url'], caption=text, reply_markup=reply_markup, parse_mode="HTML")
                else:
                    safe_edit_message(query, text=text, reply_markup=reply_markup)
        else:
            safe_edit_message(query, text="لا توجد تذاكر مفتوحة حالياً.", reply_markup=None)
        return MAIN_MENU

    elif data == "menu_query_issue":
        safe_edit_message(query, text="أدخل رقم الطلب:")
        return SEARCH_TICKETS

    elif data.startswith("view|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if ticket:
            logs = ""
            if ticket.get("logs"):
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
            if ticket['status'] == "Client Responded":
                keyboard.insert(0, [InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket_id}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(query, text=text, reply_markup=reply_markup)
        else:
            safe_edit_message(query, text="التذكرة غير موجودة.")
        return MAIN_MENU

    elif data.startswith("solve|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'solve'
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="أدخل رسالة الحل للمشكلة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE

    elif data.startswith("moreinfo|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'moreinfo'
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="أدخل المعلومات الإضافية المطلوبة للتذكرة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE

    elif data.startswith("sendclient|"):
        try:
            ticket_id = int(data.split("|")[1])
        except ValueError:
            safe_edit_message(query, "رقم التذكرة غير صحيح.")
            return MAIN_MENU
        keyboard = [
            [InlineKeyboardButton("إرسال كما هي", callback_data=f"confirm_sendclient|{ticket_id}")],
            [InlineKeyboardButton("تعديل التفاصيل", callback_data=f"edit_sendclient|{ticket_id}")],
            [InlineKeyboardButton("إلغاء", callback_data=f"cancel_sendclient|{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="هل تريد إرسال التذكرة إلى العميل كما هي أم تعديل التفاصيل؟", reply_markup=reply_markup)
        return MAIN_MENU

    elif data.startswith("confirm_sendclient|"):
        try:
            ticket_id = int(data.split("|")[1])
        except ValueError:
            safe_edit_message(query, "رقم التذكرة غير صحيح.")
            return MAIN_MENU
        ticket = db.get_ticket(ticket_id)
        send_to_client(ticket)
        safe_edit_message(query, text=f"تم إرسال التذكرة #{ticket_id} إلى العميل.")
        return MAIN_MENU

    elif data.startswith("edit_sendclient|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'edit_details'
        ticket = db.get_ticket(ticket_id)
        current_text = (
            f"تذكرة #{ticket['ticket_id']}\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}"
        )
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"من فضلك أدخل التفاصيل المعدلة (المحتوى الحالي:\n{current_text})"
        )
        return EDIT_DETAILS

    elif data.startswith("cancel_sendclient|"):
        safe_edit_message(query, text="تم إلغاء الإرسال إلى العميل.")
        return MAIN_MENU

    # -- Send to DA branch --
    elif data.startswith("sendto_da|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        client_solution = None
        if ticket.get("logs"):
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
        notify_da(ticket, client_solution, info_request=False)
        safe_edit_message(query, text="تم إرسال الحالة إلى الوكيل.")
        return MAIN_MENU

    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def send_to_client(ticket, message_text=None):
    client_name = ticket.get('client')
    clients = db.get_clients_by_name(client_name)
    bot = Bot(token=config.CLIENT_BOT_TOKEN)
    description = message_text if message_text is not None else ticket['issue_description']
    message = (f"<b>تذكرة من المشرف</b>\n"
               f"تذكرة #{ticket['ticket_id']}\n"
               f"رقم الطلب: {ticket['order_id']}\n"
               f"الوصف: {description}\n"
               f"الحالة: {ticket['status']}")
    keyboard = [
        [InlineKeyboardButton("ارسال حل المشكلة", callback_data=f"solve|{ticket['ticket_id']}|now")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if not clients:
        logger.warning(f"send_to_client: No client subscriptions found for client name '{client_name}'")
    for client in clients:
        try:
            if ticket.get('image_url'):
                bot.send_photo(chat_id=client['chat_id'], photo=ticket['image_url'],
                               caption=message, reply_markup=reply_markup, parse_mode="HTML")
            else:
                bot.send_message(chat_id=client['chat_id'], text=message,
                               reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying client {client['chat_id']}: {e}")

def search_tickets(update: Update, context: CallbackContext) -> int:
    query_text = update.message.text.strip()
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
    keyboard = [
        [InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("اختر خياراً:", reply_markup=reply_markup)
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
        notify_da(db.get_ticket(ticket_id))
        update.message.reply_text("تم إرسال الحل إلى الوكيل.")
    elif action == 'moreinfo':
        db.update_ticket_status(ticket_id, "Pending DA Response", {"action": "supervisor_moreinfo", "message": response})
        from notifier import notify_da_moreinfo
        notify_da_moreinfo(ticket_id, response)
        update.message.reply_text("تم إرسال المعلومات الإضافية إلى الوكيل.")
    context.user_data.pop('ticket_id', None)
    context.user_data.pop('action', None)
    return MAIN_MENU

def global_supervisor_text_handler(update: Update, context: CallbackContext) -> None:
    if context.user_data.get('action') in ['solve', 'moreinfo'] and context.user_data.get('ticket_id'):
        awaiting_response_handler(update, context)
    else:
        update.message.reply_text("الرجاء اختيار خيار من القائمة.")

def supervisor_edit_details_handler(update: Update, context: CallbackContext) -> int:
    edited_text = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    if not ticket_id:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    if not db.update_ticket_details(ticket_id, edited_text):
        update.message.reply_text("حدث خطأ أثناء تحديث تفاصيل التذكرة.")
        return MAIN_MENU
    ticket = db.get_ticket(ticket_id)
    send_to_client(ticket, message_text=edited_text)
    update.message.reply_text("تم إرسال التذكرة إلى العميل مع التفاصيل المعدلة.")
    context.user_data.pop('ticket_id', None)
    context.user_data.pop('action', None)
    return MAIN_MENU

def default_handler_supervisor(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU

def error_handler(update: Update, context: CallbackContext):
    logger.error('Update "%s" caused error "%s"', update, context.error)
    if update and update.message:
        update.message.reply_text("⚠️ حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى.")

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

def global_supervisor_action_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("global_supervisor_action_handler: Received data: %s", data)
    
    # Process solve action
    if data.startswith("solve|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'solve'
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="أدخل رسالة الحل للمشكلة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE
    
    # Process more info action
    elif data.startswith("moreinfo|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'moreinfo'
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="أدخل المعلومات الإضافية المطلوبة للتذكرة:",
                                 reply_markup=ForceReply(selective=True))
        return AWAITING_RESPONSE
    
    # Process send to client action
    elif data.startswith("sendclient|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        ticket = db.get_ticket(ticket_id)
        if ticket:
            send_to_client(ticket)
            safe_edit_message(query, text=f"تم إرسال التذكرة #{ticket_id} إلى العميل.")
        else:
            safe_edit_message(query, text="التذكرة غير موجودة.")
        return MAIN_MENU
    
    # Process send to DA action
    elif data.startswith("sendto_da|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        ticket = db.get_ticket(ticket_id)
        if ticket:
            client_solution = None
            if ticket.get("logs"):
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
            notify_da(ticket, client_solution, info_request=False)
            safe_edit_message(query, text="تم إرسال الحالة إلى الوكيل.")
        else:
            safe_edit_message(query, text="التذكرة غير موجودة.")
        return MAIN_MENU
    
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def global_supervisor_text_handler(update: Update, context: CallbackContext) -> None:
    if context.user_data.get('action') == 'solve' and context.user_data.get('ticket_id'):
        solution = update.message.text.strip()
        ticket_id = context.user_data['ticket_id']
        success = db.update_ticket_status(ticket_id, "Pending DA Action", {"action": "supervisor_solution", "message": solution})
        if success:
            ticket = db.get_ticket(ticket_id)
            notify_da(ticket)
            update.message.reply_text("تم إرسال الحل إلى الوكيل.")
        else:
            update.message.reply_text("حدث خطأ أثناء تحديث التذكرة.")
        context.user_data.pop('ticket_id', None)
        context.user_data.pop('action', None)
        return
    elif context.user_data.get('action') == 'moreinfo' and context.user_data.get('ticket_id'):
        info = update.message.text.strip()
        ticket_id = context.user_data['ticket_id']
        success = db.update_ticket_status(ticket_id, "Awaiting DA Response", {"action": "supervisor_moreinfo", "message": info})
        if success:
            notify_da_moreinfo(ticket_id, info)
            update.message.reply_text("تم إرسال المعلومات الإضافية إلى الوكيل.")
        else:
            update.message.reply_text("حدث خطأ أثناء تحديث التذكرة.")
        context.user_data.pop('ticket_id', None)
        context.user_data.pop('action', None)
        return
    else:
        update.message.reply_text("الرجاء اختيار خيار من القائمة.")

def default_handler_supervisor(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("عرض الكل", callback_data="menu_show_all"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU

def error_handler(update: Update, context: CallbackContext):
    logger.error('Update "%s" caused error "%s"', update, context.error)
    if update and update.message:
        update.message.reply_text("⚠️ حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى.")

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

def main():
    updater = Updater(config.SUPERVISOR_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_error_handler(error_handler)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBSCRIPTION_PHONE: [MessageHandler(Filters.text & ~Filters.command, subscription_phone)],
            MAIN_MENU: [CallbackQueryHandler(supervisor_main_menu_callback, 
                                     pattern="^(menu_show_all|menu_query_issue|view\\|.*|solve\\|.*|moreinfo\\|.*|sendclient\\|.*|sendto_da\\|.*|confirm_sendclient\\|.*|cancel_sendclient\\|.*|edit_sendclient\\|.*|confirm_sendto_da\\|.*|cancel_sendto_da\\|.*)$")],
            SEARCH_TICKETS: [MessageHandler(Filters.text & ~Filters.command, search_tickets)],
            AWAITING_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, awaiting_response_handler)],
            EDIT_DETAILS: [MessageHandler(Filters.text & ~Filters.command, supervisor_edit_details_handler)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("تم إلغاء العملية."))]
    )
    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(global_supervisor_action_handler, 
        pattern="^(solve\\|.*|moreinfo\\|.*|sendclient\\|.*|sendto_da\\|.*)$"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, global_supervisor_text_handler))
    dp.add_handler(MessageHandler(Filters.text, default_handler_supervisor))
    dp.add_handler(CallbackQueryHandler(supervisor_main_menu_callback, pattern="^sendclient\\|.*$"))
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()