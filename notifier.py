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
    """
    Notify all supervisors about a new ticket.
    If the ticket includes an image, send the image with the ticket details as the caption.
    Otherwise, send the ticket details as a text message.
    The message is in Arabic and includes all the relevant fields.
    """
    bot = Bot(token=config.SUPERVISOR_BOT_TOKEN)
    
    # Build the message text using the Ticket object's attributes
    text = (
        f"🚨 تذكرة جديدة #{ticket.ticket_id} تم إنشاؤها.\n"
        f"🔹 رقم الطلب: {ticket.order_id}\n"
        f"🔹 الوصف: {ticket.issue_description}\n"
        f"🔹 سبب المشكلة: {ticket.issue_reason}\n"
        f"🔹 نوع المشكلة: {ticket.issue_type}\n"
        f"🔹 العميل: {ticket.client}\n"
        f"🔹 الحالة: {ticket.status}"
    )
    
    # Define the inline keyboard if needed (for example, to view details)
    keyboard = [
        [InlineKeyboardButton("حل المشكلة", callback_data=f"solve|{ticket.ticket_id}")],
        [InlineKeyboardButton("طلب معلومات إضافية", callback_data=f"moreinfo|{ticket.ticket_id}")],
        [InlineKeyboardButton("إرسال إلى العميل", callback_data=f"sendclient|{ticket.ticket_id}")]
    ]
    # Optionally, if the ticket is "Client Responded", add:
    if ticket.status == "Client Responded":
        keyboard.insert(0, [InlineKeyboardButton("إرسال للحالة إلى الوكيل", callback_data=f"sendto_da|{ticket.ticket_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    supervisors = db.get_supervisors()  # This should return a list of supervisor dicts.
    for sup in supervisors:
        try:
            if ticket.image_url:
                bot.send_photo(
                    chat_id=sup['chat_id'],
                    photo=ticket.image_url,
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
            print("Error notifying client:", e)

def notify_da(ticket):
    # Get the DA by using the da_id field from the ticket
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
            print("Error notifying DA:", e)
