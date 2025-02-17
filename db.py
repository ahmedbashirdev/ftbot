from sqlalchemy import Column, Integer, String, Text, ForeignKey, create_engine, text, DateTime  # ✅ Add DateTime here
import datetime  # ✅ Ensure datetime is imported
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import text
from contextlib import contextmanager
import json 
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
    issue_description = Column(Text, nullable=False)
    issue_reason = Column(String, nullable=False)
    issue_type = Column(String, nullable=False)
    client = Column(String, nullable=False)
    image_url = Column(String, nullable=True)
    status = Column(String, default="Opened", nullable=False)
    da_id = Column(Integer, nullable=False)
    logs = Column(Text, nullable=True)  # ✅ Add this column
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

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
                ON CONFLICT DO NOTHING
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
def get_tickets_by_user(user_id):
    """Retrieve all tickets for a given DA (user_id)."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE da_id = :user_id"),
            {"user_id": user_id}
        ).fetchall()
    
    return [dict(row._mapping) for row in result] if result else []
# db.py

def search_tickets_by_order(order_id):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE order_id = :order_id"),
            {"order_id": order_id}
        )
        tickets = result.fetchall()
    return [dict(ticket) for ticket in tickets]
def get_ticket(ticket_id):
    """Retrieve a single ticket by its ID."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id}
        ).fetchone()
    
    return dict(result._mapping) if result else None
def update_ticket_status(ticket_id, new_status, log_entry=None):
    """Update the status of a ticket and optionally add a log entry."""
    session = get_db_session()
    
    try:
        # Fetch the ticket
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        
        if not ticket:
            logger.error(f"❌ Ticket with ID {ticket_id} not found!")
            return False

        # Update the status
        ticket.status = new_status

        # Append log entry if provided
        if log_entry:
            logs = []
            if ticket.logs:
                try:
                    logs = json.loads(ticket.logs)
                except Exception as e:
                    logger.error(f"❌ Error parsing logs for ticket {ticket_id}: {e}")
            
            log_entry["timestamp"] = datetime.datetime.now().isoformat()
            logs.append(log_entry)
            ticket.logs = json.dumps(logs)

        session.commit()
        logger.info(f"✅ Updated ticket {ticket_id} status to {new_status}")
        return True

    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error updating ticket {ticket_id}: {e}")
        return False

    finally:
        session.close()
def get_clients_by_name(client_name):
    """Fetch all clients that match the given client name."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE client = :client"),
            {"client": client_name}
        ).fetchall()
    
    if result:
        return [dict(row._mapping) for row in result]
    return []
def get_tickets_by_client(user_id):
    """Retrieve all tickets for a given client using user ID."""
    try:
        with get_connection() as conn:
            # Retrieve the correct client based on the bot name
            result = conn.execute(
                text("SELECT client FROM subscriptions WHERE user_id = :user_id AND bot = 'Client'"),
                {"user_id": user_id}
            ).fetchone()

            if not result:
                logger.warning(f"⚠️ No subscription found for user ID: {user_id} (Client bot)")
                return []

            client_name = result[0]  # Get the client name
            
            if not client_name:
                logger.warning(f"⚠️ Subscription found, but client name is missing for user ID: {user_id}")
                return []
            
            logger.info(f"✅ Found client '{client_name}' for user ID: {user_id}")

            # Fetch tickets for the found client
            tickets = conn.execute(
                text("SELECT * FROM tickets WHERE client = :client"),
                {"client": client_name}
            ).fetchall()

            return [dict(row._mapping) for row in tickets] if tickets else []

    except Exception as e:
        logger.error(f"❌ Error fetching tickets for user ID {user_id}: {e}", exc_info=True)
        return []