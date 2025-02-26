#!/usr/bin/env python3
# supervisor_bot.py

import logging
import json
import cloudinary
import cloudinary.uploader
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

# -----------------------------------------------------------------------------
# Logging and Cloudinary configuration
# -----------------------------------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
da_bot = Bot(token=config.DA_BOT_TOKEN)

cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET
)

# -----------------------------------------------------------------------------
# Conversation states
# (State numbers: 
#   0: SUBSCRIPTION_PHONE, 
#   1: MAIN_MENU, 
#   2: SEARCH_TICKETS, 
#   3: AWAITING_RESPONSE, 
#   4: EDIT_PROMPT, 
#   5: EDIT_FIELD, 
#   6: EDIT_IMAGE, 
#   7: EDIT_REASON, 
#   8: EDIT_TYPE)
# -----------------------------------------------------------------------------
(
    SUBSCRIPTION_PHONE,
    MAIN_MENU,
    SEARCH_TICKETS,
    AWAITING_RESPONSE,
    EDIT_PROMPT,
    EDIT_FIELD,
    EDIT_IMAGE,
    EDIT_REASON,
    EDIT_TYPE
) = range(9)

# -----------------------------------------------------------------------------
# Reason -> Type mapping
# -----------------------------------------------------------------------------
ISSUE_OPTIONS = {
    "المخزن": ["تالف", "منتهي الصلاحية", "عجز في المخزون", "تحضير خاطئ"],
    "المورد": ["خطا بالمستندات", "رصيد غير موجود", "اوردر خاطئ", "اوردر بكميه اكبر",
               "خطا فى الباركود او اسم الصنف", "اوردر وهمى", "خطأ فى الاسعار",
               "تخطى وقت الانتظار لدى العميل", "اختلاف بيانات الفاتورة", "توالف مصنع"],
    "العميل": ["رفض الاستلام", "مغلق", "عطل بالسيستم", "لا يوجد مساحة للتخزين", "شك عميل فى سلامة العبوه"],
    "التسليم": ["وصول متاخر", "تالف", "عطل بالسياره"]
}

def get_issue_types_for_reason(reason: str):
    return ISSUE_OPTIONS.get(reason, [])

# -----------------------------------------------------------------------------
# Helper: safe_edit_message
# -----------------------------------------------------------------------------
def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

