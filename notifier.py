# notifier.py
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import db
from config import DA_BOT_TOKEN, SUPERVISOR_BOT_TOKEN, CLIENT_BOT_TOKEN

# Create standalone Bot objects (used only for sending notifications)
da_bot = Bot(token=DA_BOT_TOKEN)
supervisor_bot = Bot(token=SUPERVISOR_BOT_TOKEN)
client_bot = Bot(token=CLIENT_BOT_TOKEN)

def notify_supervisors(ticket):
    supervisors = db.get_users_by_role("supervisor")
    for sup in supervisors:
        message = (
            f"تم إنشاء بلاغ جديد.\n"
            f"رقم التذكرة: {ticket['ticket_id']}\n"
            f"رقم الأوردر: {ticket['order_id']}\n"
            f"الوصف: {ticket['issue_description']}"
        )
        buttons = [
            [InlineKeyboardButton("عرض التفاصيل", callback_data=f"view|{ticket['ticket_id']}")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        try:
            if ticket['image_url']:
                supervisor_bot.send_photo(chat_id=sup["chat_id"], photo=ticket['image_url'],
                                            caption=message, reply_markup=markup, parse_mode="HTML")
            else:
                supervisor_bot.send_message(chat_id=sup["chat_id"], text=message,
                                              reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            print("Error notifying supervisor:", e)

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
