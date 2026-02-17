import os
import json
import random
import string
from typing import Optional, List

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionLocal, init_db, User, Balance, Topup, Deal, TopupStatus, DealStatus
from .telegram_auth import verify_init_data

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
COMMISSION_PCT = int(os.getenv("COMMISSION_PCT", "5"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is required")
if not APP_BASE_URL:
    # можно оставить пустым и кнопка будет не работать, но лучше указать
    APP_BASE_URL = "https://example.com"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

def _code(n=10):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))

async def get_session() -> AsyncSession:
    return SessionLocal()

async def get_or_create_user(tg_user: dict) -> User:
    uid = int(tg_user["id"])
    username = tg_user.get("username", "") or ""
    full_name = " ".join([tg_user.get("first_name","") or "", tg_user.get("last_name","") or ""]).strip()

    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.id==uid))).scalar_one_or_none()
        if not u:
            u = User(id=uid, username=username, full_name=full_name)
            s.add(u)
            s.add(Balance(user_id=uid, amount=0))
            await s.commit()
        else:
            changed = False
            if u.username != username:
                u.username = username; changed = True
            if full_name and u.full_name != full_name:
                u.full_name = full_name; changed = True
            if changed:
                await s.commit()
        return u

async def get_balance(uid: int) -> int:
    async with SessionLocal() as s:
        b = (await s.execute(select(Balance).where(Balance.user_id==uid))).scalar_one()
        return int(b.amount)

async def add_balance(uid: int, delta: int):
    async with SessionLocal() as s:
        b = (await s.execute(select(Balance).where(Balance.user_id==uid))).scalar_one()
        b.amount = int(b.amount) + int(delta)
        if b.amount < 0:
            b.amount = 0
        await s.commit()

def fee_for(amount: int) -> int:
    return max(0, round(amount * COMMISSION_PCT / 100))

# -------------------
# FastAPI
# -------------------
app = FastAPI(title="Guarant MVP")

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "web"), html=True), name="web")

def auth_user(x_telegram_initdata: str) -> dict:
    try:
        tg_user = verify_init_data(x_telegram_initdata, BOT_TOKEN)
        return tg_user
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

class DealCreateIn(BaseModel):
    description: str = Field(min_length=3, max_length=120)
    amount: int = Field(ge=1, le=10_000_000)
    role: str = Field(pattern="^(seller|buyer)$")

class TopupCreateIn(BaseModel):
    amount: int = Field(ge=1, le=10_000_000)
    note: str = Field(default="", max_length=120)

class ListOut(BaseModel):
    items: list

@app.get("/api/me")
async def api_me(x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    await get_or_create_user(tg_user)
    return {"id": int(tg_user["id"]), "username": tg_user.get("username","")}

@app.get("/api/balance")
async def api_balance(x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    await get_or_create_user(tg_user)
    return {"balance": await get_balance(int(tg_user["id"]))}

@app.post("/api/topups")
async def api_topup_create(payload: TopupCreateIn, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)

    async with SessionLocal() as s:
        t = Topup(user_id=u.id, amount=payload.amount, note=payload.note, status=TopupStatus.PENDING)
        s.add(t)
        await s.commit()
        await s.refresh(t)

    # notify admin
    try:
        await bot.send_message(
            ADMIN_ID,
            "💳 <b>Заявка на пополнение</b>\n"
            f"ID: <code>{t.id}</code>\n"
            f"Юзер: <code>{u.id}</code> @{u.username}\n"
            f"Сумма: <b>{t.amount} ₽</b>\n"
            f"Комментарий: {t.note or '—'}\n\n"
            f"Подтвердить: /approve {t.id}\n"
            f"Отклонить: /reject {t.id}"
        )
    except Exception:
        pass

    return {"ok": True, "id": t.id}

@app.get("/api/topups")
async def api_topups(x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Topup).where(Topup.user_id==u.id).order_by(Topup.id.desc()).limit(50))).scalars().all()
        return {"items": [{"id":r.id, "amount":r.amount, "note":r.note, "status":r.status.value} for r in rows]}

