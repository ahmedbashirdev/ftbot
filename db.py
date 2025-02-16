from sqlalchemy import Column, Integer, String, Text, ForeignKey, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import text
from contextlib import contextmanager
import logging
from config import DATABASE_URL

# ✅ Setup logging
logger = logging.getLogger(__name__)

# ✅ Define SQLAlchemy Base
Base = declarative_base()

# ✅ Create Engine
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800
)

# ✅ Create Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_connection():
    """Get a database connection from the pool with proper context management."""
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()

def get_db_session():
    """Returns a new database session."""
    return SessionLocal()

# ✅ Define Ticket Model
class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, nullable=False)
    issue_description = Column(Text, nullable=False)  # ✅ Correct name
    issue_reason = Column(String, nullable=False)
    issue_type = Column(String, nullable=False)
    client = Column(String, nullable=False)
    image_url = Column(String, nullable=True)
    status = Column(String, default="Opened", nullable=False)
    da_id = Column(Integer, nullable=False)  # ✅ Use da_id instead of user_id

    def __repr__(self):
        return f"<Ticket(ticket_id={self.ticket_id}, order_id={self.order_id}, status={self.status})>"

def init_db():
    """Initialize PostgreSQL database schema"""
    with get_connection() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY,
                role TEXT,
                bot TEXT,
                phone TEXT,
                client TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                chat_id BIGINT
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id SERIAL PRIMARY KEY,
                order_id TEXT NOT NULL,
                issue_description TEXT NOT NULL,
                issue_reason TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                client TEXT NOT NULL,
                image_url TEXT,
                status TEXT DEFAULT 'Opened',
                da_id BIGINT NOT NULL,
                logs TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()

def get_subscription(user_id, bot):
    """Fetch subscription by user ID and bot name."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE user_id = :user_id AND bot = :bot"),
            {"user_id": user_id, "bot": bot}
        ).fetchone()
    
    if not result:
        return None
    return dict(result._mapping)
def get_users_by_role(role):
    """Retrieve all users with a specific role."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT user_id FROM subscriptions WHERE role = :role"),
            {"role": role}
        ).fetchall()
    
    return [row[0] for row in result] if result else []
def add_ticket(order_id, issue_description, issue_reason, issue_type, client, image_url, status, da_id):
    """Insert a new ticket into the database."""
    session = get_db_session()
    try:
        new_ticket = Ticket(
            order_id=order_id,
            issue_description=issue_description,  
            issue_reason=issue_reason,
            issue_type=issue_type,
            client=client,
            image_url=image_url,
            status=status,
            da_id=da_id  # ✅ Change from user_id to da_id
        )
        session.add(new_ticket)
        session.commit()
        return new_ticket.ticket_id  # ✅ Return ticket ID
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error adding ticket: {e}")
        return None
    finally:
        session.close()
def add_subscription(user_id, phone, role, bot, client, username, first_name, last_name, chat_id):
    """Insert a new subscription into the database."""
    with get_connection() as conn:
        conn.execute(
            text("""
                INSERT INTO subscriptions 
                (user_id, phone, role, bot, client, username, first_name, last_name, chat_id)
                VALUES (:user_id, :phone, :role, :bot, :client, :username, :first_name, :last_name, :chat_id)
                ON CONFLICT (user_id) DO UPDATE SET
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

# ✅ Migration from SQLite to PostgreSQL
def migrate_data():
    import sqlite3
    from sqlalchemy import create_engine, text

    # ✅ Connect to SQLite
    sqlite_conn = sqlite3.connect('issue_resolution.db')
    sqlite_conn.row_factory = sqlite3.Row
    
    # ✅ Connect to PostgreSQL
    pg_engine = create_engine(DATABASE_URL)
    
    with pg_engine.connect() as pg_conn:
        # ✅ Migrate subscriptions
        subscriptions = sqlite_conn.execute("SELECT * FROM subscriptions").fetchall()
        for sub in subscriptions:
            pg_conn.execute(
                text("""
                    INSERT INTO subscriptions 
                    (user_id, phone, role, bot, client, username, first_name, last_name, chat_id)
                    VALUES (:user_id, :phone, :role, :bot, :client, :username, :first_name, :last_name, :chat_id)
                    ON CONFLICT (user_id) DO NOTHING
                """),
                dict(sub)
            )
        
        # ✅ Migrate tickets
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
def get_all_open_tickets():
    """Retrieve all open tickets from the database."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE status != 'Closed'")
        ).fetchall()
    
    # ✅ Convert SQLAlchemy Row objects to list of dictionaries
    return [dict(row._mapping) for row in result] if result else []