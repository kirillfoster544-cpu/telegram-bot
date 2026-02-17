import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Header, HTTPException, Body
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, Message

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


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "7489815425").strip())

APP_BASE_URL = (os.getenv("APP_BASE_URL", "").strip() or "").rstrip("/")
COMMISSION_PCT = float((os.getenv("COMMISSION_PCT", "0").strip() or "0"))

if not APP_BASE_URL:
    public_domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip() or "").strip()
    if public_domain:
        APP_BASE_URL = f"https://{public_domain}".rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

app = FastAPI(title="GUARANT Mini-App + Bot")


def now_ts() -> int:
    return int(datetime.utcnow().timestamp())


def get_init_data(init_data: Optional[str], x_tg_init_data: Optional[str]) -> str:
    data = (init_data or "").strip() or (x_tg_init_data or "").strip()
    if not data:
        raise HTTPException(status_code=401, detail="Missing init_data")
    return data


def require_user(init_data: str) -> Dict[str, Any]:
    ok, payload_or_err = verify_telegram_init_data(init_data=init_data, bot_token=BOT_TOKEN)
    if not ok:
        raise HTTPException(status_code=401, detail=f"Bad init_data: {payload_or_err}")
    return payload_or_err


def ensure_user(db_sess, tg_user: Dict[str, Any]) -> User:
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

    return u


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


def deal_fee(amount: int) -> int:
    if COMMISSION_PCT <= 0:
        return 0
    return int(round(amount * (COMMISSION_PCT / 100.0)))


def deal_role(u_id: int, d: Deal) -> str:
    if d.seller_id == u_id:
        return "seller"
    if d.buyer_id == u_id:
        return "buyer"
    if d.creator_id == u_id:
        return d.creator_role or "buyer"
    return "viewer"


def deal_permissions(u_id: int, d: Deal) -> Dict[str, bool]:
    is_buyer = d.buyer_id == u_id
    is_seller = d.seller_id == u_id

    can_join = (d.status == DealStatus.CREATED) and (u_id not in [d.buyer_id, d.seller_id]) and (d.buyer_id is None or d.seller_id is None)
    can_pay = (d.status == DealStatus.CREATED) and is_buyer and (d.buyer_id is not None and d.seller_id is not None)
    can_deliver = (d.status == DealStatus.FUNDED) and is_seller
    can_confirm = (d.status == DealStatus.DELIVERED) and is_buyer
    can_dispute = (d.status in [DealStatus.FUNDED, DealStatus.DELIVERED]) and (is_buyer or is_seller)

    return {
        "can_join": can_join,
        "can_pay": can_pay,
        "can_deliver": can_deliver,
        "can_confirm": can_confirm,
        "can_dispute": can_dispute,
    }


def deal_to_list_item(u_id: int, d: Deal) -> Dict[str, Any]:
    return {
        "id": d.id,
        "description": d.description,
        "amount": int(d.amount),
        "status": d.status,
        "role": deal_role(u_id, d),
    }


def deal_to_detail(u_id: int, d: Deal) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "ok": True,
        "id": d.id,
        "public_code": d.public_code,
        "description": d.description,
        "amount": int(d.amount),
        "fee": int(d.fee or 0),
        "status": d.status,
        "role": deal_role(u_id, d),
    }
    data.update(deal_permissions(u_id, d))
    return data