# -----------------------------------------------------------------------------
# Start & Subscription
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Main Callback Handler
# -----------------------------------------------------------------------------
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
                    query.bot.send_photo(
                        chat_id=query.message.chat.id,
                        photo=ticket['image_url'],
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                else:
                    query.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
        else:
            safe_edit_message(query, text="لا توجد تذاكر مفتوحة حالياً.")
        return MAIN_MENU

    elif data == "menu_query_issue":
        safe_edit_message(query, text="أدخل رقم الطلب:")
        return SEARCH_TICKETS

    elif data.startswith("view|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if ticket:
            try:
                logs = ""
                if ticket.get("logs"):
                    logs_list = json.loads(ticket["logs"])
                    logs = "\n".join([
                        f"{entry.get('timestamp', '')}: {entry.get('action', '')} - {entry.get('message', '')}"
                        for entry in logs_list
                    ])
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
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text="أدخل رسالة الحل للمشكلة:",
            reply_markup=ForceReply(selective=True)
        )
        return AWAITING_RESPONSE

    elif data.startswith("moreinfo|"):
        ticket_id = int(data.split("|")[1])
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'moreinfo'
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text="أدخل المعلومات الإضافية المطلوبة للتذكرة:",
            reply_markup=ForceReply(selective=True)
        )
        return AWAITING_RESPONSE

    elif data.startswith("sendclient|"):
        ticket_id = int(data.split("|")[1])
        keyboard = [[InlineKeyboardButton("إرسال كما هي", callback_data=f"confirm_sendclient|{ticket_id}"),
                     InlineKeyboardButton("تعديل التفاصيل", callback_data=f"edit_sendclient|{ticket_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query,
                          text="هل تريد إرسال التذكرة إلى العميل كما هي أم تعديل التفاصيل؟",
                          reply_markup=reply_markup)
        return MAIN_MENU

    elif data.startswith("confirm_sendclient|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if ticket:
            send_to_client(ticket)
            safe_edit_message(query, text=f"تم إرسال التذكرة #{ticket_id} إلى العميل.")
        else:
            safe_edit_message(query, text="لا يمكن العثور على التذكرة.")
        return MAIN_MENU

    elif data.startswith("cancel_sendclient|"):
        safe_edit_message(query, text="تم إلغاء الإرسال إلى العميل.")
        return MAIN_MENU

    elif data.startswith("edit_sendclient|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            safe_edit_message(query, text="التذكرة غير موجودة.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['order_id'] = ticket['order_id']
        context.user_data['issue_description'] = ticket['issue_description']
        context.user_data['issue_reason'] = ticket['issue_reason']
        context.user_data['issue_type'] = ticket['issue_type']
        context.user_data['client'] = ticket['client']
        context.user_data['image_url'] = ticket.get('image_url', None)
        context.user_data['action'] = 'edit_for_client'
        return show_ticket_summary_for_edit_supervisor(query, context)

    elif data.startswith("sendto_da|"):
        ticket_id = int(data.split("|")[1])
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            safe_edit_message(query, text="لا يمكن العثور على التذكرة.")
            return MAIN_MENU
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

# -----------------------------------------------------------------------------
# Summaries & Editing (Supervisor)
# -----------------------------------------------------------------------------
def show_ticket_summary_for_edit_supervisor(query, context: CallbackContext) -> int:
    data = context.user_data
    summary = (
        f"رقم الطلب: {data.get('order_id','')}\n"
        f"الوصف: {data.get('issue_description','')}\n"
        f"سبب المشكلة: {data.get('issue_reason','')}\n"
        f"نوع المشكلة: {data.get('issue_type','')}\n"
        f"العميل: {data.get('client','')}\n"
        f"الصورة: {data.get('image_url','لا توجد')}"
    )
    text = f"ملخص التذكرة:\n{summary}\nهل تريد تعديل التذكرة قبل الإرسال؟"
    kb = [
        [InlineKeyboardButton("نعم", callback_data="sup_edit_ticket_yes"),
         InlineKeyboardButton("لا", callback_data="sup_edit_ticket_no")]
    ]
    rm = InlineKeyboardMarkup(kb)
    safe_edit_message(query, text=text, reply_markup=rm)
    return EDIT_PROMPT

def supervisor_edit_prompt_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "sup_edit_ticket_no":
        return finalize_sendclient(query, context)
    elif data == "sup_edit_ticket_yes":
        keyboard = [
            [InlineKeyboardButton("رقم الطلب", callback_data="sup_edit_field_order"),
             InlineKeyboardButton("الوصف", callback_data="sup_edit_field_description")],
            [InlineKeyboardButton("سبب المشكلة", callback_data="sup_edit_field_reason"),
             InlineKeyboardButton("نوع المشكلة", callback_data="sup_edit_field_type")],
            [InlineKeyboardButton("الصورة", callback_data="sup_edit_field_image"),
             InlineKeyboardButton("العميل", callback_data="sup_edit_field_client")],
            [InlineKeyboardButton("تم", callback_data="sup_edit_done")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر الحقل الذي تريد تعديله:", reply_markup=reply_markup)
        return EDIT_FIELD
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def supervisor_edit_field_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "sup_edit_done":
        return show_ticket_summary_for_edit_supervisor(query, context)
    elif data == "sup_edit_field_image":
        safe_edit_message(query, text="من فضلك أرسل الصورة الجديدة:")
        return EDIT_IMAGE
    elif data == "sup_edit_field_reason":
        keyboard = []
        for reason_key in ISSUE_OPTIONS:
            keyboard.append([InlineKeyboardButton(reason_key, callback_data=f"sup_reason_{reason_key}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر سبب المشكلة الجديد:", reply_markup=reply_markup)
        return EDIT_REASON
    elif data == "sup_edit_field_type":
        current_reason = context.user_data.get('issue_reason')
        if not current_reason:
            keyboard = []
            for reason_key in ISSUE_OPTIONS:
                keyboard.append([InlineKeyboardButton(reason_key, callback_data=f"sup_reason_{reason_key}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(query, text="اختر سبب المشكلة أولاً:", reply_markup=reply_markup)
            return EDIT_REASON
        else:
            types_for_reason = get_issue_types_for_reason(current_reason)
            keyboard = []
            for t in types_for_reason:
                keyboard.append([InlineKeyboardButton(t, callback_data=f"sup_type_{t}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(query, text=f"اختر النوع المناسب ({current_reason}):", reply_markup=reply_markup)
            return EDIT_TYPE
    elif data == "sup_edit_field_order":
        safe_edit_message(query, text="أدخل رقم الطلب الجديد:")
        context.user_data['edit_field'] = "order_id"
        return EDIT_FIELD
    elif data == "sup_edit_field_description":
        safe_edit_message(query, text="أدخل وصفاً جديداً:")
        context.user_data['edit_field'] = "issue_description"
        return EDIT_FIELD
    elif data == "sup_edit_field_client":
        safe_edit_message(query, text="أدخل اسم العميل الجديد:")
        context.user_data['edit_field'] = "client"
        return EDIT_FIELD
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return EDIT_FIELD

def supervisor_edit_field_input_handler(update: Update, context: CallbackContext) -> int:
    field = context.user_data.get('edit_field')
    new_value = update.message.text.strip()
    if not field:
        update.message.reply_text("لا يوجد حقل محدد للتعديل.")
        return EDIT_FIELD
    context.user_data[field] = new_value
    update.message.reply_text(f"تم تحديث {field} إلى: {new_value}")
    keyboard = [
        [InlineKeyboardButton("رقم الطلب", callback_data="sup_edit_field_order"),
         InlineKeyboardButton("الوصف", callback_data="sup_edit_field_description")],
        [InlineKeyboardButton("سبب المشكلة", callback_data="sup_edit_field_reason"),
         InlineKeyboardButton("نوع المشكلة", callback_data="sup_edit_field_type")],
        [InlineKeyboardButton("الصورة", callback_data="sup_edit_field_image"),
         InlineKeyboardButton("العميل", callback_data="sup_edit_field_client")],
        [InlineKeyboardButton("تم", callback_data="sup_edit_done")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("اختر الحقل الذي تريد تعديله:", reply_markup=reply_markup)
    return EDIT_FIELD

def supervisor_edit_image_handler(update: Update, context: CallbackContext) -> int:
    if update.message.photo:
        photo = update.message.photo[-1]
        file = photo.get_file()
        import io
        bio = io.BytesIO()
        file.download(out=bio)
        bio.seek(0)
        try:
            result = cloudinary.uploader.upload(bio)
            secure_url = result.get("secure_url")
            if secure_url:
                context.user_data['image_url'] = secure_url
                update.message.reply_text("تم تحديث الصورة بنجاح.")
            else:
                update.message.reply_text("فشل رفع الصورة. حاول مرة أخرى:")
                return EDIT_IMAGE
        except Exception as e:
            logger.error("Error uploading image: %s", e)
            update.message.reply_text("خطأ أثناء رفع الصورة. حاول مرة أخرى:")
            return EDIT_IMAGE
    else:
        update.message.reply_text("الملف المرسل ليس صورة صالحة. أعد الإرسال:")
        return EDIT_IMAGE
    keyboard = [
        [InlineKeyboardButton("رقم الطلب", callback_data="sup_edit_field_order"),
         InlineKeyboardButton("الوصف", callback_data="sup_edit_field_description")],
        [InlineKeyboardButton("سبب المشكلة", callback_data="sup_edit_field_reason"),
         InlineKeyboardButton("نوع المشكلة", callback_data="sup_edit_field_type")],
        [InlineKeyboardButton("الصورة", callback_data="sup_edit_field_image"),
         InlineKeyboardButton("العميل", callback_data="sup_edit_field_client")],
        [InlineKeyboardButton("تم", callback_data="sup_edit_done")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("اختر الحقل الذي تريد تعديله:", reply_markup=reply_markup)
    return EDIT_FIELD

def supervisor_edit_reason_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    reason = query.data.split("sup_reason_")[1]
    context.user_data['issue_reason'] = reason
    query.message.reply_text(f"تم تحديث سبب المشكلة إلى: {reason}")
    types_for_reason = get_issue_types_for_reason(reason)
    if not types_for_reason:
        query.message.reply_text("لا توجد أنواع متاحة لهذا السبب.")
        return EDIT_FIELD
    keyboard = []
    for t in types_for_reason:
        keyboard.append([InlineKeyboardButton(t, callback_data=f"sup_type_{t}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.message.reply_text("اختر النوع المناسب:", reply_markup=reply_markup)
    return EDIT_TYPE

def supervisor_edit_type_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    new_type = query.data.split("sup_type_")[1]
    context.user_data['issue_type'] = new_type
    query.message.reply_text(f"تم تحديث نوع المشكلة إلى: {new_type}")
    keyboard = [
        [InlineKeyboardButton("رقم الطلب", callback_data="sup_edit_field_order"),
         InlineKeyboardButton("الوصف", callback_data="sup_edit_field_description")],
        [InlineKeyboardButton("سبب المشكلة", callback_data="sup_edit_field_reason"),
         InlineKeyboardButton("نوع المشكلة", callback_data="sup_edit_field_type")],
        [InlineKeyboardButton("الصورة", callback_data="sup_edit_field_image"),
         InlineKeyboardButton("العميل", callback_data="sup_edit_field_client")],
        [InlineKeyboardButton("تم", callback_data="sup_edit_done")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.message.reply_text("اختر الحقل الذي تريد تعديله:", reply_markup=reply_markup)
    return EDIT_FIELD

def finalize_sendclient(query, context: CallbackContext) -> int:
    ticket_id = context.user_data.get('ticket_id')
    existing_ticket = db.get_ticket(ticket_id)
    if not existing_ticket:
        safe_edit_message(query, text="التذكرة غير موجودة.")
        context.user_data.clear()
        return MAIN_MENU
    pseudo_ticket = {
        'ticket_id': ticket_id,
        'order_id': context.user_data.get('order_id', existing_ticket['order_id']),
        'issue_description': context.user_data.get('issue_description', existing_ticket['issue_description']),
        'issue_reason': context.user_data.get('issue_reason', existing_ticket['issue_reason']),
        'issue_type': context.user_data.get('issue_type', existing_ticket['issue_type']),
        'client': context.user_data.get('client', existing_ticket['client']),
        'image_url': context.user_data.get('image_url', existing_ticket.get('image_url')),
        'status': existing_ticket['status']
    }
    send_to_client(pseudo_ticket)
    safe_edit_message(query, text=f"تم إرسال التذكرة #{ticket_id} إلى العميل بالتفاصيل المعدلة.")
    context.user_data.clear()
    return MAIN_MENU

def send_to_client(ticket, message_text=None):
    client_name = ticket.get('client')
    clients = db.get_clients_by_name(client_name)
    bot = Bot(token=config.CLIENT_BOT_TOKEN)
    description = message_text if message_text is not None else ticket['issue_description']
    message = (
        f"<b>تذكرة من المشرف</b>\n"
        f"تذكرة #{ticket['ticket_id']}\n"
        f"رقم الطلب: {ticket['order_id']}\n"
        f"الوصف: {description}\n"
        f"الحالة: {ticket['status']}"
    )
    keyboard = [
        [InlineKeyboardButton("ارسال حل المشكلة", callback_data=f"solve|{ticket['ticket_id']}|now")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if not clients:
        logger.warning(f"send_to_client: No client subscriptions found for client name '{client_name}'")
    for c in clients:
        try:
            if ticket.get('image_url'):
                bot.send_photo(
                    chat_id=c['chat_id'],
                    photo=ticket['image_url'],
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                bot.send_message(
                    chat_id=c['chat_id'],
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error notifying client {c['chat_id']}: {e}")

# -----------------------------------------------------------------------------
# Searching, Solving, & Global Handlers
# -----------------------------------------------------------------------------
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
        notify_da_moreinfo(ticket_id, response)
        update.message.reply_text("تم إرسال المعلومات الإضافية إلى الوكيل.")
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
    if data.startswith("solve|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'solve'
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text="أدخل رسالة الحل للمشكلة:",
            reply_markup=ForceReply(selective=True)
        )
        return AWAITING_RESPONSE
    elif data.startswith("moreinfo|"):
        try:
            ticket_id = int(data.split("|")[1])
        except (IndexError, ValueError):
            safe_edit_message(query, "بيانات التذكرة غير صحيحة.")
            return MAIN_MENU
        context.user_data['ticket_id'] = ticket_id
        context.user_data['action'] = 'moreinfo'
        context.bot.send_message(
            chat_id=query.message.chat.id,
            text="أدخل المعلومات الإضافية المطلوبة للتذكرة:",
            reply_markup=ForceReply(selective=True)
        )
        return AWAITING_RESPONSE
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
    if context.user_data.get('action') in ['solve', 'moreinfo'] and context.user_data.get('ticket_id'):
        awaiting_response_handler(update, context)
    else:
        update.message.reply_text("الرجاء اختيار خيار من القائمة.")

# -----------------------------------------------------------------------------
# Main function for Supervisor Bot
# -----------------------------------------------------------------------------
def main():
    updater = Updater(config.SUPERVISOR_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_error_handler(error_handler)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBSCRIPTION_PHONE: [MessageHandler(Filters.text & ~Filters.command, subscription_phone)],
            MAIN_MENU: [CallbackQueryHandler(
                supervisor_main_menu_callback,
                pattern=r"^(menu_show_all|menu_query_issue|view\|.*|solve\|.*|moreinfo\|.*|sendclient\|.*|sendto_da\|.*|confirm_sendclient\|.*|cancel_sendclient\|.*|edit_sendclient\|.*)$"
            )],
            SEARCH_TICKETS: [MessageHandler(Filters.text & ~Filters.command, search_tickets)],
            AWAITING_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, awaiting_response_handler)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("تم إلغاء العملية."))]
    )
    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(global_supervisor_action_handler, pattern=r"^(solve\|.*|moreinfo\|.*|sendclient\|.*|sendto_da\|.*)$"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, global_supervisor_text_handler))
    # Removed the extra MessageHandler(Filters.text, default_handler_supervisor) to avoid duplicate main menu messages.

    # -------------------------
    # Global /start handler (group -1)
    # -------------------------
    dp.add_handler(CommandHandler('start', start), group=-1)

    logger.info("Supervisor bot started successfully.")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()