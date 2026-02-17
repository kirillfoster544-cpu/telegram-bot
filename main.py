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

# ✅ ВАЖНО: без точек, иначе Railway/uvicorn main:app упадёт
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


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "7489815425").strip())
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip()  # например: https://your-service.up.railway.app
COMMISSION_PCT = float(os.getenv("COMMISSION_PCT", "0").strip() or "0")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

app = FastAPI(title="Guarant Mini-App")


def now_ts() -> int:
    return int(datetime.utcnow().timestamp())


# -------------------------
# FastAPI helpers
# -------------------------
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
        raise HTTPException(status_code=401, detail=f"Bad init_data: {payload_or_err}")
    return payload_or_err


def ensure_user(db_sess, tg_user: dict) -> int:
    tg_id = int(tg_user.get("id"))
    username = (tg_user.get("username") or "").strip()
    first_name = (tg_user.get("first_name") or "").strip()
    last_name = (tg_user.get("last_name") or "").strip()
    full_name = (first_name + " " + last_name).strip()

    u = db_sess.execute(select(User).where(User.tg_id == tg_id)).scalar_one_or_none()
    if not u:
        u = User(tg_id=tg_id, username=username, full_name=full_name, created_at=now_ts())
        db_sess.add(u)
        db_sess.commit()
        db_sess.refresh(u)

        b = Balance(user_id=u.id, amount=0)
        db_sess.add(b)
        db_sess.commit()
    else:
        u.username = username
        u.full_name = full_name
        db_sess.commit()

    return u.id


def get_balance_amount(db_sess, user_id: int) -> int:
    bal = db_sess.execute(select(Balance).where(Balance.user_id == user_id)).scalar_one_or_none()
    return int(bal.amount) if bal else 0


def add_balance(db_sess, user_id: int, delta: int):
    bal = db_sess.execute(select(Balance).where(Balance.user_id == user_id)).scalar_one_or_none()
    if not bal:
        bal = Balance(user_id=user_id, amount=0)
        db_sess.add(bal)
        db_sess.commit()
        db_sess.refresh(bal)
    bal.amount = int(bal.amount) + int(delta)
    db_sess.commit()


