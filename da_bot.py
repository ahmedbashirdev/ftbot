#!/usr/bin/env python3
# da_bot.py

import logging
import datetime
import urllib.parse
from io import BytesIO
import requests
import cloudinary
import cloudinary.uploader
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
import notifier  # For sending notifications to supervisors

# Configure Cloudinary
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Conversation States
# -----------------------------------------------------------------------------
(
    SUBSCRIPTION_PHONE,   # 0
    MAIN_MENU,            # 1
    NEW_ISSUE_ORDER,      # 2
    NEW_ISSUE_DESCRIPTION,# 3
    NEW_ISSUE_REASON,     # 4
    NEW_ISSUE_TYPE,       # 5
    ASK_IMAGE,            # 6
    WAIT_IMAGE,           # 7
    AWAITING_DA_RESPONSE, # 8
    EDIT_FIELD,           # 9
    EDIT_IMAGE            # 10
) = range(11)

# -----------------------------------------------------------------------------
# Mapping for issue reasons to types
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

def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

# -----------------------------------------------------------------------------
# Start & Subscription Handlers
# -----------------------------------------------------------------------------
def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    logger.info("Received /start from user %s", user.id)
    sub = db.get_subscription(user.id, "DA")
    if not sub:
        update.message.reply_text("أهلاً! يرجى إدخال رقم هاتفك للاشتراك (DA):")
        return SUBSCRIPTION_PHONE
    else:
        keyboard = [
            [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
             InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"مرحباً {user.first_name}", reply_markup=reply_markup)
        return MAIN_MENU

def subscription_phone(update: Update, context: CallbackContext) -> int:
    phone = update.message.text.strip()
    user = update.effective_user
    logger.info("Subscribing user %s with phone %s", user.id, phone)
    db.add_subscription(user.id, phone, 'DA', "DA", None,
                        user.username, user.first_name, user.last_name, update.effective_chat.id)
    keyboard = [
        [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ DA!", reply_markup=reply_markup)
    return MAIN_MENU

# -----------------------------------------------------------------------------
# Main Menu Callback Handler
# -----------------------------------------------------------------------------
def da_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("da_main_menu_callback: Received data: %s", data)

    if data == "menu_add_issue":
        return fetch_orders_da(query, context)
    elif data == "menu_query_issue":
        user = query.from_user
        tickets = db.get_tickets_by_user(user.id)
        if tickets:
            status_map = {
                "Opened": "مفتوحة",
                "Pending DA Action": "في انتظار إجراء الوكيل",
                "Awaiting Client Response": "في انتظار رد العميل",
                "Client Responded": "تم رد العميل",
                "Client Ignored": "تم تجاهل العميل",
                "Closed": "مغلقة",
                "Additional Info Provided": "تم توفير معلومات إضافية",
                "Pending DA Response": "في انتظار رد الوكيل"
            }
            for ticket in tickets:
                st_ar = status_map.get(ticket['status'], ticket['status'])
                res = "\nالحل: تم الحل." if ticket['status'] == "Closed" else ""
                sep = "\n-----------------------------"
                text = (
                    f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                    f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
                    f"<b>الوصف:</b> {ticket['issue_description']}\n"
                    f"<b>الحالة:</b> {st_ar}{res}\n"
                    f"{sep}"
                )
                query.message.reply_text(text, parse_mode="HTML")
        else:
            safe_edit_message(query, text="لا توجد تذاكر.")
        return MAIN_MENU

    elif data.startswith("select_order|"):
        parts = data.split("|")
        if len(parts) < 3:
            safe_edit_message(query, "بيانات الطلب غير صحيحة.")
            return MAIN_MENU
        order_id = parts[1]
        client_name = parts[2]
        context.user_data['order_id'] = order_id
        context.user_data['client'] = client_name
        # Let the DA pick a reason first
        reason_buttons = [
            [InlineKeyboardButton("المخزن", callback_data="issue_reason_المخزن")],
            [InlineKeyboardButton("المورد", callback_data="issue_reason_المورد")],
            [InlineKeyboardButton("العميل", callback_data="issue_reason_العميل")],
            [InlineKeyboardButton("التسليم", callback_data="issue_reason_التسليم")]
        ]
        rm = InlineKeyboardMarkup(reason_buttons)
        safe_edit_message(
            query,
            text=f"تم اختيار الطلب رقم {order_id} للعميل {client_name}.\nالآن، اختر سبب المشكلة:",
            reply_markup=rm
        )
        return NEW_ISSUE_REASON

    elif data in ["attach_yes", "attach_no"]:
        if data == "attach_yes":
            safe_edit_message(query, text="يرجى إرسال الصورة:")
            return WAIT_IMAGE
        else:
            return show_ticket_summary_for_edit(query, context)

    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def fetch_orders_da(query, context) -> int:
    user = query.from_user
    logger.info("Fetching orders for DA user %s", user.id)
    sub = db.get_subscription(user.id, "DA")
    if not sub or not sub.get("phone"):
        safe_edit_message(query, "لم يتم العثور على رقم الهاتف في بيانات الاشتراك. يرجى الاشتراك مرة أخرى.")
        return MAIN_MENU
    agent_phone = sub["phone"]
    # For testing: using a static date
    order_date = "2024-08-18"
    url = f"https://3e5440qr0c.execute-api.eu-west-3.amazonaws.com/dev/locus_info?agent_phone={agent_phone}&order_date={order_date}"
    logger.debug("Calling API URL: %s", url)
    safe_edit_message(query, text="جاري تحميل الطلبات...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        orders = data.get("data", [])
        if not orders:
            safe_edit_message(query, "لا توجد طلبات متاحة لهذا اليوم.")
            return MAIN_MENU
        keyboard = []
        for o in orders:
            o_id = o.get("order_id")
            c_name = o.get("client_name")
            if o_id and c_name:
                btn_text = f"طلب {o_id} - {c_name}"
                cb_data = f"select_order|{o_id}|{c_name}"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=cb_data)])
        rm = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر الطلب الذي تريد رفع مشكلة عنه:", reply_markup=rm)
        return NEW_ISSUE_ORDER
    except Exception as e:
        logger.error("Error fetching orders: %s", e)
        safe_edit_message(query, "حدث خطأ أثناء جلب الطلبات. حاول مرة أخرى لاحقاً.")
        return MAIN_MENU

def new_issue_reason_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    reason = query.data.split("_", 2)[2]
    context.user_data['issue_reason'] = reason
    types = get_issue_types_for_reason(reason)
    kb = [[InlineKeyboardButton(t, callback_data="issue_type_" + t)] for t in types]
    rm = InlineKeyboardMarkup(kb)
    safe_edit_message(query, text="اختر نوع المشكلة:", reply_markup=rm)
    return NEW_ISSUE_TYPE

def new_issue_type_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    issue_type = urllib.parse.unquote(query.data.split("_", 2)[2])
    context.user_data['issue_type'] = issue_type
    safe_edit_message(query, text="الرجاء وصف المشكلة:")
    return NEW_ISSUE_DESCRIPTION

def new_issue_description(update: Update, context: CallbackContext) -> int:
    desc = update.message.text.strip()
    context.user_data['description'] = desc
    kb = [
        [InlineKeyboardButton("نعم", callback_data="attach_yes"),
         InlineKeyboardButton("لا", callback_data="attach_no")]
    ]
    rm = InlineKeyboardMarkup(kb)
    update.message.reply_text("هل تريد إرفاق صورة للمشكلة؟", reply_markup=rm)
    return ASK_IMAGE

def wait_image(update: Update, context: CallbackContext) -> int:
    try:
        if update.message.photo:
            photo = update.message.photo[-1]
            file = photo.get_file()
            bio = BytesIO()
            file.download(out=bio)
            bio.seek(0)
            result = cloudinary.uploader.upload(bio)
            secure_url = result.get("secure_url")
            if secure_url:
                context.user_data["image"] = secure_url
                return show_ticket_summary_for_edit(update.message, context)
            else:
                update.message.reply_text("فشل رفع الصورة. حاول مرة أخرى:")
                return WAIT_IMAGE
        elif update.message.document:
            update.message.reply_text("⚠️ الملف المرفق ليس صورة. الرجاء إرسال صورة صالحة.")
            return WAIT_IMAGE
        else:
            update.message.reply_text("⚠️ لم يتم إرسال صورة. أعد الإرسال:")
            return WAIT_IMAGE
    except Exception as e:
        logger.error("Error in wait_image(): %s", e)
        update.message.reply_text("⚠️ حدث خطأ أثناء معالجة الصورة. حاول مرة أخرى.")
        return WAIT_IMAGE

def show_ticket_summary_for_edit(source, context: CallbackContext):
    data = context.user_data
    summary = (
        f"رقم الطلب: {data.get('order_id','')}\n"
        f"الوصف: {data.get('description','')}\n"
        f"سبب المشكلة: {data.get('issue_reason','')}\n"
        f"نوع المشكلة: {data.get('issue_type','')}\n"
        f"العميل: {data.get('client','')}\n"
        f"الصورة: {data.get('image','لا توجد')}"
    )
    text = "ملخص التذكرة المدخلة:\n" + summary + "\nهل تريد تعديل التذكرة قبل الإرسال؟"
    kb = [
        [InlineKeyboardButton("نعم", callback_data="da_edit_yes"),
         InlineKeyboardButton("لا", callback_data="da_edit_no")]
    ]
    rm = InlineKeyboardMarkup(kb)
    if hasattr(source, 'edit_message_text'):
        source.edit_message_text(text=text, reply_markup=rm)
    else:
        context.bot.send_message(chat_id=source.chat.id, text=text, reply_markup=rm)
    return MAIN_MENU

def da_edit_prompt_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    choice = query.data
    if choice == "da_edit_no":
        return finalize_ticket_da(query, context, image_url=context.user_data.get('image', None))
    elif choice == "da_edit_yes":
        return da_edit_field_menu(query, context)
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def da_edit_field_menu(query, context: CallbackContext) -> int:
    kb = [
        [InlineKeyboardButton("رقم الطلب", callback_data="da_edit_field_order"),
         InlineKeyboardButton("الوصف", callback_data="da_edit_field_description")],
        [InlineKeyboardButton("سبب المشكلة", callback_data="da_edit_field_reason"),
         InlineKeyboardButton("الصورة", callback_data="da_edit_field_image")],
        [InlineKeyboardButton("العميل", callback_data="da_edit_field_client")],
        [InlineKeyboardButton("تم", callback_data="da_edit_done")]
    ]
    rm = InlineKeyboardMarkup(kb)
    safe_edit_message(query, text="اختر الحقل الذي تريد تعديله:", reply_markup=rm)
    return EDIT_FIELD

def da_edit_field_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "da_edit_done":
        return finalize_ticket_da(query, context, image_url=context.user_data.get('image', None))
    elif data == "da_edit_field_image":
        safe_edit_message(query, text="من فضلك أرسل الصورة الجديدة:")
        return EDIT_IMAGE
    elif data == "da_edit_field_reason":
        reason_kb = []
        for reason_key in ISSUE_OPTIONS:
            reason_kb.append([InlineKeyboardButton(reason_key, callback_data=f"da_reason_{reason_key}")])
        rm = InlineKeyboardMarkup(reason_kb)
        safe_edit_message(query, text="اختر سبب المشكلة الجديد:", reply_markup=rm)
        return EDIT_FIELD
    elif data.startswith("da_reason_"):
        new_reason = data[len("da_reason_"):]
        context.user_data['issue_reason'] = new_reason
        query.message.reply_text(f"تم تحديث سبب المشكلة إلى: {new_reason}")
        kb = []
        types = get_issue_types_for_reason(new_reason)
        if not types:
            query.message.reply_text("لا توجد أنواع متاحة لهذا السبب.")
            return EDIT_FIELD
        for t in types:
            kb.append([InlineKeyboardButton(t, callback_data=f"da_type_{t}")])
        rm = InlineKeyboardMarkup(kb)
        query.message.reply_text("اختر النوع المناسب:", reply_markup=rm)
        return EDIT_FIELD
    elif data.startswith("da_type_"):
        new_type = data[len("da_type_"):]
        context.user_data['issue_type'] = new_type
        query.message.reply_text(f"تم تحديث نوع المشكلة إلى: {new_type}")
        return da_edit_field_menu(query, context)
    elif data == "da_edit_field_order":
        safe_edit_message(query, text="أدخل رقم الطلب الجديد:")
        context.user_data['edit_field'] = "order_id"
        return EDIT_FIELD
    elif data == "da_edit_field_description":
        safe_edit_message(query, text="أدخل الوصف الجديد:")
        context.user_data['edit_field'] = "description"
        return EDIT_FIELD
    elif data == "da_edit_field_client":
        safe_edit_message(query, text="أدخل اسم العميل الجديد:")
        context.user_data['edit_field'] = "client"
        return EDIT_FIELD
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return EDIT_FIELD

def da_edit_field_input_handler(update: Update, context: CallbackContext) -> int:
    field = context.user_data.get('edit_field')
    new_value = update.message.text.strip()
    if not field:
        update.message.reply_text("لا يوجد حقل محدد للتعديل.")
        return EDIT_FIELD
    context.user_data[field] = new_value
    update.message.reply_text(f"تم تحديث {field} إلى: {new_value}")
    return da_edit_field_menu(update.message, context)

def da_edit_image_handler(update: Update, context: CallbackContext) -> int:
    if update.message.photo:
        photo = update.message.photo[-1]
        file = photo.get_file()
        bio = BytesIO()
        file.download(out=bio)
        bio.seek(0)
        try:
            result = cloudinary.uploader.upload(bio)
            secure_url = result.get("secure_url")
            if secure_url:
                context.user_data['image'] = secure_url
                update.message.reply_text("تم تحديث الصورة بنجاح.")
            else:
                update.message.reply_text("فشل رفع الصورة. حاول مرة أخرى:")
                return EDIT_IMAGE
        except Exception as e:
            logger.error("Error uploading new image: %s", e)
            update.message.reply_text("خطأ أثناء رفع الصورة. حاول مرة أخرى:")
            return EDIT_IMAGE
    else:
        update.message.reply_text("الملف المرسل ليس صورة صالحة. أعد الإرسال:")
        return EDIT_IMAGE
    return da_edit_field_menu(update.message, context)

# -----------------------------------------------------------------------------
# Additional Info Flow (da_moreinfo)
# -----------------------------------------------------------------------------
def da_moreinfo_callback_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    try:
        ticket_id = int(query.data.split("|")[1])
    except (IndexError, ValueError):
        safe_edit_message(query, text="خطأ في بيانات التذكرة.")
        return MAIN_MENU
    context.user_data['ticket_id'] = ticket_id
    logger.debug("da_moreinfo_callback_handler: Stored ticket_id=%s", ticket_id)
    prompt_da_for_more_info(ticket_id, query.message.chat.id, context)
    context.user_data['action'] = 'moreinfo'
    return AWAITING_DA_RESPONSE

def prompt_da_for_more_info(ticket_id: int, chat_id: int, context: CallbackContext):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        logger.error("prompt_da_for_more_info: Ticket %s not found", ticket_id)
        context.bot.send_message(chat_id=chat_id, text="خطأ: التذكرة غير موجودة.")
        return
    txt = (
        f"<b>التذكرة #{ticket_id}</b>\n"
        f"رقم الطلب: {ticket['order_id']}\n"
        f"الوصف: {ticket['issue_description']}\n"
        f"الحالة: {ticket['status']}\n\n"
        "يرجى إدخال المعلومات الإضافية المطلوبة للتذكرة:"
    )
    context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="HTML", reply_markup=ForceReply(selective=True))

