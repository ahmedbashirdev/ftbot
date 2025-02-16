#!/usr/bin/env python3
# da_bot.py

import logging
import datetime
import unicodedata
import time
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
from db import get_db_session,Ticket
# Configure Cloudinary using credentials from config.py
cloudinary.config( 
    cloud_name = config.CLOUDINARY_CLOUD_NAME, 
    api_key = config.CLOUDINARY_API_KEY, 
    api_secret = config.CLOUDINARY_API_SECRET
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)
logger = logging.getLogger(__name__)

# =============================================================================
# Conversation states
# 
# Note: We have removed the old NEW_ISSUE_CLIENT state.
# Now the order (and its associated client) is selected automatically
# via the API and shown in state NEW_ISSUE_ORDER.
# =============================================================================
# Conversation states
(SUBSCRIPTION_PHONE, MAIN_MENU, NEW_ISSUE_ORDER, NEW_ISSUE_DESCRIPTION,
 NEW_ISSUE_REASON, NEW_ISSUE_TYPE, ASK_IMAGE, WAIT_IMAGE,
 AWAITING_DA_RESPONSE, EDIT_PROMPT, EDIT_FIELD, MORE_INFO_PROMPT) = range(12)

# =============================================================================
# Local mapping for issue reasons to types
# =============================================================================
ISSUE_OPTIONS = {
    "المخزن": ["تالف", "منتهي الصلاحية", "عجز في المخزون", "تحضير خاطئ"],
    "المورد": ["خطا بالمستندات", "رصيد غير موجود", "اوردر خاطئ", "اوردر بكميه اكبر",
               "خطا فى الباركود او اسم الصنف", "اوردر وهمى", "خطأ فى الاسعار",
               "تخطى وقت الانتظار لدى العميل", "اختلاف بيانات الفاتورة", "توالف مصنع"],
    "العميل": ["رفض الاستلام", "مغلق", "عطل بالسيستم", "لا يوجد مساحة للتخزين", "شك عميل فى سلامة العبوه"],
    "التسليم": ["وصول متاخر", "تالف", "عطل بالسياره"]
}

def get_issue_types_for_reason(reason):
    """Return the list of issue types for the given reason."""
    return ISSUE_OPTIONS.get(reason, [])