# -------------------------
# API
# -------------------------
@app.get("/api/me")
def api_me(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        bal = get_balance_amount(s, uid)
        return {"ok": True, "user_id": uid, "tg_id": tg_user["id"], "balance": bal}


@app.get("/api/topups")
def api_topups(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        rows = s.execute(select(Topup).where(Topup.user_id == uid).order_by(Topup.id.desc()).limit(50)).scalars().all()
        return {
            "ok": True,
            "items": [
                {
                    "id": r.id,
                    "amount": int(r.amount),
                    "status": r.status,
                    "created_at": int(r.created_at),
                    "note": r.note or "",
                }
                for r in rows
            ],
        }


@app.post("/api/topup/create")
def api_topup_create(
    amount: int,
    note: str = "",
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        t = Topup(user_id=uid, amount=int(amount), status=TopupStatus.PENDING, note=(note or "")[:500], created_at=now_ts())
        s.add(t)
        s.commit()
        s.refresh(t)
        return {"ok": True, "id": t.id}


@app.get("/api/deals")
def api_deals(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        rows = s.execute(
            select(Deal).where((Deal.buyer_id == uid) | (Deal.seller_id == uid)).order_by(Deal.id.desc()).limit(50)
        ).scalars().all()
        return {
            "ok": True,
            "items": [
                {
                    "id": r.id,
                    "title": r.title,
                    "amount": int(r.amount),
                    "buyer_id": r.buyer_id,
                    "seller_id": r.seller_id,
                    "status": r.status,
                    "created_at": int(r.created_at),
                    "access_code": r.access_code,
                }
                for r in rows
            ],
        }


@app.post("/api/deal/create")
def api_deal_create(
    title: str,
    amount: int,
    seller_tg_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    with SessionLocal() as s:
        buyer_uid = ensure_user(s, tg_user)

        seller_user = s.execute(select(User).where(User.tg_id == int(seller_tg_id))).scalar_one_or_none()
        if not seller_user:
            # создаём "пустого" продавца (появится в системе как tg_id)
            seller_user = User(tg_id=int(seller_tg_id), username="", full_name="", created_at=now_ts())
            s.add(seller_user)
            s.commit()
            s.refresh(seller_user)

            b = Balance(user_id=seller_user.id, amount=0)
            s.add(b)
            s.commit()

        buyer_bal = get_balance_amount(s, buyer_uid)
        if buyer_bal < int(amount):
            raise HTTPException(status_code=400, detail="Not enough balance")

        # списываем у покупателя сразу (как ты описал: сначала оплатил, потом получил данные)
        add_balance(s, buyer_uid, -int(amount))

        access_code = f"{buyer_uid}-{seller_user.id}-{now_ts()}"

        d = Deal(
            buyer_id=buyer_uid,
            seller_id=seller_user.id,
            title=title[:200],
            amount=int(amount),
            status=DealStatus.PAID,
            created_at=now_ts(),
            access_code=access_code,
        )
        s.add(d)
        s.commit()
        s.refresh(d)

        deal_url = ""
        if APP_BASE_URL:
            deal_url = f"{APP_BASE_URL}/#deal={d.id}&code={access_code}"

        return {"ok": True, "id": d.id, "deal_url": deal_url, "access_code": access_code}


@app.post("/api/deal/confirm")
def api_deal_confirm(
    deal_id: int,
    code: str,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    code = (code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code required")

    with SessionLocal() as s:
        uid = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")

        if d.buyer_id != uid:
            raise HTTPException(status_code=403, detail="only buyer can confirm")

        if d.access_code != code:
            raise HTTPException(status_code=403, detail="bad code")

        if d.status != DealStatus.PAID:
            raise HTTPException(status_code=400, detail="deal is not in PAID status")

        # комиссия
        fee = int(int(d.amount) * (COMMISSION_PCT / 100.0)) if COMMISSION_PCT > 0 else 0
        payout = int(d.amount) - fee

        # переводим продавцу
        add_balance(s, d.seller_id, payout)

        d.status = DealStatus.COMPLETED
        s.commit()

        return {"ok": True, "fee": fee, "payout": payout}


# -------------------------
# Telegram bot
# -------------------------
def webapp_keyboard() -> InlineKeyboardMarkup:
    url = APP_BASE_URL or "https://example.com"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Открыть гарант", web_app=WebAppInfo(url=url))],
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "💎 <b>Гарант</b>\n\n"
        "Открывай мини-апп и работай со сделками.\n",
        reply_markup=webapp_keyboard(),
    )


@dp.message(Command("workbr"))
async def cmd_workbr(message: Message):
    # админ выдаёт баланс: /workbr 123456789 500
    if message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Используй: <code>/workbr TG_ID AMOUNT</code>\nПример: <code>/workbr 123456789 500</code>")
        return

    try:
        tg_id = int(parts[1])
        amount = int(parts[2])
    except:
        await message.answer("Неверный формат. Пример: <code>/workbr 123456789 500</code>")
        return

    with SessionLocal() as s:
        u = s.execute(select(User).where(User.tg_id == tg_id)).scalar_one_or_none()
        if not u:
            u = User(tg_id=tg_id, username="", full_name="", created_at=now_ts())
            s.add(u)
            s.commit()
            s.refresh(u)
            b = Balance(user_id=u.id, amount=0)
            s.add(b)
            s.commit()

        add_balance(s, u.id, amount)
        new_bal = get_balance_amount(s, u.id)

    await message.answer(f"✅ Баланс выдан.\nTG_ID: <code>{tg_id}</code>\n+{amount}\nТекущий баланс: <b>{new_bal}</b>")


async def run_bot():
    await dp.start_polling(bot)


@app.on_event("startup")
async def on_startup():
    init_db()
    # запускаем бота в фоне вместе с FastAPI
    asyncio.create_task(run_bot())


@app.get("/health")
def health():
    return JSONResponse({"ok": True})


# ✅ Раздача фронта (index.html, styles.css, app.js) из корня репо
# ВАЖНО: держим ЭТО ПОСЛЕ API роутов, чтобы /api/* работал.
WEB_DIR = os.path.dirname(__file__)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