def da_awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    logger.debug("da_awaiting_response_handler: user_data=%s", context.user_data)
    add_info = update.message.text.strip()
    t_id = context.user_data.get('ticket_id')
    if t_id is None:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    if not add_info:
        update.message.reply_text("الرجاء إدخال معلومات إضافية.")
        return MAIN_MENU
    success = db.update_ticket_status(t_id, "Additional Info Provided", {"action": "da_moreinfo", "message": add_info})
    if not success:
        update.message.reply_text("حدث خطأ أثناء تحديث التذكرة.")
        return MAIN_MENU
    logger.debug("Ticket %s updated with additional info: %s", t_id, add_info)
    try:
        notifier.notify_supervisors_da_moreinfo(t_id, add_info)
        update.message.reply_text("تم إرسال المعلومات الإضافية إلى المشرف. شكراً لك.")
    except Exception as e:
        logger.error("Error notifying supervisors: %s", e)
        update.message.reply_text("حدث خطأ أثناء إرسال المعلومات إلى المشرف.")
    context.user_data.pop('ticket_id', None)
    context.user_data.pop('action', None)
    return MAIN_MENU
# -----------------------------------------------------------------------------
# DA Callback Handler for “close|…” etc.
# -----------------------------------------------------------------------------
def da_callback_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("da_callback_handler: Received callback data: %s", data)
    if data.startswith("close|"):
        ticket_id = int(data.split("|")[1])
        db.update_ticket_status(ticket_id, "Closed", {"action": "da_closed"})
        safe_edit_message(query, text=f"تم إغلاق التذكرة #{ticket_id}.")
    elif data.startswith("da_moreinfo|"):
        return da_moreinfo_callback_handler(update, context)
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
    return MAIN_MENU

