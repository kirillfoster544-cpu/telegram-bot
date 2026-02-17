import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from sqlalchemy import select

from db import (
    SessionLocal,
    init_db,
    User,
    Balance,
    Topup,
    Deal,
    TopupStatus,
    DealStatus,
)

from telegram_auth import verify_telegram_init_data


# =========================
# ENV
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip()
COMMISSION_PCT = float(os.getenv("COMMISSION_PCT", "0") or "0")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty")


# =========================
# BOT + APP
# =========================

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI(title="Guarant Mini-App")


# =========================
# HELPERS
# =========================

def now_ts() -> int:
    return int(datetime.utcnow().timestamp())


def get_init_data(init_data: Optional[str], x_tg_init_data: Optional[str]) -> str:
    data = (init_data or "").strip() or (x_tg_init_data or "").strip()
    if not data:
        raise HTTPException(status_code=401, detail="Missing init_data")
    return data


def require_user(init_data: str) -> dict:
    ok, payload_or_err = verify_telegram_init_data(
        init_data=init_data,
        bot_token=BOT_TOKEN,
    )
    if not ok:
        raise HTTPException(status_code=401, detail=str(payload_or_err))
    return payload_or_err


def ensure_user(db_sess, tg_user: dict) -> int:
    tg_id = int(tg_user.get("id"))
    username = tg_user.get("username") or ""

    u = db_sess.execute(
        select(User).where(User.tg_id == tg_id)
    ).scalar_one_or_none()

    if not u:
        u = User(
            tg_id=tg_id,
            username=username,
            full_name="",
            created_at=now_ts()
        )
        db_sess.add(u)
        db_sess.commit()
        db_sess.refresh(u)

        b = Balance(user_id=u.id, amount=0)
        db_sess.add(b)
        db_sess.commit()

    return u.id


def get_balance(db_sess, user_id: int) -> int:
    bal = db_sess.execute(
        select(Balance).where(Balance.user_id == user_id)
    ).scalar_one_or_none()

    return int(bal.amount) if bal else 0


def add_balance(db_sess, user_id: int, delta: int):
    bal = db_sess.execute(
        select(Balance).where(Balance.user_id == user_id)
    ).scalar_one_or_none()

    if not bal:
        bal = Balance(user_id=user_id, amount=0)
        db_sess.add(bal)
        db_sess.commit()
        db_sess.refresh(bal)

    bal.amount = int(bal.amount) + int(delta)
    db_sess.commit()


# =========================
# API
# =========================

@app.get("/api/me")
def api_me(init_data: Optional[str] = None,
           x_tg_init_data: Optional[str] = Header(default=None)):

    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        balance = get_balance(s, uid)

        return {
            "id": tg_user["id"],
            "username": tg_user.get("username"),
            "balance": balance,
        }


@app.get("/api/balance")
def api_balance(init_data: Optional[str] = None,
                x_tg_init_data: Optional[str] = Header(default=None)):

    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        return {"balance": get_balance(s, uid)}


@app.get("/api/topups")
def api_topups(init_data: Optional[str] = None,
               x_tg_init_data: Optional[str] = Header(default=None)):

    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        rows = s.execute(
            select(Topup)
            .where(Topup.user_id == uid)
            .order_by(Topup.id.desc())
        ).scalars().all()

        return {
            "items": [
                {
                    "id": r.id,
                    "amount": int(r.amount),
                    "status": r.status,
                    "note": r.note,
                }
                for r in rows
            ]
        }


@app.post("/api/topups")
def api_topup_create(data: dict,
                     init_data: Optional[str] = None,
                     x_tg_init_data: Optional[str] = Header(default=None)):

    amount = int(data.get("amount", 0))
    note = data.get("note", "")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Bad amount")

    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)

        t = Topup(
            user_id=uid,
            amount=amount,
            status=TopupStatus.PENDING,
            note=note,
            created_at=now_ts(),
        )

        s.add(t)
        s.commit()

        return {"ok": True}


# =========================
# TELEGRAM BOT
# =========================

def webapp_keyboard() -> InlineKeyboardMarkup:
    url = APP_BASE_URL or "https://example.com"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Открыть гарант",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "💎 <b>Гарант</b>\n\n"
        "Открывай мини-апп и работай со сделками."
    )

    await message.answer(
        text,
        reply_markup=webapp_keyboard(),
    )


@dp.message(Command("workbr"))
async def cmd_workbr(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split()

    if len(parts) != 3:
        await message.answer(
            "Используй:\n<code>/workbr TG_ID AMOUNT</code>"
        )
        return

    tg_id = int(parts[1])
    amount = int(parts[2])

    with SessionLocal() as s:
        u = s.execute(
            select(User).where(User.tg_id == tg_id)
        ).scalar_one_or_none()

        if not u:
            u = User(
                tg_id=tg_id,
                username="",
                full_name="",
                created_at=now_ts()
            )
            s.add(u)
            s.commit()
            s.refresh(u)

            s.add(Balance(user_id=u.id, amount=0))
            s.commit()

        add_balance(s, u.id, amount)

    await message.answer("Баланс выдан.")


async def run_bot():
    await dp.start_polling(bot)


@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(run_bot())


@app.get("/health")
def health():
    return JSONResponse({"ok": True})


# =========================
# STATIC
# =========================

WEB_DIR = os.path.dirname(__file__)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
