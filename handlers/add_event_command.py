# -*- coding: utf-8 -*-
from __future__ import annotations
import re, html, os, json
from datetime import datetime, timedelta
from typing import Callable, Any, Dict, Tuple, Optional

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
)

# Чтобы не конфликтовать с другими диалогами в bot.py
EV_TYPE, EV_TITLE, EV_DESC, EV_DATE, EV_TIME, EV_PERSONAL, EV_USERS, EV_CONFIRM = range(100, 108)

# ---------- Вспомогательные клавиатуры и парсеры ----------
def kb_event_type():
    return ReplyKeyboardMarkup([["🧑‍💻 Собрание", "⏰ Дедлайн"], ["📝 Другое"]], resize_keyboard=True, one_time_keyboard=True)
def kb_yes_no():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)
def kb_quick_dates():
    return ReplyKeyboardMarkup([["Сегодня", "Завтра"], ["Через неделю"]], resize_keyboard=True, one_time_keyboard=True)
def kb_quick_times():
    return ReplyKeyboardMarkup([["10:00", "14:00"], ["18:00", "20:00"]], resize_keyboard=True, one_time_keyboard=True)

DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{4}))?\s*$")
TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")

def parse_date_input(text: str, now: datetime) -> Optional[datetime]:
    s = (text or "").strip().lower()
    if s == "сегодня": return now
    if s == "завтра": return now + timedelta(days=1)
    if s == "через неделю": return now + timedelta(days=7)
    m = DATE_RE.match(s)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or now.year)
        try: return datetime(y, mth, d, tzinfo=now.tzinfo)
        except ValueError: return None
    return None

def parse_time_input(text: str) -> Optional[Tuple[int, int]]:
    s = (text or "").strip().lower()
    if s in {"утро","утром"}: return (10, 0)
    if s in {"днем","днём"}:  return (14, 0)
    if s in {"вечер","вечером"}: return (18, 0)
    m = TIME_RE.match(s)
    if not m: return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return (hh, mm) if 0 <= hh < 24 and 0 <= mm < 60 else None

def _map_type_btn(txt: str) -> Optional[str]:
    t = (txt or "").lower()
    if "собрани" in t: return "meeting"
    if "дедлайн" in t: return "deadline"
    if "друго"   in t: return "other"
    return None