@app.post("/api/deals")
async def api_deal_create(payload: DealCreateIn, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)

    public_code = _code(10)
    amount = int(payload.amount)
    fee = fee_for(amount)

    async with SessionLocal() as s:
        d = Deal(
            public_code=public_code,
            description=payload.description,
            amount=amount,
            fee=fee,
            status=DealStatus.CREATED,
            created_by=u.id,
            seller_id=u.id if payload.role=="seller" else None,
            buyer_id=u.id if payload.role=="buyer" else None,
        )
        s.add(d)
        await s.commit()
        await s.refresh(d)

    return await api_deal_get(d.id, x_telegram_initdata=x_telegram_initdata)

@app.get("/api/deals")
async def api_deals(x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Deal).where((Deal.seller_id==u.id) | (Deal.buyer_id==u.id)).order_by(Deal.id.desc()).limit(50)
        )).scalars().all()
        items = []
        for d in rows:
            role = "seller" if d.seller_id==u.id else "buyer"
            items.append({"id":d.id,"public_code":d.public_code,"amount":d.amount,"fee":d.fee,"description":d.description,"status":d.status.value,"role":role})
        return {"items": items}

@app.get("/api/deals/{deal_id}")
async def api_deal_get(deal_id: int, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if u.id not in [d.seller_id, d.buyer_id]:
            # allow creator to view before join
            if u.id != d.created_by:
                raise HTTPException(403, "Forbidden")
        return _deal_view(d, u.id)

@app.get("/api/deals/by/{public_code}")
async def api_deal_by_code(public_code: str, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.public_code==public_code))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        return _deal_view(d, u.id)

def _deal_view(d: Deal, uid: int):
    role = "seller" if d.seller_id==uid else ("buyer" if d.buyer_id==uid else "viewer")
    can_join = (d.status==DealStatus.CREATED) and (uid not in [d.seller_id, d.buyer_id])
    can_pay = (d.status==DealStatus.CREATED) and (d.buyer_id==uid) and (d.seller_id is not None)
    # if created as buyer first, pay available after seller joins
    can_deliver = (d.status==DealStatus.FUNDED) and (d.seller_id==uid)
    can_confirm = (d.status==DealStatus.DELIVERED) and (d.buyer_id==uid)
    can_dispute = (d.status==DealStatus.DELIVERED) and (d.buyer_id==uid)

    return {
        "id": d.id,
        "public_code": d.public_code,
        "description": d.description,
        "amount": d.amount,
        "fee": d.fee,
        "status": d.status.value,
        "role": role,
        "seller_id": d.seller_id,
        "buyer_id": d.buyer_id,
        "can_join": can_join,
        "can_pay": can_pay,
        "can_deliver": can_deliver,
        "can_confirm": can_confirm,
        "can_dispute": can_dispute,
    }

@app.post("/api/deals/{public_code}/join")
async def api_deal_join(public_code: str, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.public_code==public_code))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if d.status != DealStatus.CREATED:
            raise HTTPException(400, "Deal not joinable")
        if u.id in [d.seller_id, d.buyer_id]:
            return _deal_view(d, u.id)

        # choose missing side:
        if d.seller_id is None:
            d.seller_id = u.id
        elif d.buyer_id is None:
            d.buyer_id = u.id
        else:
            raise HTTPException(400, "Deal already has two sides")
        await s.commit()
        await s.refresh(d)
        return _deal_view(d, u.id)

@app.post("/api/deals/{deal_id}/pay")
async def api_deal_pay(deal_id: int, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)

    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if d.status != DealStatus.CREATED:
            raise HTTPException(400, "Wrong status")
        if d.buyer_id != u.id:
            raise HTTPException(403, "Only buyer can pay")
        if not d.seller_id:
            raise HTTPException(400, "Seller not joined yet")

        b = (await s.execute(select(Balance).where(Balance.user_id==u.id))).scalar_one()
        if b.amount < d.amount:
            raise HTTPException(400, "Недостаточно баланса")
        b.amount -= d.amount
        d.status = DealStatus.FUNDED
        await s.commit()
        await s.refresh(d)

    return _deal_view(d, u.id)

