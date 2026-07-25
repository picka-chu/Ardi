-- Migration: Full schema for Ardi AI Agent on Supabase PostgreSQL
-- Run this in Supabase Dashboard > SQL Editor

-- Businesses (registered by owners)
CREATE TABLE IF NOT EXISTS businesses (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    address TEXT,
    phone VARCHAR(50),
    channel_id BIGINT,
    ai_active BOOLEAN NOT NULL DEFAULT FALSE,
    ai_tone VARCHAR(50) NOT NULL DEFAULT 'friendly',
    business_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    business_hours_start VARCHAR(5),
    business_hours_end VARCHAR(5),
    ai_offline_message TEXT,
    subscription_status VARCHAR(20) NOT NULL DEFAULT 'trial',
    trial_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trial_end TIMESTAMPTZ,
    subscription_plan VARCHAR(10),
    subscription_end TIMESTAMPTZ,
    orders_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    order_bank_name VARCHAR(100),
    order_bank_account VARCHAR(100),
    order_account_holder VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Products (belong to a business)
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    price NUMERIC(10, 2),
    available BOOLEAN NOT NULL DEFAULT TRUE,
    photo_file_id VARCHAR(512),
    photo_url TEXT,
    photo_caption TEXT,
    photo_embedding TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Telegram Business connections
CREATE TABLE IF NOT EXISTS business_connections (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    connection_id VARCHAR(255) NOT NULL UNIQUE,
    user_chat_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Users (track first-time vs returning)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    role VARCHAR(50) NOT NULL DEFAULT 'guest',
    business_id INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Orders (placed by customers via Ardi AI)
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_telegram_id BIGINT,
    customer_name VARCHAR(255),
    customer_phone VARCHAR(50),
    customer_address TEXT,
    notes TEXT,
    total_price NUMERIC(10, 2) NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Order items (products in each order)
CREATE TABLE IF NOT EXISTS order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price NUMERIC(10, 2) NOT NULL DEFAULT 0
);

-- Escalated chats (customer conversations escalated to business owner)
CREATE TABLE IF NOT EXISTS escalated_chats (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_telegram_id BIGINT,
    customer_name VARCHAR(255),
    reason TEXT,
    last_customer_message TEXT,
    last_ai_reply TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add columns to existing tables (idempotent ALTER TABLE for upgrades)
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_start VARCHAR(5);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_end VARCHAR(5);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ai_offline_message TEXT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20) NOT NULL DEFAULT 'trial';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_start TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_end TIMESTAMPTZ;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(10);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_end TIMESTAMPTZ;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS orders_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_bank_name VARCHAR(100);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_bank_account VARCHAR(100);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_account_holder VARCHAR(255);
ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_caption TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_embedding TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS price NUMERIC(10, 2);
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT 'guest';
ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id INTEGER REFERENCES businesses(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(10) NOT NULL DEFAULT 'en';
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE orders ALTER COLUMN total_price TYPE NUMERIC(10, 2) USING total_price::numeric;
ALTER TABLE order_items ALTER COLUMN unit_price TYPE NUMERIC(10, 2) USING unit_price::numeric;
ALTER TABLE products ALTER COLUMN price TYPE NUMERIC(10, 2) USING price::numeric;
