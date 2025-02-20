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

# Configure Cloudinary using credentials from config.py
cloudinary.config( 
    cloud_name = config.CLOUDINARY_CLOUD_NAME, 
    api_key = config.CLOUDINARY_API_KEY, 
    api_secret = config.CLOUDINARY_API_SECRET
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Conversation states
(SUBSCRIPTION_PHONE, MAIN_MENU, NEW_ISSUE_ORDER, NEW_ISSUE_DESCRIPTION,
 NEW_ISSUE_REASON, NEW_ISSUE_TYPE, ASK_IMAGE, WAIT_IMAGE,
 AWAITING_DA_RESPONSE, EDIT_PROMPT, EDIT_FIELD, MORE_INFO_PROMPT) = range(12)

# =============================================================================
# Helper Functions
# =============================================================================
def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Safely edits a message. If the original message is a photo (has a caption),
    it uses edit_message_caption; otherwise, it edits the text.
    """
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_issue_types_for_reason(reason):
    """Return the list of issue types for the given reason."""
    ISSUE_OPTIONS = {
        "المخزن": ["تالف", "منتهي الصلاحية", "عجز في المخزون", "تحضير خاطئ"],
        "المورد": ["خطا بالمستندات", "رصيد غير موجود", "اوردر خاطئ", "اوردر بكميه اكبر",
                   "خطا فى الباركود او اسم الصنف", "اوردر وهمى", "خطأ فى الاسعار",
                   "تخطى وقت الانتظار لدى العميل", "اختلاف بيانات الفاتورة", "توالف مصنع"],
        "العميل": ["رفض الاستلام", "مغلق", "عطل بالسيستم", "لا يوجد مساحة للتخزين", "شك عميل فى سلامة العبوه"],
        "التسليم": ["وصول متاخر", "تالف", "عطل بالسياره"]
    }
    return ISSUE_OPTIONS.get(reason, [])

# =============================================================================
# New Flow: Fetch orders from API using DA’s phone and today’s date
# =============================================================================
def fetch_orders_da(query, context) -> int:
    user = query.from_user
    logger.info("Fetching orders for DA user %s", user.id)
    sub = db.get_subscription(user.id, "DA")
    if not sub or not sub.get("phone"):
        safe_edit_message(query, "لم يتم العثور على رقم الهاتف في بيانات الاشتراك. يرجى الاشتراك مرة أخرى.")
        return MAIN_MENU
    agent_phone = sub["phone"]
    today_date = datetime.date.today().strftime("%Y-%m-%d")
    url = f"https://3e5440qr0c.execute-api.eu-west-3.amazonaws.com/dev/locus_info?agent_phone={agent_phone}&order_date=2024-11-05"
    logger.debug("Calling API URL: %s", url)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        orders = data.get("data", [])
        if not orders:
            safe_edit_message(query, "لا توجد طلبات متاحة لهذا اليوم.")
            return MAIN_MENU
        keyboard = []
        for order in orders:
            order_id = order.get("order_id")
            client_name = order.get("client_name")
            if order_id and client_name:
                button_text = f"طلب {order_id} - {client_name}"
                callback_data = f"select_order|{order_id}|{client_name}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر الطلب الذي تريد رفع مشكلة عنه:", reply_markup=reply_markup)
        return NEW_ISSUE_ORDER
    except Exception as e:
        logger.error("Error fetching orders: %s", e)
        safe_edit_message(query, "حدث خطأ أثناء جلب الطلبات. حاول مرة أخرى لاحقاً.")
        return MAIN_MENU

# =============================================================================
# DA Bot Handlers: Subscription & New Issue Submission Flow
# =============================================================================
def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    logger.info("Received /start from user %s", user.id)
    sub = db.get_subscription(user.id, "DA")
    if not sub:
        update.message.reply_text("أهلاً! يرجى إدخال رقم هاتفك للاشتراك (DA):")
        return SUBSCRIPTION_PHONE
    else:
        keyboard = [
            [
                InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
                InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")
            ]
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
        [
            InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
            InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("تم الاشتراك بنجاح كـ DA!", reply_markup=reply_markup)
    return MAIN_MENU

def da_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("da_main_menu_callback: Received data: %s", data)
    
    if data == "menu_add_issue":
        return fetch_orders_da(query, context)
    elif data == "menu_query_issue":
    # Get the DA’s tickets.
        user = query.from_user
        tickets = db.get_tickets_by_user(user.id)
        
        status_mapping = {
            "Opened": "مفتوحة",
            "Pending DA Action": "في انتظار إجراء الوكيل",
            "Awaiting Client Response": "في انتظار رد العميل",
            "Client Responded": "تم رد العميل",
            "Client Ignored": "تم تجاهل العميل",
            "Closed": "مغلقة",
            "Additional Info Provided": "تم توفير معلومات إضافية",
            "Pending DA Response": "في انتظار رد الوكيل"
        }
        
        if tickets:
            for ticket in tickets:
                status_ar = status_mapping.get(ticket['status'], ticket['status'])
                resolution = ""
                if ticket['status'] == "Closed":
                    resolution = "\nالحل: تم الحل."
                separator = "\n----------------------------------------\n"
                text = (
                    f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                    f"رقم الطلب: {ticket['order_id']}\n"
                    f"الوصف: {ticket['issue_description']}\n"
                    f"سبب المشكلة: {ticket['issue_reason']}\n"
                    f"نوع المشكلة: {ticket['issue_type']}\n"
                    f"الحالة: {status_ar}{resolution}"
                    f"{separator}"
                )
                if ticket.get("image_url"):
                    # Send photo with caption containing the ticket details.
                    query.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=ticket["image_url"],
                        caption=text,
                        parse_mode="HTML"
                    )
                else:
                    # Send plain text message.
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
        safe_edit_message(query, text=f"تم اختيار الطلب رقم {order_id} للعميل {client_name}.\nالآن، صف المشكلة التي تواجهها:")
        return NEW_ISSUE_DESCRIPTION
    elif data in ["attach_yes", "attach_no"]:
        if data == "attach_yes":
            safe_edit_message(query, text="يرجى إرسال الصورة:")
            return WAIT_IMAGE
        else:
            return show_ticket_summary_for_edit(query, context)
    else:
        safe_edit_message(query, text="الخيار غير معروف.")
        return MAIN_MENU

def new_issue_description(update: Update, context: CallbackContext) -> int:
    description = update.message.text.strip()
    context.user_data['description'] = description
    keyboard = [
        [InlineKeyboardButton("المخزن", callback_data="issue_reason_المخزن"),
         InlineKeyboardButton("المورد", callback_data="issue_reason_المورد")],
        [InlineKeyboardButton("العميل", callback_data="issue_reason_العميل"),
         InlineKeyboardButton("التسليم", callback_data="issue_reason_التسليم")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("اختر سبب المشكلة:", reply_markup=reply_markup)
    return NEW_ISSUE_REASON

def new_issue_reason_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    reason = query.data.split("_", 2)[2]
    context.user_data['issue_reason'] = reason
    types = get_issue_types_for_reason(reason)
    keyboard = [[InlineKeyboardButton(t, callback_data="issue_type_" + t)] for t in types]
    reply_markup = InlineKeyboardMarkup(keyboard)
    safe_edit_message(query, text="اختر نوع المشكلة:", reply_markup=reply_markup)
    return NEW_ISSUE_TYPE

def new_issue_type_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    issue_type = urllib.parse.unquote(query.data.split("_", 2)[2])
    context.user_data['issue_type'] = issue_type
    keyboard = [
        [InlineKeyboardButton("نعم", callback_data="attach_yes"),
         InlineKeyboardButton("لا", callback_data="attach_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    safe_edit_message(query, text="هل تريد إرفاق صورة للمشكلة؟", reply_markup=reply_markup)
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

def show_ticket_summary_for_edit(source, context: CallbackContext) -> int:
    if hasattr(source, 'edit_message_text'):
        msg_func = source.edit_message_text
        kwargs = {}
    else:
        msg_func = context.bot.send_message
        kwargs = {'chat_id': source.chat.id}
    data = context.user_data
    summary = (f"رقم الطلب: {data.get('order_id','')}\n"
               f"الوصف: {data.get('description','')}\n"
               f"سبب المشكلة: {data.get('issue_reason','')}\n"
               f"نوع المشكلة: {data.get('issue_type','')}\n"
               f"العميل: {data.get('client','')}\n"
               f"الصورة: {data.get('image', 'لا توجد')}")
    text = "ملخص التذكرة المدخلة:\n" + summary + "\nهل تريد تعديل التذكرة قبل الإرسال؟"
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("نعم", callback_data="edit_ticket_yes"),
         InlineKeyboardButton("لا", callback_data="edit_ticket_no")]
    ])
    msg_func(text=text, reply_markup=reply_markup, **kwargs)
    return EDIT_PROMPT

# ==============================
# Added Global Handler for "sendclient" Callback
# ==============================
def sendclient_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data.split("|")
    if len(data) < 2:
        safe_edit_message(query, "بيانات غير صحيحة.")
        return ConversationHandler.END
    try:
        ticket_id = int(data[1])
    except ValueError:
        safe_edit_message(query, "رقم التذكرة غير صحيح.")
        return ConversationHandler.END
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        safe_edit_message(query, "التذكرة غير موجودة.")
        return ConversationHandler.END
    # Use the notify_client function from notifier to send the ticket to the client.
    notifier.notify_client(ticket)
    safe_edit_message(query, text=f"تم إرسال التذكرة #{ticket_id} إلى العميل.")
    return ConversationHandler.END

# ==============================
# Minimal Stub for missing edit_ticket_prompt_callback
# ==============================
def edit_ticket_prompt_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "edit_ticket_no":
        return finalize_ticket_da(query, context, image_url=context.user_data.get('image', None))
    elif data == "edit_ticket_yes":
        safe_edit_message(query, text="تعديل التذكرة غير مدعوم حالياً. الرجاء إنشاء التذكرة بدون تعديل.", reply_markup=None)
        return MAIN_MENU
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def edit_field_callback(update: Update, context: CallbackContext) -> int:
    update.callback_query.answer("تعديل الحقل غير مدعوم حالياً.")
    return MAIN_MENU

def edit_field_input_handler(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("تعديل الحقل غير مدعوم حالياً.")
    return MAIN_MENU

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
    ticket_id = db.add_ticket(order_id, description, issue_reason, issue_type, client_selected, image_url, "Opened", user.id)
    if hasattr(source, 'edit_message_text'):
        source.edit_message_text(f"تم إنشاء التذكرة برقم {ticket_id}.\nالحالة: Opened")
    else:
        context.bot.send_message(chat_id=user.id, text=f"تم إنشاء التذكرة برقم {ticket_id}.\nالحالة: Opened")
    if 'edit_log' in context.user_data:
        for log_entry in context.user_data['edit_log']:
            db.update_ticket_status(ticket_id, "Opened", log_entry)
    ticket = db.get_ticket(ticket_id)
    notifier.notify_supervisors(ticket)
    context.user_data.clear()
    return MAIN_MENU
def da_moreinfo_callback_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    try:
        ticket_id = int(query.data.split("|")[1])
    except (IndexError, ValueError):
        safe_edit_message(query, text="خطأ في بيانات التذكرة.")
        return MAIN_MENU
    context.user_data['ticket_id'] = ticket_id
    # Send prompt as reply so that it's linked to this conversation
    query.message.reply_text(
        f"يرجى إدخال المعلومات الإضافية المطلوبة للتذكرة #{ticket_id}:",
        reply_markup=ForceReply(selective=True)
    )
    # Return a state (even if it later drops out)
    return AWAITING_DA_RESPONSE
def da_awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    logger.debug("In da_awaiting_response_handler, context.user_data: %s", context.user_data)
    additional_info = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    if not ticket_id:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    db.update_ticket_status(ticket_id, "Additional Info Provided", 
                              {"action": "da_moreinfo", "message": additional_info})
    notifier.notify_supervisors_da_moreinfo(ticket_id, additional_info)
    update.message.reply_text("تم إرسال المعلومات الإضافية. شكراً لك.")
    context.user_data.pop('ticket_id', None)
    return MAIN_MENU
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

def prompt_da_for_more_info(ticket_id: int, chat_id: int, context: CallbackContext):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        logger.error("prompt_da_for_more_info: Ticket %s not found", ticket_id)
        context.bot.send_message(chat_id=chat_id, text="خطأ: التذكرة غير موجودة.")
        return
    text = (
        f"<b>التذكرة #{ticket_id}</b>\n"
        f"رقم الطلب: {ticket['order_id']}\n"
        f"الوصف: {ticket['issue_description']}\n"
        f"الحالة: {ticket['status']}\n\n"
        "يرجى إدخال المعلومات الإضافية المطلوبة للتذكرة:"
    )
    context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=ForceReply(selective=True))

def default_handler_da(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [
            InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
            InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU
def global_additional_info_handler(update: Update, context: CallbackContext) -> int:
    # If there's a pending ticket awaiting additional info, process it.
    if context.user_data.get('ticket_id'):
        return da_awaiting_response_handler(update, context)
    # Otherwise, do nothing (or route to main menu)
    return MAIN_MENU
# =============================================================================
# Main function to start the DA bot
# =============================================================================
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
                SUBSCRIPTION_PHONE: [MessageHandler(Filters.text & ~Filters.command, subscription_phone)],
                MAIN_MENU: [
                    CallbackQueryHandler(sendclient_callback, pattern="^sendclient\\|"),
                    CallbackQueryHandler(da_main_menu_callback, pattern="^(menu_add_issue|menu_query_issue|select_order\\|.*|attach_yes|attach_no)$"),
                    MessageHandler(Filters.text & ~Filters.command, default_handler_da)
                ],
                NEW_ISSUE_ORDER: [CallbackQueryHandler(da_main_menu_callback, pattern="^select_order\\|")],
                NEW_ISSUE_DESCRIPTION: [MessageHandler(Filters.text & ~Filters.command, new_issue_description)],
                NEW_ISSUE_REASON: [CallbackQueryHandler(new_issue_reason_callback, pattern="^issue_reason_.*")],
                NEW_ISSUE_TYPE: [CallbackQueryHandler(new_issue_type_callback, pattern="^issue_type_.*")],
                ASK_IMAGE: [CallbackQueryHandler(da_main_menu_callback, pattern="^(attach_yes|attach_no)$")],
                WAIT_IMAGE: [MessageHandler(Filters.photo, wait_image),
                            MessageHandler(Filters.text, wait_image)],
                EDIT_PROMPT: [CallbackQueryHandler(edit_ticket_prompt_callback, pattern="^(edit_ticket_yes|edit_ticket_no)$")],
                EDIT_FIELD: [
                    CallbackQueryHandler(edit_field_callback, pattern="^edit_field_.*"),
                    MessageHandler(Filters.text & ~Filters.command, edit_field_input_handler)
                ],
                MORE_INFO_PROMPT: [MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)],
                AWAITING_DA_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)]
            },
            fallbacks=[
                CommandHandler('cancel', lambda update, context: update.message.reply_text("تم إلغاء العملية.")),
                CommandHandler('start', lambda update, context: start(update, context))
            ],
            allow_reentry=True  # <-- Add this parameter
        )
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, global_additional_info_handler))
        dp.add_handler(conv_handler)
        # Global handler for sendclient callback
        dp.add_handler(CallbackQueryHandler(sendclient_callback, pattern="^sendclient\\|"))
        dp.add_handler(CallbackQueryHandler(da_callback_handler, pattern="^(close\\||da_moreinfo\\|).*"))
        updater.start_polling()
        logger.info("DA bot started successfully.")
        updater.idle()
    except Exception as e:
        logger.error("Failed to start DA bot: %s", e)

if __name__ == '__main__':
    main()