@app.post("/api/deals/{deal_id}/deliver")
async def api_deal_deliver(deal_id: int, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if d.status != DealStatus.FUNDED:
            raise HTTPException(400, "Wrong status")
        if d.seller_id != u.id:
            raise HTTPException(403, "Only seller can deliver")
        d.status = DealStatus.DELIVERED
        await s.commit()
        await s.refresh(d)
    return _deal_view(d, u.id)

@app.post("/api/deals/{deal_id}/confirm")
async def api_deal_confirm(deal_id: int, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)

    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if d.status != DealStatus.DELIVERED:
            raise HTTPException(400, "Wrong status")
        if d.buyer_id != u.id:
            raise HTTPException(403, "Only buyer can confirm")

        seller_bal = (await s.execute(select(Balance).where(Balance.user_id==d.seller_id))).scalar_one()
        admin_bal = (await s.execute(select(Balance).where(Balance.user_id==ADMIN_ID))).scalar_one_or_none()
        if not admin_bal:
            # create admin as user
            s.add(User(id=ADMIN_ID, username="admin", full_name="Admin"))
            s.add(Balance(user_id=ADMIN_ID, amount=0))
            await s.commit()
            admin_bal = (await s.execute(select(Balance).where(Balance.user_id==ADMIN_ID))).scalar_one()

        seller_bal.amount += (d.amount - d.fee)
        admin_bal.amount += d.fee
        d.status = DealStatus.RELEASED
        await s.commit()
        await s.refresh(d)

    return _deal_view(d, u.id)

@app.post("/api/deals/{deal_id}/dispute")
async def api_deal_dispute(deal_id: int, x_telegram_initdata: str = Header(default="", alias="X-Telegram-InitData")):
    tg_user = auth_user(x_telegram_initdata)
    u = await get_or_create_user(tg_user)
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==deal_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        if d.status != DealStatus.DELIVERED:
            raise HTTPException(400, "Wrong status")
        if d.buyer_id != u.id:
            raise HTTPException(403, "Only buyer can dispute")
        d.status = DealStatus.DISPUTE
        d.dispute_opened = True
        await s.commit()
        await s.refresh(d)

    # notify admin
    try:
        await bot.send_message(
            ADMIN_ID,
            "⚠️ <b>Спор по сделке</b>\n"
            f"Сделка: <code>#{d.id}</code>\n"
            f"Сумма: <b>{d.amount} ₽</b>\n"
            f"Покупатель: <code>{d.buyer_id}</code>\n"
            f"Продавец: <code>{d.seller_id}</code>\n\n"
            f"Решение: /release {d.id} или /refund {d.id}"
        )
    except Exception:
        pass

    return _deal_view(d, u.id)

# -------------------
# Bot UI + Admin
# -------------------
def kb_open_app() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть мини‑апп", url=APP_BASE_URL)]
    ])

@dp.message(CommandStart())
async def cmd_start(m: Message):
    # create user + balance
    await get_or_create_user({"id": m.from_user.id, "username": m.from_user.username or "", "first_name": m.from_user.first_name or "", "last_name": m.from_user.last_name or ""})
    await m.answer(
        "GUARANT • mini‑app\n\n"
        "Открывай приложение и создавай сделки.\n",
        reply_markup=kb_open_app()
    )

@dp.message(Command("bal"))
async def cmd_bal(m: Message):
    parts = (m.text or "").split()
    uid = m.from_user.id if len(parts) == 1 else int(parts[1])
    b = await get_balance(uid)
    await m.answer(f"Баланс <code>{uid}</code>: <b>{b} ₽</b>")

def _is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

@dp.message(Command("topups"))
async def cmd_topups(m: Message):
    if not _is_admin(m.from_user.id):
        return
    async with SessionLocal() as s:
        rows = (await s.execute(select(Topup).where(Topup.status==TopupStatus.PENDING).order_by(Topup.id.asc()).limit(20))).scalars().all()
    if not rows:
        await m.answer("Нет заявок.")
        return
    lines = ["💳 <b>Заявки (PENDING)</b>:"]
    for r in rows:
        lines.append(f"#{r.id} • <code>{r.user_id}</code> • <b>{r.amount} ₽</b> • {r.note or '—'}")
    lines.append("\nПодтвердить: /approve <id>\nОтклонить: /reject <id>")
    await m.answer("\n".join(lines))

