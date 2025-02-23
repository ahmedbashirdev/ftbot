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

# Conversation states for flows not handled by our global additional‑info handler
(SUBSCRIPTION_PHONE, MAIN_MENU, NEW_ISSUE_ORDER, NEW_ISSUE_DESCRIPTION,
 NEW_ISSUE_REASON, NEW_ISSUE_TYPE, ASK_IMAGE, WAIT_IMAGE,
 AWAITING_DA_RESPONSE, EDIT_PROMPT, EDIT_FIELD) = range(11)

# We'll remove the dedicated MORE_INFO_PROMPT state from the ConversationHandler.
# Instead, after a callback for "da_moreinfo|ticket_id" is received,
# we store context.user_data and send a ForceReply.
# Then a global MessageHandler will check for context.user_data['action'] == 'moreinfo'.

def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    if hasattr(query.message, "caption") and query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

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
                separator = "\n-----------------------------"
                text = (
                    f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                    f"<b>رقم الطلب:</b> {ticket['order_id']}\n"
                    f"<b>الوصف:</b> {ticket['issue_description']}\n"
                    f"<b>الحالة:</b> {ticket['status']}{resolution}\n"
                    f"{separator}"
                )

                if ticket.get('image_url'):
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=ticket['image_url'],
                        caption=text,
                        parse_mode="HTML"
                    )
                else:
                    context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text,
                        parse_mode="HTML"
                    )
        else:
            context.bot.send_message(chat_id=update.effective_chat.id, text="لا توجد تذاكر.")
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

def fetch_orders_da(query, context) -> int:
    user = query.from_user
    logger.info("Fetching orders for DA user %s", user.id)
    sub = db.get_subscription(user.id, "DA")
    if not sub or not sub.get("phone"):
        safe_edit_message(query, "لم يتم العثور على رقم الهاتف في بيانات الاشتراك. يرجى الاشتراك مرة أخرى.")
        return MAIN_MENU
    agent_phone = sub["phone"]
    # For now, keep the fixed date as per your testing.
    url = f"https://3e5440qr0c.execute-api.eu-west-3.amazonaws.com/dev/locus_info?agent_phone={agent_phone}&order_date=2024-08-18"
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
    types = {
        "المخزن": ["تالف", "منتهي الصلاحية", "عجز في المخزون", "تحضير خاطئ"],
        "المورد": ["خطا بالمستندات", "رصيد غير موجود", "اوردر خاطئ", "اوردر بكميه اكبر",
                   "خطا فى الباركود او اسم الصنف", "اوردر وهمى", "خطأ فى الاسعار",
                   "تخطى وقت الانتظار لدى العميل", "اختلاف بيانات الفاتورة", "توالف مصنع"],
        "العميل": ["رفض الاستلام", "مغلق", "عطل بالسيستم", "لا يوجد مساحة للتخزين", "شك عميل فى سلامة العبوه"],
        "التسليم": ["وصول متاخر", "تالف", "عطل بالسياره"]
    }.get(reason, [])
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