# -----------------------------------------------------------------------------
# Finalize Ticket Flow
# -----------------------------------------------------------------------------
def finalize_ticket_da(source, context, image_url):
    if hasattr(source, 'from_user'):
        user = source.from_user
    else:
        user = source.message.from_user
    data = context.user_data
    order_id = data.get('order_id')
    description = data.get('description')
    issue_reason = data.get('issue_reason')
    issue_type = data.get('issue_type')
    client_selected = data.get('client', 'غير محدد')
    ticket_id = db.add_ticket(order_id, description, issue_reason, issue_type,
                              client_selected, image_url, "Opened", user.id)
    if hasattr(source, 'edit_message_text'):
        source.edit_message_text(f"تم إنشاء التذكرة برقم {ticket_id}.\nالحالة: Opened")
    else:
        context.bot.send_message(chat_id=user.id,
                                 text=f"تم إنشاء التذكرة برقم {ticket_id}.\nالحالة: Opened")
    ticket = db.get_ticket(ticket_id)
    notifier.notify_supervisors(ticket)
    context.user_data.clear()
    return MAIN_MENU

# -----------------------------------------------------------------------------
# Default Handlers
# -----------------------------------------------------------------------------
def default_handler_da(update: Update, context: CallbackContext) -> int:
    kb = [
        [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    rm = InlineKeyboardMarkup(kb)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=rm)
    return MAIN_MENU

def global_da_text_handler(update: Update, context: CallbackContext) -> None:
    if context.user_data.get('action') == 'moreinfo' and context.user_data.get('ticket_id'):
        da_awaiting_response_handler(update, context)
    else:
        default_handler_da(update, context)

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    if not config.DA_BOT_TOKEN:
        logger.error("DA_BOT_TOKEN not found in config!")
        return
    try:
        updater = Updater(config.DA_BOT_TOKEN, use_context=True)
        dp = updater.dispatcher

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                SUBSCRIPTION_PHONE: [
                    MessageHandler(Filters.text & ~Filters.command, subscription_phone)
                ],
                MAIN_MENU: [
                    CallbackQueryHandler(da_main_menu_callback, pattern="^(menu_add_issue|menu_query_issue|client_option_.*|issue_reason_.*|issue_type_.*|attach_yes|attach_no)$"),
                    CallbackQueryHandler(da_edit_prompt_callback, pattern="^(da_edit_yes|da_edit_no)$"),
                    MessageHandler(Filters.text & ~Filters.command, default_handler_da)
                ],
                NEW_ISSUE_ORDER: [CallbackQueryHandler(da_main_menu_callback, pattern="^select_order\\|")],
                NEW_ISSUE_REASON: [CallbackQueryHandler(new_issue_reason_callback, pattern="^issue_reason_.*")],
                NEW_ISSUE_TYPE: [CallbackQueryHandler(new_issue_type_callback, pattern="^issue_type_.*")],
                NEW_ISSUE_DESCRIPTION: [MessageHandler(Filters.text & ~Filters.command, new_issue_description)],
                ASK_IMAGE: [CallbackQueryHandler(da_main_menu_callback, pattern="^(attach_yes|attach_no)$")],
                WAIT_IMAGE: [MessageHandler(Filters.photo, wait_image),
                             MessageHandler(Filters.text, wait_image)],
                EDIT_FIELD: [
                    CallbackQueryHandler(da_edit_field_callback, pattern="^(da_edit_field_.*|da_edit_done|da_reason_.*|da_type_.*)$"),
                    MessageHandler(Filters.text & ~Filters.command, da_edit_field_input_handler)
                ],
                EDIT_IMAGE: [
                    MessageHandler(Filters.photo, da_edit_image_handler),
                    MessageHandler(Filters.text, da_edit_image_handler)
                ],
                AWAITING_DA_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)]
            },
            fallbacks=[
                CommandHandler('cancel', lambda u, c: u.message.reply_text("تم إلغاء العملية.")),
                CommandHandler('start', lambda u, c: start(u, c))
            ],
            allow_reentry=True
        )
        dp.add_handler(conv_handler)

        dp.add_handler(CallbackQueryHandler(da_callback_handler, pattern="^(close\\||da_moreinfo\\|).*"))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, global_da_text_handler))

        # Global /start handler to ensure /start always resets the conversation and shows the main menu
        dp.add_handler(CommandHandler("start", start))

        logger.info("DA bot started successfully.")
        updater.start_polling()
        updater.idle()
    except Exception as e:
        logger.error("Failed to start DA bot: %s", e)

if __name__ == '__main__':
    main()