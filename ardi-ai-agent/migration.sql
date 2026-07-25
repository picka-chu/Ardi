-- Migration: Create tables for Ardi AI Agent on Supabase PostgreSQL
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
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Products (belong to a business)
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    price FLOAT,
    available BOOLEAN NOT NULL DEFAULT TRUE,
    photo_file_id VARCHAR(512),
    photo_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Telegram Business connections
CREATE TABLE IF NOT EXISTS business_connections (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    connection_id VARCHAR(255) NOT NULL UNIQUE,
    user_chat_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Users (track first-time vs returning)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMP NOT NULL DEFAULT NOW()
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
    total_price FLOAT NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Order items (products in each order)
CREATE TABLE IF NOT EXISTS order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price FLOAT NOT NULL DEFAULT 0
);

-- Add available column to existing products table (if running on an existing DB)
ALTER TABLE products ADD COLUMN IF NOT EXISTS available BOOLEAN NOT NULL DEFAULT TRUE;
