import os
import asyncio
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from . import db
from .exporter import export_one

TOKEN_FILE = "/opt/services/paybot/secrets/telegram_token.txt"

PAYMENT_LABELS = {"cash": "Нал", "bank": "Безнал", "bizcard": "Бизнес-карта"}
BUDGET_LABELS = {
    "aho": "АХО",
    "mbp": "МБП",
    "kitchen": "Закупка кухня",
    "bar": "Закупка бар",
    "tech": "Тех часть",
    "fot": "ФОТ",
    "marketing": "Маркетинг",
    "other": "Другое",
}

def read_token():
    with open(TOKEN_FILE, "r") as f:
        return f.read().strip()

def admins() -> set[int]:
    raw = os.environ.get("ADMINS", "").strip()
    if not raw:
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}

def is_admin(user_id: int) -> bool:
    return user_id in admins()

class NewRequest(StatesGroup):
    title = State()
    amount = State()
    paytype = State()
    budget = State()
    attachment = State()

class AdminDecision(StatesGroup):
    comment = State()

class AdminEdit(StatesGroup):
    choose_field = State()
    edit_title = State()
    edit_amount = State()
    edit_paytype = State()
    edit_budget = State()
    edit_note = State()

def nice_amount(x: float) -> str:
    s = f"{x:,.2f}".replace(",", " ").replace(".00", "")
    return s

