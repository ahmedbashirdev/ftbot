import sqlite3
from sqlalchemy import create_engine, text
from config import DATABASE_URL

def migrate_data():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('issue_resolution.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to PostgreSQL
    pg_engine = create_engine(DATABASE_URL)
    
    # Migrate subscriptions
    subscriptions = sqlite_conn.execute("SELECT * FROM subscriptions").fetchall()
    with pg_engine.connect() as pg_conn:
        for sub in subscriptions:
            pg_conn.execute(
                text("""
                    INSERT INTO subscriptions 
                    (user_id, phone, role, bot, client, username, first_name, last_name, chat_id)
                    VALUES (:user_id, :phone, :role, :bot, :client, :username, :first_name, :last_name, :chat_id)
                    ON CONFLICT (user_id, bot) DO NOTHING
                """),
                dict(sub)
            )
        
        # Migrate tickets
        tickets = sqlite_conn.execute("SELECT * FROM tickets").fetchall()
        for ticket in tickets:
            pg_conn.execute(
                text("""
                    INSERT INTO tickets 
                    (ticket_id, order_id, issue_description, issue_reason, issue_type, 
                     client, image_url, status, da_id, logs, created_at)
                    VALUES (:ticket_id, :order_id, :issue_description, :issue_reason, :issue_type,
                            :client, :image_url, :status, :da_id, :logs, :created_at)
                    ON CONFLICT (ticket_id) DO NOTHING
                """),
                dict(ticket)
            )
        
        pg_conn.commit()
    
    sqlite_conn.close()

if __name__ == "__main__":
    migrate_data()