@dp.message(Command("approve"))
async def cmd_approve(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Формат: /approve <id>")
        return
    tid = int(parts[1])
    async with SessionLocal() as s:
        t = (await s.execute(select(Topup).where(Topup.id==tid))).scalar_one_or_none()
        if not t or t.status != TopupStatus.PENDING:
            await m.answer("Не найдено или уже обработано.")
            return
        t.status = TopupStatus.APPROVED
        b = (await s.execute(select(Balance).where(Balance.user_id==t.user_id))).scalar_one()
        b.amount += t.amount
        await s.commit()
    try:
        await bot.send_message(t.user_id, f"✅ Пополнение подтверждено: <b>+{t.amount} ₽</b>")
    except Exception:
        pass
    await m.answer(f"✅ OK: #{tid} → <code>{t.user_id}</code> +{t.amount} ₽")

@dp.message(Command("reject"))
async def cmd_reject(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Формат: /reject <id>")
        return
    tid = int(parts[1])
    async with SessionLocal() as s:
        t = (await s.execute(select(Topup).where(Topup.id==tid))).scalar_one_or_none()
        if not t or t.status != TopupStatus.PENDING:
            await m.answer("Не найдено или уже обработано.")
            return
        t.status = TopupStatus.REJECTED
        await s.commit()
    try:
        await bot.send_message(t.user_id, f"❌ Заявка отклонена: <b>{t.amount} ₽</b>")
    except Exception:
        pass
    await m.answer(f"❌ OK: #{tid} отклонено")

@dp.message(Command("grant"))
async def cmd_grant(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 3:
        await m.answer("Формат: /grant <user_id> <amount>")
        return
    uid = int(parts[1]); amt = int(parts[2])
    await add_balance(uid, amt)
    await m.answer(f"✅ Начислено: <code>{uid}</code> +{amt} ₽")

@dp.message(Command("take"))
async def cmd_take(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 3:
        await m.answer("Формат: /take <user_id> <amount>")
        return
    uid = int(parts[1]); amt = int(parts[2])
    await add_balance(uid, -amt)
    await m.answer(f"✅ Списано: <code>{uid}</code> -{amt} ₽")

@dp.message(Command("release"))
async def cmd_release(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Формат: /release <deal_id>")
        return
    did = int(parts[1])
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==did))).scalar_one_or_none()
        if not d or d.status != DealStatus.DISPUTE:
            await m.answer("Сделка не найдена или статус не DISPUTE.")
            return
        seller_bal = (await s.execute(select(Balance).where(Balance.user_id==d.seller_id))).scalar_one()
        admin_bal = (await s.execute(select(Balance).where(Balance.user_id==ADMIN_ID))).scalar_one()
        seller_bal.amount += (d.amount - d.fee)
        admin_bal.amount += d.fee
        d.status = DealStatus.RELEASED
        await s.commit()
    await m.answer(f"✅ Release: сделка #{did} → продавцу начислено")

@dp.message(Command("refund"))
async def cmd_refund(m: Message):
    if not _is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Формат: /refund <deal_id>")
        return
    did = int(parts[1])
    async with SessionLocal() as s:
        d = (await s.execute(select(Deal).where(Deal.id==did))).scalar_one_or_none()
        if not d or d.status != DealStatus.DISPUTE:
            await m.answer("Сделка не найдена или статус не DISPUTE.")
            return
        buyer_bal = (await s.execute(select(Balance).where(Balance.user_id==d.buyer_id))).scalar_one()
        buyer_bal.amount += d.amount
        d.status = DealStatus.REFUNDED
        await s.commit()
    await m.answer(f"✅ Refund: сделка #{did} → покупателю возврат")

# -------------------
# Lifespan: init db + start bot polling
# -------------------
@app.on_event("startup")
async def _startup():
    await init_db()
    # start bot polling in background
    import asyncio
    asyncio.create_task(dp.start_polling(bot))
