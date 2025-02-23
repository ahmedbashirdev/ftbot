# notifier.py
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import db
import config
import logging
from config import DA_BOT_TOKEN, SUPERVISOR_BOT_TOKEN, CLIENT_BOT_TOKEN

logger = logging.getLogger(__name__)

# Create standalone Bot objects (used only for sending notifications)
da_bot = Bot(token=DA_BOT_TOKEN)
supervisor_bot = Bot(token=SUPERVISOR_BOT_TOKEN)
client_bot = Bot(token=CLIENT_BOT_TOKEN)

def notify_supervisors(ticket):
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)
    
    # Build the notification text using dictionary keys
    text = (
        f"🚨 <b>تذكرة جديدة #{ticket['ticket_id']}</b> تم إنشاؤها.\n"
        f"🔹 <b>رقم الطلب:</b> {ticket['order_id']}\n"
        f"🔹 <b>الوصف:</b> {ticket['issue_description']}\n"
        f"🔹 <b>سبب المشكلة:</b> {ticket['issue_reason']}\n"
        f"🔹 <b>نوع المشكلة:</b> {ticket['issue_type']}\n"
        f"🔹 <b>العميل:</b> {ticket['client']}\n"
        f"🔹 <b>الحالة:</b> {ticket['status']}"
    )
    
    keyboard = [
        [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket['ticket_id']}")],
        [InlineKeyboardButton("طلب معلومات إضافية", callback_data=f"moreinfo|{ticket['ticket_id']}")],
        [InlineKeyboardButton("إرسال إلى العميل", callback_data=f"sendclient|{ticket['ticket_id']}")]
    ]
    if ticket['status'] == "Client Responded":
        keyboard.insert(0, [InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket['ticket_id']}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Retrieve all supervisors from the DB
    supervisors = db.get_supervisors()
    for sup in supervisors:
        try:
            if ticket.get("image_url"):
                bot.send_photo(
                    chat_id=sup['chat_id'],
                    photo=ticket.get("image_url"),
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

def notify_client(ticket):
    clients = db.get_users_by_role("client", client=ticket["client"])
    for client in clients:
        message = (
            f"تم رفع بلاغ يتعلق بطلب {ticket['order_id']}.\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"النوع: {ticket['issue_type']}"
        )
        buttons = [
            [InlineKeyboardButton("عرض التفاصيل", callback_data=f"client_view|{ticket['ticket_id']}")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        try:
            if ticket['image_url']:
                client_bot.send_photo(chat_id=client["chat_id"], photo=ticket['image_url'],
                                        caption=message, reply_markup=markup, parse_mode="HTML")
            else:
                client_bot.send_message(chat_id=client["chat_id"], text=message,
                                        reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logger.error("Error notifying client: %s", e)
def notify_supervisors_da_moreinfo(ticket_id: int, additional_info: str):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        logger.error("notify_supervisors_da_moreinfo: Ticket %s not found", ticket_id)
        return
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)
    text = (
        f"<b>معلومات إضافية من الوكيل للتذكرة #{ticket_id}</b>\n"
        f"رقم الطلب: {ticket['order_id']}\n"
        f"الوصف: {ticket['issue_description']}\n"
        f"المعلومات الإضافية: {additional_info}\n"
        f"الحالة: {ticket['status']}"
    )
    keyboard = [[InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    supervisors = db.get_supervisors()
    logger.info("notify_supervisors_da_moreinfo: Found %d supervisor(s): %s", len(supervisors), supervisors)
    if not supervisors:
        logger.error("notify_supervisors_da_moreinfo: No supervisors found in DB.")
        return
    for sup in supervisors:
        try:
            sup_chat = sup.get('chat_id')
            if not sup_chat:
                logger.error("notify_supervisors_da_moreinfo: Supervisor %s has no chat_id", sup)
                continue
            logger.info("Notifying supervisor %s for ticket %s", sup_chat, ticket_id)
            bot.send_message(chat_id=sup_chat, text=text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error("notify_supervisors_da_moreinfo: Error notifying supervisor %s: %s", sup.get('chat_id'), e)
def notify_da(ticket):
    da_user = db.get_user(ticket["da_id"], "da")
    if da_user:
        message = (
            f"تم تحديث بلاغك رقم {ticket['ticket_id']}.\n"
            f"الوصف: {ticket['issue_description']}\n"
            f"الحالة: {ticket['status']}"
        )
        buttons = [
            [InlineKeyboardButton("عرض التفاصيل", callback_data=f"da_view|{ticket['ticket_id']}")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        try:
            if ticket['image_url']:
                da_bot.send_photo(chat_id=da_user["chat_id"], photo=ticket['image_url'],
                                  caption=message, reply_markup=markup, parse_mode="HTML")
            else:
                da_bot.send_message(chat_id=da_user["chat_id"], text=message,
                                    reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logger.error("Error notifying DA: %s", e)