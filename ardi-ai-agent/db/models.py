import datetime
from sqlalchemy import String, BigInteger, Text, DateTime, Boolean, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


def _utcnow():
    return datetime.datetime.now(datetime.UTC)


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    address: Mapped[str] = mapped_column(Text, nullable=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    ai_active: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_tone: Mapped[str] = mapped_column(String(50), default="friendly")
    business_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    business_hours_start: Mapped[str] = mapped_column(String(5), nullable=True)
    business_hours_end: Mapped[str] = mapped_column(String(5), nullable=True)
    ai_offline_message: Mapped[str] = mapped_column(Text, nullable=True)
    subscription_status: Mapped[str] = mapped_column(String(20), default="trial")
    trial_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    trial_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_plan: Mapped[str] = mapped_column(String(10), nullable=True)
    subscription_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    orders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    order_bank_name: Mapped[str] = mapped_column(String(100), nullable=True)
    order_bank_account: Mapped[str] = mapped_column(String(100), nullable=True)
    order_account_holder: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="business", cascade="all, delete-orphan")


class BusinessConnectionModel(Base):
    __tablename__ = "business_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    connection_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    user_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), default="guest")
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    photo_file_id: Mapped[str] = mapped_column(String(512), nullable=True)
    photo_url: Mapped[str] = mapped_column(Text, nullable=True)
    photo_caption: Mapped[str] = mapped_column(Text, nullable=True)
    photo_embedding: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    business: Mapped["Business"] = relationship("Business", back_populates="products")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    customer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str] = mapped_column(String(50), nullable=True)
    customer_address: Mapped[str] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    total_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)

    order: Mapped["Order"] = relationship("Order", back_populates="items")


class EscalatedChat(Base):
    __tablename__ = "escalated_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    customer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    last_customer_message: Mapped[str] = mapped_column(Text, nullable=True)
    last_ai_reply: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