# -------------------------
# API
# -------------------------
@app.get("/api/me")
def api_me(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        bal = get_balance_amount(s, u.id)
        return {
            "ok": True,
            "id": int(u.tg_id),          # TG ID (как ты просил)
            "user_id": int(u.id),        # внутренний ID
            "username": (u.username or ""),
            "full_name": (u.full_name or ""),
            "balance": bal,
        }


@app.get("/api/balance")
def api_balance(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]
    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        return {"ok": True, "balance": get_balance_amount(s, u.id)}


@app.get("/api/topups")
def api_topups(init_data: Optional[str] = None, x_tg_init_data: Optional[str] = Header(default=None)):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        rows = s.execute(
            select(Topup).where(Topup.user_id == u.id).order_by(Topup.id.desc()).limit(50)
        ).scalars().all()

        return {
            "ok": True,
            "items": [{
                "id": r.id,
                "amount": int(r.amount),
                "status": r.status,
                "created_at": int(r.created_at),
                "note": r.note or "",
            } for r in rows]
        }


@app.post("/api/topups")
def api_topup_create(
    body: Dict[str, Any] = Body(...),
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    amount = int(body.get("amount") or 0)
    note = (body.get("note") or "").strip()

    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        t = Topup(user_id=u.id, amount=amount, note=note[:500], status=TopupStatus.PENDING, created_at=now_ts())
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
        u = ensure_user(s, tg_user)
        rows = s.execute(
            select(Deal).where(
                (Deal.buyer_id == u.id) | (Deal.seller_id == u.id) | (Deal.creator_id == u.id)
            ).order_by(Deal.id.desc()).limit(50)
        ).scalars().all()

        return {"ok": True, "items": [deal_to_list_item(u.id, d) for d in rows]}


@app.post("/api/deals")
def api_deal_create(
    body: Dict[str, Any] = Body(...),
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    description = (body.get("description") or "").strip()
    amount = int(body.get("amount") or 0)
    role = (body.get("role") or "buyer").strip().lower()

    if not description:
        raise HTTPException(status_code=400, detail="description required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    if role not in ("buyer", "seller"):
        raise HTTPException(status_code=400, detail="role must be buyer/seller")

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        public_code = Deal.gen_public_code()
        fee = deal_fee(amount)

        d = Deal(
            public_code=public_code,
            description=description[:120],
            amount=amount,
            fee=fee,
            creator_id=u.id,
            creator_role=role,
            status=DealStatus.CREATED,
            created_at=now_ts(),
        )
        if role == "buyer":
            d.buyer_id = u.id
        else:
            d.seller_id = u.id

        s.add(d)
        s.commit()
        s.refresh(d)
        return deal_to_detail(u.id, d)


@app.get("/api/deals/{deal_id}")
def api_deal_get(
    deal_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if u.id not in [d.buyer_id, d.seller_id, d.creator_id]:
            raise HTTPException(status_code=403, detail="no access")
        return deal_to_detail(u.id, d)


@app.get("/api/deals/by/{code}")
def api_deal_by_code(
    code: str,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    code = (code or "").strip()
    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.public_code == code)).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        return deal_to_detail(u.id, d)


@app.post("/api/deals/{code}/join")
def api_deal_join(
    code: str,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    code = (code or "").strip()
    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.public_code == code)).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if d.status != DealStatus.CREATED:
            return deal_to_detail(u.id, d)

        if u.id in [d.buyer_id, d.seller_id]:
            return deal_to_detail(u.id, d)

        if d.buyer_id is None:
            d.buyer_id = u.id
        elif d.seller_id is None:
            d.seller_id = u.id

        s.commit()
        return deal_to_detail(u.id, d)


@app.post("/api/deals/{deal_id}/pay")
def api_deal_pay(
    deal_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if d.status != DealStatus.CREATED:
            return deal_to_detail(u.id, d)
        if d.buyer_id != u.id:
            raise HTTPException(status_code=403, detail="only buyer can pay")
        if not d.seller_id:
            raise HTTPException(status_code=400, detail="seller not joined yet")

        bal = get_balance_amount(s, u.id)
        if bal < int(d.amount):
            raise HTTPException(status_code=400, detail="Not enough balance")

        add_balance(s, u.id, -int(d.amount))
        d.status = DealStatus.FUNDED
        s.commit()
        return deal_to_detail(u.id, d)


@app.post("/api/deals/{deal_id}/deliver")
def api_deal_deliver(
    deal_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if d.status != DealStatus.FUNDED:
            return deal_to_detail(u.id, d)
        if d.seller_id != u.id:
            raise HTTPException(status_code=403, detail="only seller can deliver")

        d.status = DealStatus.DELIVERED
        s.commit()
        return deal_to_detail(u.id, d)


@app.post("/api/deals/{deal_id}/confirm")
def api_deal_confirm(
    deal_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if d.status != DealStatus.DELIVERED:
            return deal_to_detail(u.id, d)
        if d.buyer_id != u.id:
            raise HTTPException(status_code=403, detail="only buyer can confirm")

        payout = int(d.amount) - int(d.fee or 0)
        if payout < 0:
            payout = 0
        add_balance(s, int(d.seller_id), payout)

        d.status = DealStatus.RELEASED
        s.commit()
        return deal_to_detail(u.id, d)


@app.post("/api/deals/{deal_id}/dispute")
def api_deal_dispute(
    deal_id: int,
    init_data: Optional[str] = None,
    x_tg_init_data: Optional[str] = Header(default=None),
):
    init_data = get_init_data(init_data, x_tg_init_data)
    payload = require_user(init_data)
    tg_user = payload["user"]

    with SessionLocal() as s:
        u = ensure_user(s, tg_user)
        d = s.execute(select(Deal).where(Deal.id == int(deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="deal not found")
        if d.status not in [DealStatus.FUNDED, DealStatus.DELIVERED]:
            return deal_to_detail(u.id, d)
        if u.id not in [d.buyer_id, d.seller_id]:
            raise HTTPException(status_code=403, detail="no access")

        d.status = DealStatus.DISPUTE
        s.commit()
        return deal_to_detail(u.id, d)


# -------------------------
# Telegram bot
# -------------------------
def webapp_keyboard() -> InlineKeyboardMarkup:
    url = APP_BASE_URL or "https://example.com"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Гарант", web_app=WebAppInfo(url=url))],
    ])


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "💎 <b>Гарант</b>

"
        "Открывай мини-апп и работай со сделками.",
        reply_markup=webapp_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Команды:
"
        "/start — открыть мини-апп
"
        "/help — помощь
"
        "/workbr TG_ID AMOUNT — (только админ) выдать баланс"
    )


@dp.message(Command("workbr"))
async def cmd_workbr(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Используй: <code>/workbr TG_ID AMOUNT</code>")
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
            s.add(Balance(user_id=u.id, amount=0))
            s.commit()

        add_balance(s, u.id, amount)
        new_bal = get_balance_amount(s, u.id)

    await message.answer(
        f"✅ Баланс выдан.\nTG_ID: <code>{tg_id}</code>\n+{amount}\nТекущий баланс: <b>{new_bal}</b>"
    )


async def run_bot():
    await dp.start_polling(bot)


@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(run_bot())


@app.get("/health")
def health():
    return JSONResponse({"ok": True})


WEB_DIR = os.path.dirname(__file__)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