def build_admin_kb(req_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Согласовать", callback_data=f"decide:{req_id}:approved")
    kb.button(text="❌ Отклонить", callback_data=f"decide:{req_id}:rejected")
    kb.button(text="✏️ Доработать", callback_data=f"edit:{req_id}")
    kb.adjust(2,1)
    return kb.as_markup()

def build_pay_kb(prefix="pay:"):
    kb = InlineKeyboardBuilder()
    kb.button(text="Нал", callback_data=f"{prefix}cash")
    kb.button(text="Безнал", callback_data=f"{prefix}bank")
    kb.button(text="Бизнес-карта", callback_data=f"{prefix}bizcard")
    kb.adjust(1)
    return kb.as_markup()

def build_budget_kb(prefix="bud:"):
    kb = InlineKeyboardBuilder()
    kb.button(text="АХО", callback_data=f"{prefix}aho")
    kb.button(text="МБП", callback_data=f"{prefix}mbp")
    kb.button(text="Закупка кухня", callback_data=f"{prefix}kitchen")
    kb.button(text="Закупка бар", callback_data=f"{prefix}bar")
    kb.button(text="Тех часть", callback_data=f"{prefix}tech")
    kb.button(text="ФОТ", callback_data=f"{prefix}fot")
    kb.button(text="Маркетинг", callback_data=f"{prefix}marketing")
    kb.button(text="Другое", callback_data=f"{prefix}other")
    kb.adjust(2,2,2,2)
    return kb.as_markup()

def build_edit_menu(req_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Назначение", callback_data=f"editfield:{req_id}:title")
    kb.button(text="💰 Сумма", callback_data=f"editfield:{req_id}:amount")
    kb.button(text="💳 Оплата", callback_data=f"editfield:{req_id}:payment")
    kb.button(text="📁 Статья", callback_data=f"editfield:{req_id}:budget")
    kb.button(text="📨 Сообщение пользователю", callback_data=f"editfield:{req_id}:note")
    kb.adjust(2,2,1)
    return kb.as_markup()

async def notify_admins(bot: Bot, req_id: int):
    row = db.get_request(req_id)
    if not row:
        return

    pay = PAYMENT_LABELS.get((row["payment_type"] or "").strip(), row["payment_type"] or "")
    bud = BUDGET_LABELS.get((row["budget_category"] or "").strip(), row["budget_category"] or "")
    text = (
        f"Заявка №{row['id']}\n"
        f"Автор: {row['author_name']} ({row['author_tg_id']})\n"
        f"Сумма: {nice_amount(float(row['amount']))}\n"
        f"Оплата: {pay}\n"
        f"Статья: {bud}\n"
        f"За что платим: {row['title']}\n"
        f"Статус: {row['status']}"
    )

    for aid in admins():
        try:
            await bot.send_message(aid, text, reply_markup=build_admin_kb(req_id))
            file_id = row["attachment_file_id"]
            kind = (row["attachment_kind"] or "").strip()
            if file_id:
                caption = f"Приложение к заявке №{row['id']}"
                if kind == "photo":
                    await bot.send_photo(aid, photo=file_id, caption=caption)
                else:
                    await bot.send_document(aid, document=file_id, caption=caption)
        except Exception:
            pass

async def main():
    bot = Bot(token=read_token())
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(Command("start"))
    async def start(msg: Message):
        await msg.answer("Paybot.\n/new — создать заявку.\n/whoami — показать tg_id.")

    @dp.message(Command("whoami"))
    async def whoami(msg: Message):
        await msg.answer(f"Ваш tg_id: {msg.from_user.id}")

    # ---------- USER FLOW ----------
    @dp.message(Command("new"))
    async def new(msg: Message, state: FSMContext):
        await state.clear()
        await state.set_state(NewRequest.title)
        await msg.answer("Ок. Напиши: за что платим? (текст)")

    @dp.message(NewRequest.title)
    async def new_title(msg: Message, state: FSMContext):
        title = (msg.text or "").strip()
        if not title:
            await msg.answer("Текст пустой. Напиши нормально: за что платим?")
            return
        await state.update_data(title=title)
        await state.set_state(NewRequest.amount)
        await msg.answer("Сумма? (только число, можно с точкой/запятой)")

    @dp.message(NewRequest.amount)
    async def new_amount(msg: Message, state: FSMContext):
        raw = (msg.text or "").strip().replace(" ", "").replace(",", ".")
        try:
            amount = float(raw)
        except Exception:
            await msg.answer("Не понял сумму. Пример: 12400 или 12400.50")
            return
        if amount <= 0:
            await msg.answer("Сумма должна быть больше нуля.")
            return
        await state.update_data(amount=amount)
        await state.set_state(NewRequest.paytype)
        await msg.answer("Как оплачиваем? (обязательно выбери)", reply_markup=build_pay_kb(prefix="paynew:"))

    @dp.callback_query(NewRequest.paytype, F.data.startswith("paynew:"))
    async def choose_pay(cb: CallbackQuery, state: FSMContext):
        pay = cb.data.split(":", 1)[1].strip()
        if pay not in PAYMENT_LABELS:
            await cb.answer("Не понял вариант.", show_alert=True)
            return
        await state.update_data(payment_type=pay)
        await state.set_state(NewRequest.budget)
        await cb.answer("Ок")
        await cb.message.answer("Статья бюджета? (обязательно выбери)", reply_markup=build_budget_kb(prefix="budnew:"))

    @dp.message(NewRequest.paytype)
    async def pay_guard(msg: Message):
        await msg.answer("Выбери вариант оплаты кнопкой.")

    @dp.callback_query(NewRequest.budget, F.data.startswith("budnew:"))
    async def choose_budget(cb: CallbackQuery, state: FSMContext):
        bud = cb.data.split(":", 1)[1].strip()
        if bud not in BUDGET_LABELS:
            await cb.answer("Не понял статью.", show_alert=True)
            return
        await state.update_data(budget_category=bud)
        await state.set_state(NewRequest.attachment)
        await cb.answer("Ок")
        await cb.message.answer("Файл/фото счёта приложишь? Если нет — напиши: нет")

    @dp.message(NewRequest.budget)
    async def budget_guard(msg: Message):
        await msg.answer("Выбери статью бюджета кнопкой.")

    @dp.message(NewRequest.attachment)
    async def new_attachment(msg: Message, state: FSMContext):
        data = await state.get_data()
        title = data.get("title")
        amount = float(data.get("amount", 0))
        payment_type = data.get("payment_type")
        budget_category = data.get("budget_category")

        if payment_type not in PAYMENT_LABELS or budget_category not in BUDGET_LABELS:
            await msg.answer("Сначала выбери оплату и статью кнопками.")
            await state.set_state(NewRequest.paytype)
            await msg.answer("Как оплачиваем?", reply_markup=build_pay_kb(prefix="paynew:"))
            return

        attachment_file_id: Optional[str] = None
        attachment_kind: Optional[str] = None

        if msg.document:
            attachment_file_id = msg.document.file_id
            attachment_kind = "document"
        elif msg.photo:
            attachment_file_id = msg.photo[-1].file_id
            attachment_kind = "photo"
        else:
            t = (msg.text or "").strip().lower()
            if t not in ("нет", "no", "-", "неа"):
                await msg.answer("Либо приложи файл/фото, либо напиши: нет")
                return

        req_id = db.create_request(
            author_id=msg.from_user.id,
            author_name=(msg.from_user.full_name or "Без имени"),
            title=title,
            amount=amount,
            payment_type=payment_type,
            budget_category=budget_category,
            attachment_file_id=attachment_file_id,
            attachment_kind=attachment_kind,
        )

        await state.clear()
        await msg.answer(f"Заявка №{req_id} создана и отправлена админам.")
        await notify_admins(bot, req_id)

    # ---------- ADMIN DECISION ----------
    @dp.callback_query(F.data.startswith("decide:"))
    async def decide(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.from_user.id):
            await cb.answer("Не админ.", show_alert=True)
            return

        _, rid, status = cb.data.split(":")
        req_id = int(rid)

        row = db.get_request(req_id)
        if not row:
            await cb.answer("Заявка не найдена.", show_alert=True)
            return
        if row["status"] not in ("new","rework"):
            await cb.answer(f"Уже решено/закрыто: {row['status']}", show_alert=True)
            return

        await state.clear()
        await state.set_state(AdminDecision.comment)
        await state.update_data(pending={"req_id": req_id, "status": status})
        await cb.answer()
        # Блокируем мисклики: пока ждём комментарий решения, убираем кнопки с карточки
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer(
            f"Решение по заявке №{req_id}. Комментарий (необязательно):\n"
            f"— отправь текст\n"
            f"— или '-' без комментария"
        )

    @dp.message(AdminDecision.comment)
    async def decision_comment(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id):
            await msg.answer("Не админ.")
            return

        comment = (msg.text or "").strip()
        if comment == "-":
            comment = ""

        data = await state.get_data()
        pending = data.get("pending") or {}
        req_id = int(pending.get("req_id", 0))
        status = pending.get("status")

        if req_id <= 0 or status not in ("approved","rejected"):
            await state.clear()
            await msg.answer("Контекст потерялся. Нажми кнопку ещё раз.")
            return

        changed = db.set_decision(
            req_id=req_id,
            status=status,
            admin_id=msg.from_user.id,
            admin_name=(msg.from_user.full_name or "Админ"),
            decision_comment=comment,
        )
        await state.clear()

        if changed != 1:
            await msg.answer("Не удалось зафиксировать решение (возможно, статус уже изменился).")
            return

        try:
            export_one()
        except Exception as e:
            await msg.answer(f"Решение сохранено, но экспорт упал: {e}")
            return

        await msg.answer(f"Готово. Заявка №{req_id} → {status} и выгружена в Google Sheets.")

    # ---------- ADMIN EDIT / REWORK ----------
    @dp.callback_query(F.data.startswith("edit:"))
    async def edit(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.from_user.id):
            await cb.answer("Не админ.", show_alert=True)
            return
        req_id = int(cb.data.split(":")[1])

        row = db.get_request(req_id)
        if not row:
            await cb.answer("Заявка не найдена.", show_alert=True)
            return
        if row["status"] not in ("new","rework"):
            await cb.answer(f"Нельзя править при статусе {row['status']}", show_alert=True)
            return

        await state.clear()
        await state.set_state(AdminEdit.choose_field)
        await state.update_data(req_id=req_id)
        await cb.answer()
        await cb.message.answer(f"Доработка заявки №{req_id}: что правим?", reply_markup=build_edit_menu(req_id))

    @dp.callback_query(AdminEdit.choose_field, F.data.startswith("editfield:"))
    async def edit_choose(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.from_user.id):
            await cb.answer("Не админ.", show_alert=True)
            return
        _, rid, field = cb.data.split(":")
        req_id = int(rid)
        await state.update_data(req_id=req_id, edit_field=field)
        await cb.answer()

        if field == "title":
            await state.set_state(AdminEdit.edit_title)
            await cb.message.answer("Введи новое назначение (текст).")
        elif field == "amount":
            await state.set_state(AdminEdit.edit_amount)
            await cb.message.answer("Введи новую сумму (число).")
        elif field == "payment":
            await state.set_state(AdminEdit.edit_paytype)
            await cb.message.answer("Выбери новый тип оплаты:", reply_markup=build_pay_kb(prefix="payedit:"))
        elif field == "budget":
            await state.set_state(AdminEdit.edit_budget)
            await cb.message.answer("Выбери новую статью бюджета:", reply_markup=build_budget_kb(prefix="budedit:"))
        elif field == "note":
            await state.set_state(AdminEdit.edit_note)
            await cb.message.answer("Напиши сообщение пользователю (почему доработка/что исправить). '-' = без текста.")
        else:
            await cb.message.answer("Не понял поле.")

    async def _notify_user(bot: Bot, req_id: int, text: str):
        row = db.get_request(req_id)
        if not row:
            return
        try:
            await bot.send_message(int(row["author_tg_id"]), text)
        except Exception:
            pass

    @dp.message(AdminEdit.edit_title)
    async def edit_title(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id): return
        title = (msg.text or "").strip()
        if not title:
            await msg.answer("Пусто. Введи текст.")
            return
        data = await state.get_data()
        req_id = int(data["req_id"])
        db.update_request_fields(req_id, {"title": title})
        db.set_status(req_id, "rework")
        await _notify_user(bot, req_id, f"По заявке №{req_id} админ поправил назначение. Проверь, ок ли.")
        await msg.answer(f"Ок. Назначение обновлено. Заявка №{req_id} → rework.")
        await state.set_state(AdminEdit.choose_field)
        await msg.answer("Ещё что правим?", reply_markup=build_edit_menu(req_id))

    @dp.message(AdminEdit.edit_amount)
    async def edit_amount(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id): return
        raw = (msg.text or "").strip().replace(" ", "").replace(",", ".")
        try:
            amount = float(raw)
        except Exception:
            await msg.answer("Не понял сумму. Пример: 12400 или 12400.50")
            return
        if amount <= 0:
            await msg.answer("Сумма должна быть > 0")
            return
        data = await state.get_data()
        req_id = int(data["req_id"])
        db.update_request_fields(req_id, {"amount": amount})
        db.set_status(req_id, "rework")
        await _notify_user(bot, req_id, f"По заявке №{req_id} админ поправил сумму на {nice_amount(amount)}. Проверь.")
        await msg.answer(f"Ок. Сумма обновлена. Заявка №{req_id} → rework.")
        await state.set_state(AdminEdit.choose_field)
        await msg.answer("Ещё что правим?", reply_markup=build_edit_menu(req_id))

    @dp.callback_query(AdminEdit.edit_paytype, F.data.startswith("payedit:"))
    async def edit_pay(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.from_user.id):
            await cb.answer("Не админ.", show_alert=True)
            return
        pay = cb.data.split(":",1)[1].strip()
        if pay not in PAYMENT_LABELS:
            await cb.answer("Не понял.", show_alert=True)
            return
        data = await state.get_data()
        req_id = int(data["req_id"])
        db.update_request_fields(req_id, {"payment_type": pay})
        db.set_status(req_id, "rework")
        await _notify_user(bot, req_id, f"По заявке №{req_id} админ поменял тип оплаты на: {PAYMENT_LABELS[pay]}.")
        await cb.answer("Ок")
        await cb.message.answer(f"Ок. Оплата обновлена. Заявка №{req_id} → rework.")
        await state.set_state(AdminEdit.choose_field)
        await cb.message.answer("Ещё что правим?", reply_markup=build_edit_menu(req_id))

    @dp.callback_query(AdminEdit.edit_budget, F.data.startswith("budedit:"))
    async def edit_budget(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.from_user.id):
            await cb.answer("Не админ.", show_alert=True)
            return
        bud = cb.data.split(":",1)[1].strip()
        if bud not in BUDGET_LABELS:
            await cb.answer("Не понял.", show_alert=True)
            return
        data = await state.get_data()
        req_id = int(data["req_id"])
        db.update_request_fields(req_id, {"budget_category": bud})
        db.set_status(req_id, "rework")
        await _notify_user(bot, req_id, f"По заявке №{req_id} админ поменял статью бюджета на: {BUDGET_LABELS[bud]}.")
        await cb.answer("Ок")
        await cb.message.answer(f"Ок. Статья обновлена. Заявка №{req_id} → rework.")
        await state.set_state(AdminEdit.choose_field)
        await cb.message.answer("Ещё что правим?", reply_markup=build_edit_menu(req_id))

    @dp.message(AdminEdit.edit_note)
    async def edit_note(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id): return
        note = (msg.text or "").strip()
        if note == "-":
            note = ""
        data = await state.get_data()
        req_id = int(data["req_id"])
        db.set_status(req_id, "rework")
        if note:
            db.add_comment(req_id, msg.from_user.id, msg.from_user.full_name or "Админ", note)
            await _notify_user(bot, req_id, f"По заявке №{req_id} требуется доработка:\n{note}")
        else:
            await _notify_user(bot, req_id, f"По заявке №{req_id} требуется доработка. Уточни детали у админа.")
        await msg.answer(f"Ок. Пользователю отправлено. Заявка №{req_id} → rework.")
        await state.set_state(AdminEdit.choose_field)
        await msg.answer("Ещё что правим?", reply_markup=build_edit_menu(req_id))

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
