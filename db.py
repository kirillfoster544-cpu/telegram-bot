import os
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime, Enum, ForeignKey, Boolean
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.sql import func
import enum

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    # fallback (dev only)
    DATABASE_URL = "sqlite+aiosqlite:///./dev.sqlite3"

# Railway Postgres обычно вида: postgres://... → нужно postgres+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()

class TopupStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class DealStatus(str, enum.Enum):
    CREATED = "CREATED"      # created by one side, waiting second side
    FUNDED = "FUNDED"        # buyer paid into escrow
    DELIVERED = "DELIVERED"  # seller marked delivered
    RELEASED = "RELEASED"    # buyer confirmed -> seller credited
    REFUNDED = "REFUNDED"    # admin refunded -> buyer credited
    DISPUTE = "DISPUTE"      # dispute opened

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)  # telegram user id
    username = Column(String(64), default="")
    full_name = Column(String(128), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    balance = relationship("Balance", uselist=False, back_populates="user")

class Balance(Base):
    __tablename__ = "balances"
    user_id = Column(BigInteger, ForeignKey("users.id"), primary_key=True)
    amount = Column(Integer, default=0)  # rubles integer
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", back_populates="balance")

class Topup(Base):
    __tablename__ = "topups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    amount = Column(Integer, nullable=False)
    note = Column(String(200), default="")
    status = Column(Enum(TopupStatus), default=TopupStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)

class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    public_code = Column(String(16), unique=True, index=True)

    description = Column(String(140), default="")
    amount = Column(Integer, nullable=False)
    fee = Column(Integer, default=0)
    status = Column(Enum(DealStatus), default=DealStatus.CREATED)

    seller_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    buyer_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    created_by = Column(BigInteger, nullable=False)  # who created
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute_opened = Column(Boolean, default=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
