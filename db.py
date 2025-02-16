from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager
from config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800
)

@contextmanager
def get_connection():
    """Get a database connection from the pool with proper context management."""
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()

def init_db():
    """Initialize PostgreSQL database schema"""
    with get_connection() as conn:
        # Create subscriptions table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT,
                role TEXT,
                bot TEXT,
                phone TEXT,
                client TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                chat_id BIGINT,
                PRIMARY KEY (user_id, bot)
            )
        """))
        
        # Create tickets table with proper PostgreSQL types
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id SERIAL PRIMARY KEY,
                order_id TEXT,
                issue_description TEXT,
                issue_reason TEXT,
                issue_type TEXT,
                client TEXT,
                image_url TEXT,
                status TEXT,
                da_id BIGINT,
                logs TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()

# Example of updated query function
def get_subscription(user_id, bot):
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE user_id = :user_id AND bot = :bot"),
            {"user_id": user_id, "bot": bot}
        ).fetchone()
    if not result:
        return None

    # ✅ Convert SQLAlchemy Row object to dictionary
    return dict(result._mapping)  

def add_ticket(order_id, description, issue_reason, issue_type, client, image_url, status, user_id):
    """Insert a new ticket into the database."""
    session = get_db_session()  # Ensure session is managed correctly
    try:
        new_ticket = Ticket(
            order_id=order_id,
            description=description,
            issue_reason=issue_reason,
            issue_type=issue_type,
            client=client,
            image_url=image_url,
            status=status,
            user_id=user_id
        )
        session.add(new_ticket)
        session.commit()
        return new_ticket.ticket_id  # Return the ID of the new ticket
    except Exception as e:
        session.rollback()
        logger.error(f"Error adding ticket: {e}")
        return None
    finally:
        session.close()
        
def add_subscription(user_id, phone, role, bot, client, username, first_name, last_name, chat_id):
    with get_connection() as conn:
        conn.execute(
            text("""
                INSERT INTO subscriptions 
                (user_id, phone, role, bot, client, username, first_name, last_name, chat_id)
                VALUES (:user_id, :phone, :role, :bot, :client, :username, :first_name, :last_name, :chat_id)
                ON CONFLICT (user_id, bot) DO UPDATE SET
                    phone = EXCLUDED.phone,
                    role = EXCLUDED.role,
                    client = EXCLUDED.client,
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    chat_id = EXCLUDED.chat_id
            """),
            {
                "user_id": user_id,
                "phone": phone,
                "role": role,
                "bot": bot,
                "client": client,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "chat_id": chat_id
            }
        )
        conn.commit()
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