# =============================================================================
# Helper: safe_edit_message
# =============================================================================
def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Safely edits a message. If the original message is a photo (has a caption),
    edit its caption; otherwise, edit its text.
    """
    if query.message.caption:
        return query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        return query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def start(update: Update, context: CallbackContext) -> int:
    """Start the conversation and check if user is subscribed."""
    logger.debug("Start command received")
    
    try:
        user = update.effective_user
        logger.debug(f"User {user.id} ({user.first_name}) started the bot")
        
        # Check if user is already subscribed
        sub = db.get_subscription(user.id, "DA")
        
        if not sub:
            logger.debug(f"No subscription found for user {user.id}")
            update.message.reply_text(
                "أهلاً! يرجى إدخال رقم هاتفك للاشتراك (DA):"
            )
            return SUBSCRIPTION_PHONE
            
        logger.debug(f"Found subscription for user {user.id}")
        keyboard = [
            [
                InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
                InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            f"مرحباً {user.first_name}",
            reply_markup=reply_markup
        )
        return MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        update.message.reply_text(
            "عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى لاحقاً."
        )
        return ConversationHandler.END

def subscription_phone(update: Update, context: CallbackContext) -> int:
    logger.debug("Subscription phone handler called")
    phone = update.message.text.strip()
    user = update.effective_user
    
    try:
        db.add_subscription(user.id, phone, 'DA', "DA", None,
                           user.username, user.first_name, user.last_name, update.effective_chat.id)
        logger.debug(f"Added subscription for user {user.id} with phone {phone}")
        
        keyboard = [
            [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
             InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("تم الاشتراك بنجاح كـ DA!", reply_markup=reply_markup)
        return MAIN_MENU
    except Exception as e:
        logger.error(f"Error in subscription_phone: {e}")
        update.message.reply_text("حدث خطأ أثناء التسجيل. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

def fetch_orders(query, context):
    """Fetch orders for the DA from the API"""
    try:
        # Show loading message first
        safe_edit_message(query, text="جاري تحميل الطلبات...")
        
        # Get user from the callback query
        user = query.from_user
        sub = db.get_subscription(user.id, "DA")
        
        if not sub or not sub['phone']:
            safe_edit_message(query, text="لم يتم العثور على بيانات الاشتراك أو رقم الهاتف.")
            return MAIN_MENU
            
        agent_phone = sub['phone']
        url = f"https://3e5440qr0c.execute-api.eu-west-3.amazonaws.com/dev/locus_info?agent_phone=01066440390&order_date=2024-11-05"
        
        logger.debug(f"Making API request to: {url}")
        response = requests.get(url)
        response.raise_for_status()
        
        orders_data = response.json()
        logger.debug(f"API Response: {orders_data}")
        
        if not orders_data or 'data' not in orders_data or not orders_data['data']:
            safe_edit_message(query, text="لا توجد طلبات متاحة لهذا اليوم.")
            return MAIN_MENU

        # Build keyboard from API data
        keyboard = []
        for order in orders_data['data']:
            if isinstance(order, dict):
                order_id = str(order.get('order_id', ''))
                client_name = str(order.get('client_name', ''))
                if order_id and client_name:
                    button = [InlineKeyboardButton(
                        f"طلب {order_id} - {client_name}",
                        callback_data=f"select_order|{order_id}|{client_name}"
                    )]
                    keyboard.append(button)

        if keyboard:
            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(
                query,
                text="اختر الطلب الذي تريد رفع مشكلة عنه:",
                reply_markup=reply_markup
            )
            return NEW_ISSUE_ORDER
        else:
            safe_edit_message(query, text="لا توجد طلبات متاحة.")
            return MAIN_MENU

    except requests.RequestException as e:
        logger.error(f"API request error in fetch_orders: {e}", exc_info=True)
        safe_edit_message(query, text="حدث خطأ أثناء الاتصال بالخادم. الرجاء المحاولة مرة أخرى.")
        return MAIN_MENU
    except Exception as e:
        logger.error(f"Error in fetch_orders: {e}", exc_info=True)
        safe_edit_message(query, text="حدث خطأ أثناء جلب الطلبات. الرجاء المحاولة مرة أخرى.")
        return MAIN_MENU

def send_full_issue_details_to_client(query, ticket_id):
    """Send complete issue details to the client."""
    try:
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            safe_edit_message(query, text="لم يتم العثور على التذكرة.")
            return

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

        status_ar = status_mapping.get(ticket['status'], ticket['status'])
        text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                f"رقم الطلب: {ticket['order_id']}\n"
                f"الوصف: {ticket['issue_description']}\n"
                f"سبب المشكلة: {ticket['issue_reason']}\n"
                f"نوع المشكلة: {ticket['issue_type']}\n"
                f"العميل: {ticket['client']}\n"
                f"الحالة: {status_ar}")

        if ticket['image_url']:
            # If there's an image, send it with the caption
            query.message.reply_photo(
                photo=ticket['image_url'],
                caption=text,
                parse_mode="HTML"
            )
        else:
            # If no image, just send the text
            query.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error sending issue details: {e}")
        safe_edit_message(query, text="حدث خطأ أثناء إرسال تفاصيل المشكلة.")

def da_main_menu_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug(f"da_main_menu_callback: Received callback data: {data}")  # ✅ Debug log
    logger.debug(f"Raw callback query: {query}")  # ✅ Debug log
    
    if data == "menu_add_issue":
        # Start the fetch_orders flow when adding a new issue
        return fetch_orders(query, context)
    elif data == "menu_query_issue":
        # Get user's tickets
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
                text = (f"<b>تذكرة #{ticket['ticket_id']}</b>\n"
                       f"رقم الطلب: {ticket['order_id']}\n"
                       f"الوصف: {ticket['issue_description']}\n"
                       f"سبب المشكلة: {ticket['issue_reason']}\n"
                       f"نوع المشكلة: {ticket['issue_type']}\n"
                       f"الحالة: {status_ar}{resolution}")
                query.message.reply_text(text, parse_mode="HTML")
        else:
            safe_edit_message(query, text="لا توجد تذاكر.")
        return MAIN_MENU
        
    # Handle order selection
    elif data.startswith("select_order|"):
        parts = data.split("|")
        if len(parts) < 3:
            safe_edit_message(query, text="بيانات الطلب غير صحيحة.")
            return MAIN_MENU
        order_id = parts[1]
        client_name = parts[2]
        context.user_data['order_id'] = order_id
        context.user_data['client'] = client_name
        safe_edit_message(query, text=f"تم اختيار الطلب رقم {order_id} للعميل {client_name}.\nالآن، صف المشكلة التي تواجهها:")
        return NEW_ISSUE_DESCRIPTION
        
    # Handle issue reason selection
    elif data.startswith("issue_reason_"):
        reason = data.split("_", 2)[2]
        context.user_data['issue_reason'] = reason
        types = get_issue_types_for_reason(reason)
        keyboard = [[InlineKeyboardButton(t, callback_data="issue_type_" + t)] for t in types]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="اختر نوع المشكلة:", reply_markup=reply_markup)
        return NEW_ISSUE_TYPE
        
    # Handle issue type selection
    elif data.startswith("issue_type_"):
        issue_type = urllib.parse.unquote(data.split("_", 2)[2])
        context.user_data['issue_type'] = issue_type
        keyboard = [
            [InlineKeyboardButton("نعم", callback_data="attach_yes"),
             InlineKeyboardButton("لا", callback_data="attach_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="هل تريد إرفاق صورة للمشكلة؟", reply_markup=reply_markup)
        return ASK_IMAGE
        
    # Handle image attachment choice
    elif data in ["attach_yes", "attach_no"]:
        if data == "attach_yes":
            safe_edit_message(query, text="يرجى إرسال الصورة:")
            return WAIT_IMAGE
        else:
            return show_ticket_summary_for_edit(query, context)
            
    # Handle additional info request
    elif data.startswith("da_moreinfo|"):
        return da_moreinfo_callback_handler(update, context)
        
    # Handle ticket editing
    elif data.startswith("edit_ticket_") or data.startswith("edit_field_"):
        return edit_ticket_prompt_callback(update, context)
        
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


def wait_image(update: Update, context: CallbackContext) -> int:
    if update.message.photo:
        try:
            photo = update.message.photo[-1]
            file = photo.get_file()
            bio = BytesIO()
            file.download(out=bio)
            bio.seek(0)

            # ✅ Add Retry for Cloudinary Upload
            retry_count = 3
            for attempt in range(retry_count):
                try:
                    result = cloudinary.uploader.upload(bio)
                    secure_url = result.get('secure_url')
                    if secure_url:
                        context.user_data['image'] = secure_url
                        return show_ticket_summary_for_edit(update.message, context)
                except Exception as e:
                    logger.error(f"Cloudinary upload failed (Attempt {attempt + 1}/{retry_count}): {e}")
                    time.sleep(2)  # Wait before retrying

            update.message.reply_text("⚠️ فشل رفع الصورة بعد عدة محاولات. حاول مرة أخرى.")
            return WAIT_IMAGE
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
            update.message.reply_text("⚠️ حدث خطأ أثناء رفع الصورة. حاول مرة أخرى.")
            return WAIT_IMAGE
    else:
        update.message.reply_text("⚠️ لم يتم إرسال صورة صحيحة. أعد الإرسال:")
        return WAIT_IMAGE
    
def show_ticket_summary_for_edit(source, context: CallbackContext):
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

def edit_ticket_prompt_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
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
        keyboard = [
            [InlineKeyboardButton("نعم", callback_data="edit_ticket_yes"),
             InlineKeyboardButton("لا", callback_data="edit_ticket_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        safe_edit_message(query, text="هل تريد تعديل التذكرة قبل الإرسال؟", reply_markup=reply_markup)
        return EDIT_PROMPT

def edit_field_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    field = query.data
    if field == "edit_field_issue_reason":
        options = ["المخزن", "المورد", "العميل", "التسليم"]
        mapping = {}
        keyboard_buttons = []
        for i, option in enumerate(options):
            key = str(i)
            mapping[key] = option
            keyboard_buttons.append([InlineKeyboardButton(option, callback_data="edit_field_issue_reason_idx_" + key)])
        context.user_data['edit_reason_map'] = mapping
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        safe_edit_message(query, text="اختر سبب المشكلة الجديد:", reply_markup=reply_markup)
        return EDIT_FIELD
    if field.startswith("edit_field_issue_reason_idx_"):
        idx = field[len("edit_field_issue_reason_idx_"):]
        mapping = context.user_data.get('edit_reason_map', {})
        new_reason = mapping.get(idx)
        if not new_reason:
            safe_edit_message(query, text="خطأ في اختيار سبب المشكلة.")
            return EDIT_PROMPT
        context.user_data['issue_reason'] = new_reason
        log_entry = {"action": "edit_field", "field": "سبب المشكلة", "new_value": new_reason}
        context.user_data.setdefault('edit_log', []).append(log_entry)
        types = get_issue_types_for_reason(new_reason)
        if types:
            mapping2 = {}
            keyboard_buttons = []
            for i, opt in enumerate(types):
                key = str(i)
                mapping2[key] = opt
                keyboard_buttons.append([InlineKeyboardButton(opt, callback_data="edit_field_issue_type_idx_" + key)])
            context.user_data['edit_type_map'] = mapping2
            reply_markup = InlineKeyboardMarkup(keyboard_buttons)
            safe_edit_message(query, text=f"تم تعديل سبب المشكلة إلى: {new_reason}\nالآن اختر نوع المشكلة:", reply_markup=reply_markup)
            return EDIT_FIELD
        else:
            safe_edit_message(query, text=f"تم تعديل سبب المشكلة إلى: {new_reason}\nولا توجد خيارات متاحة لنوع المشكلة لهذا السبب.")
            return EDIT_PROMPT
    if field == "edit_field_issue_type":
        current_reason = context.user_data.get('issue_reason', '')
        types = get_issue_types_for_reason(current_reason)
        if not types:
            safe_edit_message(query, text="لا توجد خيارات متاحة لنوع المشكلة.")
            return EDIT_PROMPT
        mapping = {}
        keyboard_buttons = []
        for i, option in enumerate(types):
            key = str(i)
            mapping[key] = option
            keyboard_buttons.append([InlineKeyboardButton(option, callback_data="edit_field_issue_type_idx_" + key)])
        context.user_data['edit_type_map'] = mapping
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        safe_edit_message(query, text="اختر نوع المشكلة الجديد:", reply_markup=reply_markup)
        return EDIT_FIELD
    if field in ["edit_field_order", "edit_field_description", "edit_field_image", "edit_field_client"]:
        context.user_data['edit_field'] = field
        if field == "edit_field_client":
            keyboard = [
                [InlineKeyboardButton("بوبا", callback_data="edit_field_client_بوبا"),
                 InlineKeyboardButton("بتلكو", callback_data="edit_field_client_بتلكو"),
                 InlineKeyboardButton("بيبس", callback_data="edit_field_client_بيبس")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            safe_edit_message(query, text="اختر العميل الجديد:", reply_markup=reply_markup)
            return EDIT_FIELD
        else:
            field_name = field.split('_')[-1]
            safe_edit_message(query, text=f"أدخل القيمة الجديدة لـ {field_name}:")
            return EDIT_FIELD
    if field.startswith("edit_field_issue_type_idx_"):
        idx = field[len("edit_field_issue_type_idx_"):]
        mapping = context.user_data.get('edit_type_map', {})
        new_type = mapping.get(idx)
        if not new_type:
            safe_edit_message(query, text="خطأ في اختيار نوع المشكلة.")
            return EDIT_PROMPT
        context.user_data['issue_type'] = new_type
        log_entry = {"action": "edit_field", "field": "نوع المشكلة", "new_value": new_type}
        context.user_data.setdefault('edit_log', []).append(log_entry)
        safe_edit_message(query, text=f"تم تعديل نوع المشكلة إلى: {new_type}")
        keyboard = [
            [InlineKeyboardButton("نعم", callback_data="edit_ticket_yes"),
             InlineKeyboardButton("لا", callback_data="edit_ticket_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="هل تريد تعديل التذكرة مرة أخرى؟",
                                 reply_markup=reply_markup)
        return EDIT_PROMPT
    if field.startswith("edit_field_client_"):
        new_client = field[len("edit_field_client_"):].strip()
        context.user_data['client'] = new_client
        log_entry = {"action": "edit_field", "field": "العميل", "new_value": new_client}
        context.user_data.setdefault('edit_log', []).append(log_entry)
        safe_edit_message(query, text=f"تم تعديل العميل إلى: {new_client}")
        keyboard = [
            [InlineKeyboardButton("نعم", callback_data="edit_ticket_yes"),
             InlineKeyboardButton("لا", callback_data="edit_ticket_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(chat_id=query.message.chat.id,
                                 text="هل تريد تعديل التذكرة مرة أخرى؟",
                                 reply_markup=reply_markup)
        return EDIT_PROMPT
    field_name = field.split('_')[-1]
    context.user_data['edit_field'] = field
    safe_edit_message(query, text=f"أدخل القيمة الجديدة لـ {field_name}:")
    return EDIT_FIELD

def edit_field_input_handler(update: Update, context: CallbackContext) -> int:
    if 'edit_field' in context.user_data:
        field = context.user_data['edit_field']
        new_value = update.message.text.strip()
        if field == "edit_field_order":
            context.user_data['order_id'] = new_value
        elif field == "edit_field_description":
            context.user_data['description'] = new_value
        elif field == "edit_field_image":
            context.user_data['image'] = new_value
        elif field == "edit_field_issue_reason":
            context.user_data['issue_reason'] = new_value
        field_name = field.split('_')[-1]
        log_entry = {"action": "edit_field", "field": field_name, "new_value": new_value}
        context.user_data.setdefault('edit_log', []).append(log_entry)
        update.message.reply_text(f"تم تعديل {field_name} إلى: {new_value}")
        keyboard = [
            [InlineKeyboardButton("نعم", callback_data="edit_ticket_yes"),
            InlineKeyboardButton("لا", callback_data="edit_ticket_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("هل تريد تعديل التذكرة مرة أخرى؟", reply_markup=reply_markup)
        return EDIT_PROMPT
    else:
        update.message.reply_text("حدث خطأ أثناء التعديل.")
        return EDIT_PROMPT

def finalize_ticket_da(source, context, image_url):
    """Handles finalizing the ticket and inserting it into the database."""
    user = source.from_user if hasattr(source, 'from_user') else source.message.from_user
    data = context.user_data

    session = get_db_session()  # ✅ Ensure session is created before use

    try:
        new_ticket = db.Ticket(
            order_id=data.get('order_id'),
            issue_description=data.get('description'),  # ✅ Change to issue_description
            issue_reason=data.get('issue_reason'),
            issue_type=data.get('issue_type'),
            client=data.get('client', 'غير محدد'),
            image_url=image_url,
            status="Opened",
            da_id=user.id  # ✅ Use da_id instead of user_id

        )
        session.add(new_ticket)
        session.commit()
        ticket_id = new_ticket.ticket_id

        # Notify supervisors
        notifier.notify_supervisors(new_ticket)

        # Send confirmation message
        context.bot.send_message(chat_id=user.id, text=f"تم إنشاء التذكرة برقم {ticket_id}. الحالة: Opened")

        return MAIN_MENU

    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error finalizing ticket: {e}")
        return MAIN_MENU

    finally:
        session.close()   # ✅ Close the session# =============================================================================
# Additional Info & Close Issue Flows
# =============================================================================
def da_awaiting_response_handler(update: Update, context: CallbackContext) -> int:
    additional_info = update.message.text.strip()
    ticket_id = context.user_data.get('ticket_id')
    logger.debug("da_awaiting_response_handler: Received additional_info='%s' for ticket_id=%s", additional_info, ticket_id)
    if not ticket_id:
        update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return MAIN_MENU
    db.update_ticket_status(ticket_id, "Additional Info Provided", {"action": "da_moreinfo", "message": additional_info})
    logger.debug("da_awaiting_response_handler: Updated ticket status for ticket_id=%s", ticket_id)
    notify_supervisors_da_moreinfo(ticket_id, additional_info)
    update.message.reply_text("تم إرسال المعلومات الإضافية. شكراً لك.")

def da_callback_handler(update: Update, context: CallbackContext) -> int:  # Changed from async def and ContextTypes.DEFAULT_TYPE
    query = update.callback_query
    query.answer()
    data = query.data
    logger.debug("da_callback_handler: Received callback data: %s", data)
    if data.startswith("close|"):
        ticket_id = int(data.split("|")[1])
        db.update_ticket_status(ticket_id, "Closed", {"action": "da_closed"})
        safe_edit_message(query, text="تم إغلاق التذكرة بنجاح.")
        bot_sup = Bot(token=config.SUPERVISOR_BOT_TOKEN)
        for sup in db.get_supervisors():
            try:
                bot_sup.send_message(chat_id=sup['chat_id'],
                                     text=f"التذكرة #{ticket_id} تم إغلاقها من قبل الوكيل.",
                                     parse_mode="HTML")
            except Exception as e:
                logger.error("da_callback_handler: Error notifying supervisor of closure for ticket %s: %s", ticket_id, e)
        return MAIN_MENU
    elif data.startswith("da_moreinfo|"):
        return da_moreinfo_callback_handler(update, context)
    else:
        safe_edit_message(query, text="الإجراء غير معروف.")
        return MAIN_MENU

def da_moreinfo_callback_handler(update: Update, context: CallbackContext) -> int:  # Changed from ContextTypes.DEFAULT_TYPE
    query = update.callback_query
    query.answer()  # Fixed indentation
    data = query.data
    try:
        ticket_id = int(data.split("|")[1])
    except (IndexError, ValueError):
        safe_edit_message(query, text="خطأ في بيانات التذكرة.")
        return MAIN_MENU
    context.user_data['ticket_id'] = ticket_id
    logger.debug("da_moreinfo_callback_handler: Stored ticket_id=%s", ticket_id)
    prompt_da_for_more_info(ticket_id, query.message.chat.id, context)
    return MORE_INFO_PROMPT

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
    logger.debug("prompt_da_for_more_info: Prompting DA in chat %s for ticket %s", chat_id, ticket_id)
    context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=ForceReply(selective=True))

def notify_supervisors_da_moreinfo(ticket_id: int, additional_info: str):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        logger.error("notify_supervisors_da_moreinfo: Ticket %s not found", ticket_id)
        return
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)
    text = (f"<b>معلومات إضافية من الوكيل للتذكرة #{ticket_id}</b>\n"
            f"رقم الطلب: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"المعلومات الإضافية: {additional_info}\n"
            f"الحالة: {ticket['status']}")
    keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    logger.debug("notify_supervisors_da_moreinfo: Notifying supervisors for ticket %s", ticket_id)
    for sup in db.get_supervisors():
        try:
            bot.send_message(chat_id=sup['chat_id'], text=text, reply_markup=reply_markup, parse_mode="HTML")
            logger.debug("notify_supervisors_da_moreinfo: Notified supervisor %s", sup['chat_id'])
        except Exception as e:
            logger.error("notify_supervisors_da_moreinfo: Error notifying supervisor %s: %s", sup['chat_id'], e)

def default_handler_da(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("إضافة مشكلة", callback_data="menu_add_issue"),
         InlineKeyboardButton("استعلام عن مشكلة", callback_data="menu_query_issue")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("الرجاء اختيار خيار:", reply_markup=reply_markup)
    return MAIN_MENU
def default_handler_da_edit(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("الرجاء إدخال القيمة المطلوبة أو اختر من الخيارات المتاحة.")
    return EDIT_FIELD

# =============================================================================
# Main function
# =============================================================================
def main():
    """Start the DA bot."""
    if not config.DA_BOT_TOKEN:
        logger.error("Bot token not found!")
        return
        
    try:
        updater = Updater(config.DA_BOT_TOKEN, use_context=True)
        logger.info("Bot connected successfully")
        dp = updater.dispatcher
        dp.add_handler(CommandHandler('test', test_command))
        
        # Complete conversation handler with all states
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                SUBSCRIPTION_PHONE: [
                    MessageHandler(Filters.text & ~Filters.command, subscription_phone)
                ],
                MAIN_MENU: [
                    CallbackQueryHandler(da_main_menu_callback, pattern='^(menu_|select_order|issue_reason_|issue_type_|attach_|da_moreinfo|edit_ticket_|edit_field_)'),
                    MessageHandler(Filters.text & ~Filters.command, default_handler_da)
                ],
                NEW_ISSUE_ORDER: [
                    CallbackQueryHandler(da_main_menu_callback, pattern='^select_order')
                ],
                NEW_ISSUE_DESCRIPTION: [
                    MessageHandler(Filters.text & ~Filters.command, new_issue_description)
                ],
                NEW_ISSUE_REASON: [
                    CallbackQueryHandler(da_main_menu_callback, pattern='^issue_reason_')
                ],
                NEW_ISSUE_TYPE: [
                    CallbackQueryHandler(da_main_menu_callback, pattern='^issue_type_')
                ],
                ASK_IMAGE: [
                    CallbackQueryHandler(da_main_menu_callback, pattern='^attach_')
                ],
                WAIT_IMAGE: [
                    MessageHandler(Filters.photo, wait_image),
                    MessageHandler(Filters.text & ~Filters.command, lambda u, c: u.message.reply_text("يرجى إرسال صورة."))
                ],
                EDIT_PROMPT: [
                    CallbackQueryHandler(edit_ticket_prompt_callback, pattern='^edit_ticket_')
                ],
                EDIT_FIELD: [
                    CallbackQueryHandler(edit_field_callback, pattern='^edit_field_'),
                    MessageHandler(Filters.text & ~Filters.command, edit_field_input_handler),
                    MessageHandler(~Filters.text & ~Filters.command, default_handler_da_edit)
                ],
                MORE_INFO_PROMPT: [
                    MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)
                ],
                AWAITING_DA_RESPONSE: [
                    MessageHandler(Filters.text & ~Filters.command, da_awaiting_response_handler)
                ]
            },
            fallbacks=[
                CommandHandler('cancel', lambda update, context: (
                    update.message.reply_text("تم إلغاء العملية."), 
                    ConversationHandler.END)[1]
                ),
                CommandHandler('start', start)
            ],
            allow_reentry=True
        )

        dp.add_handler(conv_handler)
        dp.add_error_handler(error_handler)

        logger.info("Starting polling...")
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        return

def test_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot is working! Try /start")

# In main(), add this before adding the conversation handler:

# Add error handler function
def error_handler(update: Update, context: CallbackContext):
    """Log Errors caused by Updates."""
    logger.error('Update "%s" caused error "%s"', update, context.error)
