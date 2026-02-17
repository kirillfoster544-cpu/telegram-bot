import os
import secrets

from sqlalchemy import create_engine, Integer, String, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column

DB_PATH = os.getenv("DB_PATH", "guarant.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class TopupStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DealStatus:
    CREATED = "CREATED"
    FUNDED = "FUNDED"
    DELIVERED = "DELIVERED"
    RELEASED = "RELEASED"
    DISPUTE = "DISPUTE"
    REFUNDED = "REFUNDED"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Balance(Base):
    __tablename__ = "balances"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Topup(Base):
    __tablename__ = "topups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=TopupStatus.PENDING, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    description: Mapped[str] = mapped_column(String(140), default="", nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    fee: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    creator_role: Mapped[str] = mapped_column(String(16), default="buyer", nullable=False)

    buyer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    seller_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=DealStatus.CREATED, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @staticmethod
    def gen_public_code() -> str:
        return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].lower()


def init_db():
    Base.metadata.create_all(bind=engine)