# ---------- Построитель ConversationHandler с DI ----------
def build_add_event_handler(
    *,
    admin_id: int,
    users_file: str,
    events_file: str,
    work_tz,  # ZoneInfo
    load_json: Callable[[str], list],
    save_json: Callable[[str, list], None],
    format_datetime_rus: Callable[[datetime], str],
):
    user_data_key = "new_event"

    async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # поддерживаем старый формат: /add_event type;title;desc;2025-06-20T18:00
        if not update.effective_user or not update.effective_message:
            return ConversationHandler.END
        if update.effective_user.id != admin_id:
            await update.effective_message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
            return ConversationHandler.END

        if context.args:
            try:
                raw = " ".join(context.args)
                parts = [p.strip() for p in raw.split(";")]
                if len(parts) < 4: raise ValueError("Недостаточно параметров")
                event_type, title, description, dt_str = parts[:4]
                dt = datetime.fromisoformat(dt_str)
                events = load_json(events_file)
                new_id = max([e.get("id", 0) for e in events], default=0) + 1
                events.append({
                    "id": new_id,
                    "type": event_type,
                    "title": title,
                    "description": description,
                    "datetime": dt.replace(tzinfo=work_tz).isoformat(),
                    "notify_users": True,
                })
                save_json(events_file, events)
                await update.effective_message.reply_text(f"✅ Добавлено событие:\n<b>{html.escape(title)}</b> ({event_type})", parse_mode="HTML")
                return ConversationHandler.END
            except Exception as e:
                await update.effective_message.reply_text(f"❌ Ошибка парсинга: {e}\nЗапускаю мастер…")

        context.user_data[user_data_key] = {}
        await update.effective_message.reply_text("📅 Какой тип события?", reply_markup=kb_event_type())
        return EV_TYPE

    async def step_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
        t = _map_type_btn(update.message.text if update.message else "")
        if not t:
            await update.message.reply_text("Выбери тип кнопкой: «Собрание», «Дедлайн» или «Другое».", reply_markup=kb_event_type())
            return EV_TYPE
        context.user_data[user_data_key]["type"] = t
        await update.message.reply_text("✍️ Введи заголовок события:", reply_markup=ReplyKeyboardRemove())
        return EV_TITLE

    async def step_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data[user_data_key]["title"] = (update.message.text or "").strip()
        await update.message.reply_text("📝 Короткое описание (или «Пропустить»):")
        return EV_DESC

    async def step_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
        desc = (update.message.text or "").strip()
        if desc.lower() == "пропустить": desc = ""
        context.user_data[user_data_key]["description"] = desc
        now = datetime.now(work_tz)
        await update.message.reply_text("📆 Дата (кнопкой или «ДД.ММ», «ДД.ММ.ГГГГ», «сегодня/завтра»):", reply_markup=kb_quick_dates())
        return EV_DATE

    async def step_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
        base = datetime.now(work_tz)
        dt = parse_date_input(update.message.text if update.message else "", base)
        if not dt:
            await update.message.reply_text("Не понял дату. Примеры: «сегодня», «20.06», «20.06.2025».", reply_markup=kb_quick_dates())
            return EV_DATE
        context.user_data[user_data_key]["_date"] = dt.date()
        await update.message.reply_text("⏰ Время? (10:00, 14:30 или «утром/вечером»):", reply_markup=kb_quick_times())
        return EV_TIME

    async def step_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
        pair = parse_time_input(update.message.text if update.message else "")
        if not pair:
            await update.message.reply_text("Не понял время. Примеры: 10:00, 14:30, «вечером».", reply_markup=kb_quick_times())
            return EV_TIME
        h, m = pair
        d = context.user_data[user_data_key]["_date"]
        dt = datetime(d.year, d.month, d.day, h, m, tzinfo=work_tz)
        context.user_data[user_data_key]["datetime"] = dt.isoformat()
        await update.message.reply_text("Это персональное событие? (Да/Нет)", reply_markup=kb_yes_no())
        return EV_PERSONAL

    async def step_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
        ans = (update.message.text or "").strip().lower()
        is_personal = ans.startswith("д")
        context.user_data[user_data_key]["personal"] = is_personal
        if is_personal:
            await update.message.reply_text("Кого упомянуть? Напиши @username через пробел. Или «Пропустить».", reply_markup=ReplyKeyboardRemove())
            return EV_USERS
        return await step_confirm(update, context)

    async def step_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (update.message.text or "").strip()
        usernames = [] if raw.lower() == "пропустить" else [t.lstrip("@").strip().lower() for t in raw.split() if t.strip()]
        # маппим username -> user_id по файлу users.json
        from_user_file = load_json(users_file)
        ids = []
        for name in usernames:
            u = next((u for u in from_user_file if u.get("username","").lower()==name), None)
            if u: ids.append(u["user_id"])
        context.user_data[user_data_key]["users"] = ids
        return await step_confirm(update, context)

    async def step_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data.get(user_data_key, {})
        title = data.get("title","—")
        etype = data.get("type","other")
        when  = data.get("datetime","")
        desc  = data.get("description","")
        personal = data.get("personal", False)
        who = data.get("users", [])
        dt = datetime.fromisoformat(when) if when else None
        when_str = format_datetime_rus(dt) if dt else "—"
        text = [
            "<b>Проверь данные события:</b>",
            f"Тип: <b>{etype}</b>",
            f"Заголовок: <b>{html.escape(title)}</b>",
            f"Описание: {html.escape(desc) if desc else '—'}",
            f"Когда: <b>{when_str}</b>",
            f"Персональное: <b>{'Да' if personal else 'Нет'}</b>",
        ]
        if personal: text.append(f"Пользователи: {', '.join(map(str, who)) if who else '—'}")
        await update.message.reply_text("\n".join(text), parse_mode="HTML", reply_markup=kb_yes_no())
        return EV_CONFIRM

    async def step_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
        answer = (update.message.text or "").strip().lower()
        if answer not in {"да","yes","y"}:
            await update.message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove())
            context.user_data.pop(user_data_key, None)
            return ConversationHandler.END

        data = context.user_data.get(user_data_key, {})
        events = load_json(events_file)
        new_id = max([e.get("id", 0) for e in events], default=0) + 1
        ev = {
            "id": new_id,
            "type": data.get("type","other"),
            "title": data.get("title",""),
            "description": data.get("description",""),
            "datetime": data.get("datetime"),
            "notify_users": True
        }
        if data.get("personal"):
            ev["personal"] = True
            ev["users"] = data.get("users", [])
        events.append(ev)
        save_json(events_file, events)
        context.user_data.pop(user_data_key, None)
        await update.message.reply_text("✅ Событие добавлено!", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # сам handler
    return ConversationHandler(
        entry_points=[CommandHandler("add_event", entry)],
        states={
            EV_TYPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_type)],
            EV_TITLE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_title)],
            EV_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_desc)],
            EV_DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_date)],
            EV_TIME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_time)],
            EV_PERSONAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_personal)],
            EV_USERS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_users)],
            EV_CONFIRM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_finish)],
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^(отмена|cancel)$"), 
                           lambda u,c: (u.effective_message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove()), ConversationHandler.END)[1])
        ],
        allow_reentry=True,
    )