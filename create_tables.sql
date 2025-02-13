-- Create subscriptions table
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, chat_id)
);

-- Create tickets table
CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    subscription_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    url TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
);

-- Create index on subscription_id for better query performance
CREATE INDEX IF NOT EXISTS idx_tickets_subscription_id ON tickets(subscription_id);

-- Create index on status for filtering
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