# --- Editing Handlers ---
def edit_ticket_prompt_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data.strip().lower()
    logger.debug("edit_ticket_prompt_callback: received callback data: %s", data)
    if data == "edit_ticket_no":
        return finalize_ticket_da(query, context, image_url=context.user_data.get('image', None))
    elif data == "edit_ticket_yes":
        keyboard = [
            [InlineKeyboardButton("رقم الطلب", callback_data="edit_field_order"),
             InlineKeyboardButton("الوصف", callback_data="edit_field_description")],
            [InlineKeyboardButton("سبب المشكلة", callback_data="edit_field_issue_reason"),
             InlineKeyboardButton("نوع المشكلة", callback_data="edit_field_issue_type")],
            [InlineKeyboardButton("العميل", callback_data="edit_field_client"),
             InlineKeyboardButton("الصورة", callback_data="edit_field_image")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر الحقل الذي تريد تعديله:", reply_markup=reply_markup)
        return EDIT_FIELD
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def edit_field_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    field = data[len("edit_field_"):]
    context.user_data['edit_field'] = field
    safe_edit_message(query, text=f"أدخل القيمة الجديدة لـ {field}:")
    return EDIT_FIELD

def edit_field_input_handler(update: Update, context: CallbackContext) -> int:
    new_value = update.message.text.strip()
    field = context.user_data.get('edit_field')
    if not field:
        update.message.reply_text("حدث خطأ، لم يتم تحديد الحقل المراد تعديله.")
        return EDIT_PROMPT
    mapping = {
       "order": "order_id",
       "description": "description",
       "issue_reason": "issue_reason",
       "issue_type": "issue_type",
       "client": "client",
       "image": "image"
    }
    key = mapping.get(field)
    if key:
       context.user_data[key] = new_value
       update.message.reply_text(f"تم تحديث {field} بنجاح.")
    else:
       update.message.reply_text("الحقل غير معروف.")
    return show_ticket_summary_for_edit(update.message, context)

# --- Additional Info Flow ---
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
    # Return a special state value that we will capture via a global text handler.
    # We won't rely on ConversationHandler state here.
    context.user_data['action'] = 'moreinfo'
    return MAIN_MENU  # Note: The conversation state remains MAIN_MENU so that a global text handler can catch the reply.

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

def da_awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    logger.debug("Entering da_awaiting_response_handler with user_data: %s", context.user_data)
    additional_info = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    if ticket_id is None:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    if not additional_info:
        update.message.reply_text("الرجاء إدخال معلومات إضافية.")
        return MAIN_MENU
    success = db.update_ticket_status(ticket_id, "Awaiting Supervisor Decision", 
                                      {"action": "da_moreinfo", "message": additional_info})
    if not success:
        update.message.reply_text("حدث خطأ أثناء تحديث التذكرة.")
        return MAIN_MENU
    ticket = db.get_ticket(ticket_id)
    logger.debug("Updated ticket data: %s", ticket)
    logger.debug("Ticket %s updated with additional info: %s", ticket_id, additional_info)
    try:
        notifier.notify_supervisors_da_moreinfo(ticket_id, additional_info)
        update.message.reply_text("تم إرسال المعلومات الإضافية إلى المشرف. شكراً لك.")
    except Exception as e:
        logger.error("Error notifying supervisors: %s", e)
        update.message.reply_text("حدث خطأ أثناء إرسال المعلومات إلى المشرف.")
    context.user_data.pop('ticket_id', None)
    context.user_data.pop('action', None)
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

def default_handler_da(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU

# --- Global Text Handler for Additional Info Replies ---
def global_da_text_handler(update: Update, context: CallbackContext) -> None:
    """
    This global handler checks if context.user_data has an action 'moreinfo' and a ticket_id.
    If so, it routes the message to da_awaiting_response_handler.
    Otherwise, it calls default_handler_da.
    """
    if context.user_data.get('action') == 'moreinfo' and context.user_data.get('ticket_id'):
        
        da_awaiting_response_handler(update, context)
    else:
        default_handler_da(update, context)

# =============================================================================
# Finalize Ticket Flow
# =============================================================================
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
    ticket = db.get_ticket(ticket_id)
    notifier.notify_supervisors(ticket)
    context.user_data.clear()
    return MAIN_MENU

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
                AWAITING_DA_RESPONSE: [MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)]
            },
            fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("تم إلغاء العملية.")),
                       CommandHandler('start', lambda u, c: start(u, c))],
            allow_reentry=True
        )
        dp.add_handler(conv_handler)
        # Global text handler for additional info replies:
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, global_da_text_handler))
        dp.add_handler(CallbackQueryHandler(da_callback_handler, pattern="^(close\\||da_moreinfo\\|).*"))
        updater.start_polling()
        logger.info("DA bot started successfully.")
        updater.idle()
    except Exception as e:
        logger.error("Failed to start DA bot: %s", e)

if __name__ == '__main__':
    main()