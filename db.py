from sqlalchemy import Column, Integer, String, Text, ForeignKey, create_engine, text, DateTime  
import datetime  
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import text
from contextlib import contextmanager
import json 
import logging 
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Define SQLAlchemy Base
Base = declarative_base()

# Create Engine
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800
)

# Create Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_connection():
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()

def get_db_session():
    return SessionLocal()

# Ticket Model
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
    logs = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<Ticket(ticket_id={self.ticket_id}, order_id={self.order_id}, status={self.status})>"

def init_db():
    with get_connection() as conn:
        # Create subscriptions table with a unique constraint on user_id and chat_id
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                phone TEXT,
                role TEXT,
                bot TEXT,
                client TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, chat_id)
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
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE user_id = :user_id AND bot = :bot"),
            {"user_id": user_id, "bot": bot}
        ).fetchone()
    if not result:
        return None
    return dict(result._mapping)

def update_ticket_details(ticket_id, new_description):
    """Update the issue_description field of a ticket."""
    session = get_db_session()
    try:
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if not ticket:
            logger.error("Ticket with ID %s not found!", ticket_id)
            return False
        ticket.issue_description = new_description
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error("Error updating ticket details for ticket %s: %s", ticket_id, e)
        return False
    finally:
        session.close()

def get_users_by_role(role, client=None):
    with get_connection() as conn:
        if client:
            result = conn.execute(
                text("SELECT * FROM subscriptions WHERE role = :role AND LOWER(client) = LOWER(:client)"),
                {"role": role.capitalize(), "client": client}
            )
        else:
            result = conn.execute(
                text("SELECT * FROM subscriptions WHERE role = :role"),
                {"role": role.capitalize()}
            )
        users = result.fetchall()
    return [dict(row._mapping) for row in users] if users else []

def add_subscription(user_id, phone, role, bot, client, username, first_name, last_name, chat_id):
    """Insert or update a subscription into the database."""
    with get_connection() as conn:
        conn.execute(
            text("""
                INSERT INTO subscriptions 
                (user_id, phone, role, bot, client, username, first_name, last_name, chat_id)
                VALUES (:user_id, :phone, :role, :bot, :client, :username, :first_name, :last_name, :chat_id)
                ON CONFLICT (user_id, chat_id) DO UPDATE SET 
                    phone = EXCLUDED.phone,
                    client = EXCLUDED.client,
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name
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

def add_ticket(order_id, issue_description, issue_reason, issue_type, client, image_url, status, da_id):
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
            da_id=da_id
        )
        session.add(new_ticket)
        session.commit()
        return new_ticket.ticket_id
    except Exception as e:
        session.rollback()
        logger.error("Error adding ticket: %s", e)
        return None
    finally:
        session.close()

def get_all_open_tickets():
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE status != 'Closed'")
        ).fetchall()
    return [dict(row._mapping) for row in result] if result else []

def get_tickets_by_user(user_id):
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE da_id = :user_id"),
            {"user_id": user_id}
        ).fetchall()
    return [dict(row._mapping) for row in result] if result else []

def get_ticket(ticket_id):
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id}
        ).fetchone()
    return dict(result._mapping) if result else None

def update_ticket_status(ticket_id, new_status, log_entry=None):
    """
    Update the status of a ticket and optionally append a log entry.
    Ensures the ticket_id is an integer.
    """
    try:
        ticket_id = int(ticket_id)
    except Exception as e:
        logger.error("Invalid ticket_id provided: %s", ticket_id)
        return False

    session = get_db_session()
    try:
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if not ticket:
            logger.error("Ticket with ID %s not found!", ticket_id)
            return False
        ticket.status = new_status
        if log_entry:
            logs = []
            if ticket.logs:
                try:
                    logs = json.loads(ticket.logs)
                except Exception as e:
                    logger.error("Error parsing logs for ticket %s: %s", ticket_id, e)
            log_entry["timestamp"] = datetime.datetime.now().isoformat()
            logs.append(log_entry)
            ticket.logs = json.dumps(logs)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error("Error updating ticket %s: %s", ticket_id, e)
        return False
    finally:
        session.close()

def get_tickets_by_client(user_id):
    """Retrieve all tickets for a given client using the user's subscription information."""
    with get_connection() as conn:
        # First, fetch the subscription for the client bot using the user ID
        result = conn.execute(
            text("SELECT client FROM subscriptions WHERE user_id = :user_id AND bot = 'Client'"),
            {"user_id": user_id}
        ).fetchone()
        
        if not result:
            logger.warning(f"No subscription found for user ID: {user_id} (Client bot)")
            return []
        
        # Get the client name from the subscription
        client_name = result[0]
        if not client_name:
            logger.warning(f"Subscription found for user ID: {user_id} but client name is missing")
            return []
        
        logger.info(f"Found client '{client_name}' for user ID: {user_id}")
        
        # Now, fetch tickets where the 'client' field matches the subscription's client name
        tickets = conn.execute(
            text("SELECT * FROM tickets WHERE client = :client"),
            {"client": client_name}
        ).fetchall()
        
        return [dict(row._mapping) for row in tickets] if tickets else []

def search_tickets_by_order(order_id):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE order_id ILIKE :order_id"),
            {"order_id": f"%{order_id}%"}
        )
        tickets = result.fetchall()
    return [dict(ticket) for ticket in tickets]

def get_all_subscriptions():
    with get_connection() as conn:
        result = conn.execute(text("SELECT * FROM subscriptions")).fetchall()
    return [dict(row._mapping) for row in result] if result else []

def get_supervisors():
    """Retrieve all subscriptions for supervisors (bot='Supervisor' and role='Supervisor')."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE role = :role AND bot = 'Supervisor'"),
            {"role": "Supervisor"}
        ).fetchall()
    return [dict(row._mapping) for row in result] if result else []

def get_clients_by_name(client_name):
    """Retrieve all subscriptions with the given client name (for Client role)."""
    with get_connection() as conn:
        result = conn.execute(
            text("SELECT * FROM subscriptions WHERE client = :client"),
            {"client": client_name}
        ).fetchall()
    return [dict(row._mapping) for row in result] if result else []

def migrate_data():
    # Migration logic (if needed)
    pass

if __name__ == "__main__":
    init_db()
    # Optionally, call migrate_data()