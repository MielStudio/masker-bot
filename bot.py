from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters, JobQueue, CallbackQueryHandler,
    Application
)
from telegram.constants import ParseMode
import json
import os
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo
import shlex
import html
from database.db import SessionLocal
from repositories.user_repository import UserRepository
from services.user_service import UserService
from repositories.task_repository import TaskRepository
from services.task_service import TaskService
from repositories.event_repository import EventRepository
from services.event_service import EventService
from handlers.add_event_command import build_add_event_handler
from handlers.give_points_command import build_give_points_handler
from handlers.add_task_command import build_add_task_handler
from config import *
import calendar as cal

EVENTS_FILE = os.path.join(os.path.dirname(__file__), "events.json")
TASKS_FILE = os.path.join(os.path.dirname(__file__), "tasks.json")
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

SELECT_PROJECT, SELECT_TASK, CONFIRM = range(3)

# добавь РЯДОМ (уникальные числа, чтобы не конфликтовали):
EV_TYPE, EV_TITLE, EV_DESC, EV_DATE, EV_TIME, EV_PERSONAL, EV_USERS, EV_CONFIRM = range(3, 11)


# =======================================

def with_user_service(func):
    db = SessionLocal()
    try:
        user_repo = UserRepository(db)
        user_service = UserService(user_repo)
        return func(user_service)
    finally:
        db.close()

def with_task_service(func):
    db = SessionLocal()
    try:
        task_repo = TaskRepository(db)
        task_service = TaskService(task_repo)
        return func(task_service)
    finally:
        db.close()

def with_event_repo(func):
    db = SessionLocal()
    try:
        event_repo = EventRepository(db)
        return func(event_repo)
    finally:
        db.close()

def with_event_service(func):
    db = SessionLocal()
    try:
        event_repo = EventRepository(db)
        event_service = EventService(event_repo)
        return func(event_service)
    finally:
        db.close()


def _active_tasks_count(user_id: int, tasks: list[dict]) -> int:
    return sum(1 for t in tasks if t.get("reserved_by") == user_id)

def _need_idle_ping(user: dict, now: datetime) -> bool:
    """
    True, если пользователя можно пинговть как 'без задач':
    - нет активных задач
    - не пинговали последние IDLE_REMINDER_DAYS
    """
    last = user.get("last_idle_reminder")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (now - last_dt) >= timedelta(days=IDLE_REMINDER_DAYS)

def _guess_role_id(name: str) -> str | None:
    s = (name or "").lower()
    for key, rid in ROLE_SYNONYMS.items():
        if key in s:
            return rid
    return None

def _stars(level: int) -> str:
    lvl = max(1, min(3, int(level or 1)))
    return "★" * lvl + "☆" * (3 - lvl)  # 1..3 звёзд, остальное пустыми

def _normalize_user_roles(user_record: dict):
    """
    Возвращает список элементов вида (title, emoji, level),
    читая либо roles_ext, либо старый roles.
    """
    items = []
    ext = user_record.get("roles_ext")
    if isinstance(ext, list) and ext:
        for r in ext:
            rid = r.get("id")
            level = r.get("level", 2)
            title, emoji = ROLE_CATALOG.get(rid, (rid or "—", "•"))
            items.append((title, emoji, level))
        return items

    # Фолбэк: старый список строк roles
    for name in (user_record.get("roles") or []):
        rid = _guess_role_id(name)
        if rid and rid in ROLE_CATALOG:
            title, emoji = ROLE_CATALOG[rid]
        else:
            title, emoji = (name, "•")
        items.append((title, emoji, 2))  # дефолтный уровень
    return items

async def check_user_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False

    def _run(user_service: UserService):
        return user_service.is_team_member(user_id)

    is_member = with_user_service(_run)

    if not is_member:
        target = update.effective_message
        if target:
            await target.reply_text(
                "⚠️ Извините, бот работает только с участниками команды.\n"
                "По вопросам обращайтесь к @StanPaige."
            )
        return False

    return True

def format_datetime_rus(dt: datetime) -> str:
    return f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"

def format_date_only_rus(dt: datetime) -> str:
    return f"{dt.day} {MONTH_NAMES[dt.month]} {dt.year}"

# Быстрые клавиатуры
def kb_event_type():
    return ReplyKeyboardMarkup(
        [["🧑‍💻 Собрание", "⏰ Дедлайн"], ["📝 Другое"]],
        resize_keyboard=True, one_time_keyboard=True
    )

def kb_yes_no():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)

def kb_quick_dates():
    return ReplyKeyboardMarkup([["Сегодня", "Завтра"], ["Через неделю"]], resize_keyboard=True, one_time_keyboard=True)

def kb_quick_times():
    return ReplyKeyboardMarkup([["10:00", "14:00"], ["18:00", "20:00"]], resize_keyboard=True, one_time_keyboard=True)

# Парсеры
DATE_RE_1 = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{4}))?\s*$")
TIME_RE    = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")

def parse_date_input(text: str, now: datetime) -> datetime | None:
    s = (text or "").strip().lower()
    if s == "сегодня":
        return now
    if s == "завтра":
        return now + timedelta(days=1)
    if s == "через неделю":
        return now + timedelta(days=7)

    m = DATE_RE_1.match(s)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) if y else now.year
        try:
            return datetime(y, mth, d, tzinfo=WORK_TZ)
        except ValueError:
            return None
    return None

def parse_time_input(text: str) -> tuple[int, int] | None:
    s = (text or "").strip().lower()
    if s in {"утром","утро"}:   return (10, 0)
    if s in {"днем","днём"}:    return (14, 0)
    if s in {"вечером","вечер"}:return (18, 0)
    m = TIME_RE.match(s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if 0 <= hh < 24 and 0 <= mm < 60:
        return (hh, mm)
    return None

def get_user_by_id(user_id: int):
    def _run(user_service: UserService):
        user = user_service.get_user_by_telegram_id(user_id)
        if not user:
            return None
        return user_service.user_to_legacy_dict(user)

    return with_user_service(_run)

def profile_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Баллы", callback_data="pf:points")],
        [InlineKeyboardButton("🧰 Работа", callback_data="pf:work")],
        [InlineKeyboardButton("📅 Ивенты", callback_data="pf:events")],
    ])

def profile_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад в профиль", callback_data="pf:back")],
    ])

def recalculate_percent_rates():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    # Собираем множество всех проектов
    projects = set()
    for u in users:
        pts = u.get("points", {}) or {}
        if isinstance(pts, (int, float)):
            pts = {"Non-project work": int(pts)}
        projects.update(pts.keys())

    # Считаем суммы по проектам
    totals = {p: 0 for p in projects}
    for u in users:
        pts = u.get("points", {}) or {}
        if isinstance(pts, (int, float)):
            pts = {"Non-project work": int(pts)}
        for p, v in pts.items():
            totals[p] += v

    # Записываем доли по каждому проекту
    for u in users:
        pts = u.get("points", {}) or {}
        if isinstance(pts, (int, float)):
            pts = {"Non-project work": int(pts)}
        u["percent_rate"] = {}
        for p in projects:
            total = totals[p] or 0
            mine = pts.get(p, 0)
            u["percent_rate"][p] = (mine / total) if total > 0 else 0.0

    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def add_points(user_id: int, points: int):
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)

        for user in users:
            if user["user_id"] == user_id:
                user["points"] += points
                break
        else:
            print("❌ Пользователь не найден.")
            return

        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        recalculate_percent_rates()

    except Exception as e:
        print(f"❌ Ошибка при добавлении баллов: {e}")

def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"✅ Загружено {len(data)} объектов из {path}")
            return data
    except FileNotFoundError:
        print(f"❌ Файл не найден: {path}")
        return []
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка декодирования JSON в {path}: {e}")
        return []
    except Exception as e:
        print(f"❌ Неизвестная ошибка при загрузке {path}: {e}")
        return []

def save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"💾 Сохранено {len(data)} объектов в {path}")
    except Exception as e:
        print(f"❌ Ошибка при сохранении {path}: {e}")

def format_profile_text(tg_user, user_record) -> str:
    full_name = user_record.get("full_name") or (tg_user.full_name if tg_user else "Без имени")

    # === НОВОЕ: красивый список должностей ===
    role_items = _normalize_user_roles(user_record)
    if role_items:
        roles_block = "\n".join(
            f"{emoji} <b>{html.escape(title)}</b>  {_stars(level)}"
            for (title, emoji, level) in role_items
        )
    else:
        roles_block = "—"

    # Дата вступления (оставляем как было)
    joined_at = user_record.get("joined_at")
    if joined_at:
        try:
            dt = datetime.fromisoformat(joined_at)
            joined_str = format_date_only_rus(dt)
        except:
            joined_str = joined_at
    else:
        joined_str = "—"

    return (
        "<b>👤 Профиль</b>\n\n"
        f"Имя: <b>{html.escape(full_name)}</b>\n"
        f"Должности:\n{roles_block}\n"
        f"Официально в команде с: <b>{joined_str}</b>\n\n"
        "Выбери нужный раздел ниже:"
    )

def build_points_text_for_user(user_record) -> str:
    users = load_json(USERS_FILE)
    my_points: dict = user_record.get("points", {}) or {}

    # Собираем множество всех проектов
    all_projects = set()
    for u in users:
        for prj in (u.get("points", {}) or {}):
            all_projects.add(prj)

    if not all_projects:
        return "📊 Баллы не найдены."

    lines = ["<b>📊 Твои баллы и доля по проектам:</b>", ""]
    for prj in sorted(all_projects):
        total = 0
        for u in users:
            total += (u.get("points", {}) or {}).get(prj, 0)

        mine = my_points.get(prj, 0)
        share = (mine / total * 100) if total > 0 else 0.0
        lines.append(f"• <b>{html.escape(prj)}</b>: {mine} балл(ов) ({share:.1f}%)")
    return "\n".join(lines)

def build_work_text_for_user(user_id: int) -> str:
    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(user_id)
        return [task_service.task_to_legacy_dict(t) for t in tasks]

    my_tasks = with_task_service(_run)

    if not my_tasks:
        return "🧰 Сейчас у тебя нет активных задач."

    out = ["<b>🧰 Твои текущие задачи:</b>", ""]
    for t in my_tasks:
        if t.get("deadline"):
            dt = datetime.fromisoformat(t["deadline"]).replace(tzinfo=WORK_TZ)
            ddl = f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"
        else:
            ddl = "Не назначен"

        out.append(
            f"• <b>{html.escape(t['title'])}</b> (#{t['id']})\n"
            f"  ⏰ Дедлайн: {ddl}\n"
            f"  🏆 Баллы: {t.get('points', 0)}\n"
        )

    return "\n".join(out).strip()

def build_events_text_for_user(user_id: int) -> str:
    now = datetime.now(WORK_TZ)

    def _run(event_service: EventService):
        return event_service.get_upcoming_for_user(user_id, now, limit=5)

    upcoming = with_event_service(_run)

    if not upcoming:
        return "📅 Ближайших событий для тебя не найдено."

    out = ["<b>📅 Твои ближайшие события:</b>", ""]
    for e in upcoming:
        dt = datetime.fromisoformat(e["datetime"]).replace(tzinfo=WORK_TZ)
        when = f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"
        out.append(
            f"• <b>{html.escape(e['title'])}</b>\n"
            f"  🕒 {when}\n"
            f"  {html.escape(e.get('description', '') or '')}\n"
        )

    return "\n".join(out).strip()

def _user_future_events_for_month(user_id: int, year: int, month: int):
    now = datetime.now(WORK_TZ)

    def _run(event_service: EventService):
        return event_service.get_future_for_month(user_id, year, month, now)

    events = with_event_service(_run)

    by_day = {}
    for e in events:
        try:
            dt = datetime.fromisoformat(e["datetime"]).replace(tzinfo=WORK_TZ)
        except Exception:
            continue

        by_day.setdefault(dt.day, []).append((dt, e))

    for d in by_day:
        by_day[d].sort(key=lambda t: t[0])

    return by_day

def _calendar_header(year: int, month: int) -> str:
    return f"📅 <b>{MONTHS_NOM[month]} {year}</b>\n" \
           "Точки «•» означают, что в этот день есть события.\n" \
           "Нажми на день, чтобы увидеть подробности."

def _build_month_markup(year: int, month: int, user_id: int) -> InlineKeyboardMarkup:
    """Рисуем сетку календаря с точками на днях, где есть события."""
    cal.setfirstweekday(cal.MONDAY)
    month_matrix = cal.monthcalendar(year, month)  # список недель по 7 дней, пустые дни = 0
    days_with_events = _user_future_events_for_month(user_id, year, month)

    # 1) Заголовочная строка с навигацией
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1)  if month == 12 else (year, month + 1)

    rows = [
        [
            InlineKeyboardButton("◀️", callback_data=f"cal:{prev_y}-{prev_m:02d}"),
            InlineKeyboardButton("Сегодня", callback_data="cal:today"),
            InlineKeyboardButton("▶️", callback_data=f"cal:{next_y}-{next_m:02d}"),
        ],
        [  # заголовки дней недели
            InlineKeyboardButton(x, callback_data="cal:noop")
            for x in ("Пн","Вт","Ср","Чт","Пт","Сб","Вс")
        ]
    ]

    # 2) Сетка
    for week in month_matrix:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
            else:
                mark = "•" if day in days_with_events else ""
                row.append(InlineKeyboardButton(f"{day}{mark}", callback_data=f"cal:{year}-{month:02d}:d{day:02d}"))
        rows.append(row)

    return InlineKeyboardMarkup(rows)

def _format_day_events_text(user_id: int, year: int, month: int, day: int) -> str:
    items = _user_future_events_for_month(user_id, year, month).get(day, [])
    if not items:
        return "<b>Событий нет</b>"

    lines = [f"<b>📅 {day} {MONTH_NAMES[month]} {year}</b>", ""]
    for dt, e in items:
        when = f"{dt.strftime('%H:%M')}"
        pfx = "📣" if e.get("type") == "meeting" else ("⏰" if e.get("type") == "deadline" else "📝")
        desc = html.escape(e.get("description","")) or "—"
        lines.append(f"{pfx} <b>{html.escape(e['title'])}</b>\n🕒 {when}\n{desc}\n")
    return "\n".join(lines).strip()

def _parse_cal_cb(data: str):
    """
    Возвращает ('month', y, m) | ('day', y, m, d) | ('today',) | ('noop',)
    """
    if data == "cal:today":
        return ("today",)
    if data == "cal:noop":
        return ("noop",)
    if data.startswith("cal:"):
        rest = data[4:]
        if ":d" in rest:
            ym, d = rest.split(":d", 1)
            y, m = map(int, ym.split("-"))
            return ("day", y, m, int(d))
        else:
            y, m = map(int, rest.split("-"))
            return ("month", y, m)
    return ("noop",)

async def safe_reply(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     text: str, markup=None):
    """
    Удобная обёртка для reply_text/callback_query.reply_text/send_message,
    которая проверяет, где у нас есть контекст, и не падает на None.
    """
    try:
        if update.message:
            return await update.message.reply_text(text, reply_markup=markup)
        if update.callback_query and update.callback_query.message:
            return await update.callback_query.message.reply_text(text, reply_markup=markup)
        if update.effective_chat:
            return await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text, reply_markup=markup
            )
    except Exception as e:
        print("❌ safe_reply error:", e)

async def event_auto_notify(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(WORK_TZ)

    def _for_notifications(event_service: EventService):
        return event_service.get_events_for_notifications(now)

    events = with_event_service(_for_notifications)

    for event in events:
        try:
            dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
            delta_hours = delta = (dt - now).total_seconds() / 3600

            if REMINDER_24H_MIN_HOURS <= delta_hours <= REMINDER_24H_MAX_HOURS and not event.get("notified_24h"):
                await send_event_notification(event, context, "24")
                def _mark24(event_service: EventService):
                    event_service.mark_notified_24h(event["id"])
                with_event_service(_mark24)

            if REMINDER_2H_MIN_HOURS <= delta_hours <= REMINDER_2H_MAX_HOURS and not event.get("notified_2h"):
                await send_event_notification(event, context, "2")
                def _mark2(event_service: EventService):
                    event_service.mark_notified_2h(event["id"])
                with_event_service(_mark2)

        except Exception as e:
            print(f"❌ Ошибка авто-оповещения: {e}")

    def _expired(event_service: EventService):
        return event_service.get_started_or_expired(now)

    expired_events = with_event_service(_expired)

    for event in expired_events:
        try:
            if event["type"] == "meeting":
                await send_event_message(event, context, f"📣 Собрание \"{event['title']}\" началось!")

            elif event["type"] == "deadline":
                task_id = event.get("task_id")
                if task_id:
                    def _unassign(task_service: TaskService):
                        return task_service.unassign_task(task_id)

                    with_task_service(_unassign)

                    await send_event_message(
                        event,
                        context,
                        f"⏰ Дедлайн по задаче \"{event['title']}\" истёк!\n"
                        "Задача изымается и становится доступной другим участникам."
                    )

            def _archive(event_service: EventService):
                event_service.archive(event["id"])

            with_event_service(_archive)

        except Exception as e:
            print(f"❌ Ошибка обработки истёкшего события: {e}")

async def idle_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)
    now = datetime.now(WORK_TZ)

    changed = False
    for u in users:
        uid = u.get("user_id")
        if not uid:
            continue
        # (опционально) не пингуем админа
        if uid == ADMIN_ID:
            continue

        has_tasks = _active_tasks_count(uid, tasks) > 0
        if has_tasks:
            continue
        if not _need_idle_ping(u, now):
            continue

        # Сообщение и быстрая клавиатура на «Взять задачу»
        text = (
            "😌 Сейчас у тебя нет активных задач.\n\n"
            "Можешь взять новую через /get_task или попроси @StanPaige назначить задачу.\n"
            "Если нужна помощь — напиши."
        )
        try:
            kb = ReplyKeyboardMarkup(
                [["🔧 Взять задачу", "/get_task"]],
                resize_keyboard=True, one_time_keyboard=True
            )
            await context.bot.send_message(chat_id=uid, text=text, reply_markup=kb)
            u["last_idle_reminder"] = now.isoformat()
            changed = True
        except Exception as e:
            print(f"❌ Не удалось отправить idle-пинг {u.get('username')}: {e}")

    if changed:
        save_json(USERS_FILE, users)

async def idle_scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return
    await idle_reminder_job(context)
    await update.message.reply_text("✅ Проверка выполнена. Пинги отправлены тем, у кого нет задач.")

async def send_event_notification(event, context, when_str):
    dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
    simple_time = f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"
    event_text = (
        f"⏰ Напоминание! До события <b>{event['title']}</b> осталось {when_str} часа(ов)!\n\n"
        f"🕒 Когда: {simple_time}\n\n"
        f"{event.get('description') or ''}"
    )

    if event.get("personal"):
        recipients = event.get("users", [])
    else:
        def _users(user_service: UserService):
            return [
                user_service.user_to_legacy_dict(u)
                for u in user_service.user_repo.list_all()
            ]
        users = with_user_service(_users)
        recipients = [u["user_id"] for u in users]

    success, failed = 0, 0
    for tg_user_id in recipients:
        try:
            await context.bot.send_message(chat_id=tg_user_id, text=event_text, parse_mode="HTML")
            success += 1
        except Exception as e:
            failed += 1
            print(f"❌ Не удалось отправить {tg_user_id}: {e}")

    print(f"📣 Рассылка по событию #{event['id']} ({when_str}h): Успешно: {success}, Ошибок: {failed}")

async def send_event_message(event, context, text):
    if event.get("personal"):
        recipients = event.get("users", [])
    else:
        def _users(user_service: UserService):
            return [
                user_service.user_to_legacy_dict(u)
                for u in user_service.user_repo.list_all()
            ]
        users = with_user_service(_users)
        recipients = [u["user_id"] for u in users]

    for tg_user_id in recipients:
        try:
            await context.bot.send_message(chat_id=tg_user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"❌ Не удалось отправить сообщение {tg_user_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    message = update.effective_message
    if message:
        main_kb = ReplyKeyboardMarkup(
            [["👤 Профиль", "🔧 Взять задачу"]],
            resize_keyboard=True
    )
    await message.reply_text(
        "Здравствуй друг мой. Ты стал частью нашего обителя. Позволь мне тебя сопровождать в твоем грядущем путешествии. Используй заклинание /help чтобы узнать на что ты способен",
        reply_markup=main_kb
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    help_text = (
        "📖 <b>Список твоих заклинаний:</b>\n\n"
        "/start – мое приветствие. Ничего не обычного\n"
        "/help – возможность увидеть все доступные тебе заклинания\n"
        "/upcoming_events – увидеть будущее. Узнать все грядущие события в твоей жизни\n"
        "/my_points – лицезреть свою стоимость и труд. Сколько же ты заработал баллов за свое прохождение?\n"
        "/my_task – понять какова твоя миссия сейчас. Какую работу ты исполняешь в данный момент?\n"
        "/get_task - взять себе новую миссию, если конечно, судьба уже не дала тебе ее"
        # Сюда можно добавить другие команды в будущем
    )
    message = update.effective_message
    if message:
        await message.reply_text(help_text, parse_mode="HTML")

async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    if user.id != ADMIN_ID:
        await message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args:
        await message.reply_text("⚠️ Укажи ID события. Пример: /notify 1")
        return

    event_id = int(context.args[0])

    def _get(event_service: EventService):
        return event_service.get_event_by_id(event_id)

    event = with_event_service(_get)
    if not event:
        await message.reply_text("❌ Событие с таким ID не найдено.")
        return

    dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
    simple_time = f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"
    event_text = (
        f"📢 <b>{event['title']}</b>\n\n"
        f"🕒 Когда: {simple_time}\n\n"
        f"{event.get('description') or ''}"
    )

    recipients = event.get("users", []) if event.get("personal", False) else [
        u["user_id"] for u in [get_user_by_id(ADMIN_ID)] if u
    ]

    if not event.get("personal", False):
        # всем участникам команды
        def _users(user_service: UserService):
            return [
                user_service.user_to_legacy_dict(u)
                for u in user_service.user_repo.list_all()
            ]
        users = with_user_service(_users)
        recipients = [u["user_id"] for u in users]

    success, failed = 0, 0
    for tg_user_id in recipients:
        try:
            await context.bot.send_message(chat_id=tg_user_id, text=event_text, parse_mode="HTML")
            success += 1
        except Exception as e:
            failed += 1
            print(f"❗ Не удалось отправить {tg_user_id}: {e}")

    await message.reply_text(f"✅ Рассылка завершена.\nУспешно: {success} | Ошибок: {failed}")

async def upcoming_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    chat = update.effective_chat

    if user is None or chat is None:
        print("❌ update.effective_user или update.effective_chat вернули None")
        return

    now = datetime.now(WORK_TZ)

    def _run(event_service: EventService):
        return event_service.get_upcoming_for_user(user.id, now, limit=5)

    upcoming = with_event_service(_run)

    if not upcoming:
        await context.bot.send_message(chat_id=chat.id, text="😌 Видимо в будущем тебя не ждут какие либо события.")
        return

    text = "<b>📅 Ближайшие события:</b>\n\n"
    for event in upcoming:
        dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
        date_str = format_datetime_rus(dt)
        text += f"📢 <b>{event['title']}</b>\n🕒 {date_str}\n{event.get('description') or ''}\n\n"

    await context.bot.send_message(chat_id=chat.id, text=text.strip(), parse_mode="HTML")

async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    if not update.message or not update.effective_user:
        return

    tg_user_id = update.effective_user.id

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)

        for user in users:
            if user.get("user_id") == tg_user_id:
                text = "📊 <b>Твои баллы и ставки:</b>\n\n"

                points_dict = user.get("points", {}) or {}
                if isinstance(points_dict, (int, float)):
                    points_dict = {"Non-project work": int(points_dict)}

                percent_dict = user.get("percent_rate", {}) or {}
                if isinstance(percent_dict, (int, float)):
                    # если раньше хранили числом — размажем одинаково по имеющимся проектам
                    percent_dict = {k: float(percent_dict) for k in points_dict.keys()}

                for project in points_dict.keys():
                    points = points_dict.get(project, 0)
                    percent = percent_dict.get(project, 0) * 100
                    text += f"🔹 <b>{project}</b>: {points} баллов ({round(percent)}%)\n"
                await update.message.reply_text(text, parse_mode="HTML")
                return

        await update.message.reply_text("❌ Ты почему то отстутствуешь в системе реестра империи.")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def check_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text("⚠️ Формат: /check_points <username>")
        return

    username = context.args[0].lstrip("@")

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)

        for user in users:
            if user["username"].lower() == username.lower():
                text = f"📊 <b>Баллы @{username}:</b>\n\n"

                points_dict = user.get("points", {})
                percent_dict = user.get("percent_rate", {})

                for project in points_dict.keys():
                    points = points_dict.get(project, 0)
                    percent = percent_dict.get(project, 0) * 100
                    text += f"🔹 <b>{project}</b>: {points} баллов ({round(percent)}%)\n"
                await update.message.reply_text(text, parse_mode="HTML")
                return

        await update.message.reply_text("❌ Пользователь не найден.")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def get_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END

    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    user = get_user_by_id(user_id)
    if not user:
        await safe_reply(update, context, "⚠️ Почему тебя нет в реестре империи?")
        return ConversationHandler.END

    def _run(task_service: TaskService):
        return task_service.count_user_active_tasks(user_id)

    active_tasks_count = with_task_service(_run)

    if active_tasks_count >= MAX_ACTIVE_TASKS_PER_USER:
        await safe_reply(
            update,
            context,
            f"⚠️ Ты не можешь брать более {MAX_ACTIVE_TASKS_PER_USER} задач одновременно!"
        )
        return ConversationHandler.END

    context.user_data["user_id"] = user_id

    markup = ReplyKeyboardMarkup(
        [[p] for p in DEFAULT_PROJECTS],
        one_time_keyboard=True,
        resize_keyboard=True
    )
    await safe_reply(update, context, "🔧 Выберите проект:", markup)
    return SELECT_PROJECT

async def select_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    project = update.message.text
    context.user_data["project"] = project

    user_id = context.user_data.get("user_id")
    if not user_id:
        await safe_reply(update, context, "⚠️ Не удалось определить пользователя.")
        return ConversationHandler.END

    user_record = get_user_by_id(user_id)
    if not user_record:
        await safe_reply(update, context, "⚠️ Кто ты, воин?")
        return ConversationHandler.END

    def _run(task_service: TaskService):
        return task_service.get_available_tasks_for_user(project, user_record)

    relevant_tasks = with_task_service(_run)

    if not relevant_tasks:
        await safe_reply(update, context, "😔 Сейчас нет доступных миссий для твоей роли")
        return ConversationHandler.END

    msg = "📝 Доступные задачи:\n\n"
    for t in relevant_tasks:
        estimated_days = t.get("estimated_days", 7)
        if estimated_days >= 7:
            weeks = estimated_days // 7
            days = estimated_days % 7
            if days == 0:
                time_str = f"{weeks} нед."
            else:
                time_str = f"{weeks} нед. {days} дн."
        else:
            time_str = f"{estimated_days} дн."

        msg += (
            f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
            f"📄 {t['description']}\n"
            f"📂 Тип: {t['type']}\n"
            f"🏆 Баллы: {t['points']}\n"
            f"⏰ Примерное время: {time_str}\n\n"
        )

    await safe_reply(update, context, msg, markup=ReplyKeyboardRemove())
    await safe_reply(update, context, "Введите номер задачи, которую хотите взять")
    return SELECT_TASK

async def select_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    try:
        task_id = int(update.message.text)
        context.user_data["task_id"] = task_id
    except (ValueError, TypeError):
        await safe_reply(update, context, "⚠️ Пожалуйста, введите корректный номер задачи")
        return SELECT_TASK  # чтобы повторить ввод

    await safe_reply(update, context, f"Вы уверены, что хотите взять задачу #{task_id}? Напишите 'да' или 'нет'")
    return CONFIRM

async def confirm_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    answer = update.message.text.lower()
    if answer != "да":
        await safe_reply(update, context, "❌ Выбор отменён")
        return ConversationHandler.END

    task_id = context.user_data.get("task_id")
    user_id = context.user_data.get("user_id")

    if not task_id or not user_id:
        await safe_reply(update, context, "⚠️ Не удалось подтвердить выбор")
        return ConversationHandler.END

    def _count(task_service: TaskService):
        return task_service.count_user_active_tasks(user_id)

    active_count = with_task_service(_count)

    if active_count >= MAX_ACTIVE_TASKS_PER_USER:
        await safe_reply(
            update,
            context,
            f"⚠️ Ты не можешь иметь более {MAX_ACTIVE_TASKS_PER_USER} задач одновременно!"
        )
        return ConversationHandler.END

    if active_count >= 1 and not context.user_data.get("confirmed_multiple"):
        context.user_data["confirmed_multiple"] = True
        await safe_reply(
            update,
            context,
            "⚠️ Ты берешь ещё одну задачу.\n"
            "Будь осторожен: более одной задачи может усложнить твою работу.\n"
            "Ты точно уверен? Напиши ещё раз 'да' чтобы подтвердить."
        )
        return CONFIRM

    def _assign(task_service: TaskService):
        return task_service.assign_task_with_auto_deadline(task_id, user_id, WORK_TZ)

    task = with_task_service(_assign)

    if not task:
        await safe_reply(update, context, f"⚠️ Задача #{task_id} не найдена или недоступна.")
        return ConversationHandler.END

    # создаём deadline event, если его ещё нет
    def _ensure_event(event_repo):
        existing = event_repo.get_by_task_id(task_id)
        if existing:
            return existing

        next_id = 1
        all_events = event_repo.list_all()
        if all_events:
            next_id = max(e.id for e in all_events) + 1

        return event_repo.create_deadline_event(
            event_id=next_id,
            task_id=task.id,
            telegram_user_id=user_id,
            title=f"Дедлайн по задаче #{task.id}",
            description="Пожалуйста, завершите работу в срок.",
            dt_value=task.deadline,
        )

    with_event_repo(_ensure_event)

    context.user_data.pop("confirmed_multiple", None)

    await safe_reply(update, context, "✅ Миссия принадлежит теперь вам. Проявите себя достойно!")
    return ConversationHandler.END

async def my_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(user_id)
        return [task_service.task_to_legacy_dict(t) for t in tasks]

    reserved_tasks = with_task_service(_run)

    if not reserved_tasks:
        await update.message.reply_text(
            "😔 Вы не обременены миссией\n"
            "Чтобы это исправить, используйте заклинание /get_task"
        )
        return

    msg = "📝 Ваши текущие задачи:\n\n"
    for t in reserved_tasks:
        if t.get("deadline"):
            dt = datetime.fromisoformat(t["deadline"]).replace(tzinfo=WORK_TZ)
            date_str = f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"
        else:
            date_str = "Не назначен"

        msg += (
            f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
            f"📄 {t['description']}\n"
            f"📂 Тип: {t['type']}\n"
            f"🏆 Баллы: {t['points']}\n"
            f"⏰ Дедлайн: {date_str}\n\n"
        )

    await update.message.reply_text(msg, parse_mode="HTML")

async def search_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    args = context.args
    filter_name = args[0].lower() if args else None

    def _run(task_service: TaskService):
        tasks = [task_service.task_to_legacy_dict(t) for t in task_service.list_all_tasks()]

        if filter_name == "reserved":
            return [t for t in tasks if t.get("reserved_by") is not None]
        if filter_name == "unreserved":
            return [t for t in tasks if t.get("reserved_by") is None]
        if filter_name == "deadline":
            return sorted(tasks, key=lambda t: t.get("deadline") or "")

        return tasks

    filtered_tasks = with_task_service(_run)

    if not filtered_tasks:
        await update.message.reply_text("⚠️ Задачи не найдены с указанными параметрами.")
        return

    msg = "📋 Все задачи:\n\n"
    for t in filtered_tasks:
        reserved_by = t.get("reserved_by")
        reserved_str = f"Зарезервирована пользователем {reserved_by}" if reserved_by else "Свободна"

        estimated_days = t.get("estimated_days", 7)
        if estimated_days >= 7:
            weeks = estimated_days // 7
            days = estimated_days % 7
            time_str = f"{weeks} нед." if days == 0 else f"{weeks} нед. {days} дн."
        else:
            time_str = f"{estimated_days} дн."

        msg += (
            f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
            f"📄 {t['description']}\n"
            f"📂 Тип: {t['type']}\n"
            f"🏆 Баллы: {t['points']}\n"
            f"⏰ Примерное время: {time_str}\n"
            f"📌 Статус: {reserved_str}\n\n"
        )

    for i in range(0, len(msg), MAX_TELEGRAM_MESSAGE_LEN):
        await update.message.reply_text(msg[i:i + MAX_TELEGRAM_MESSAGE_LEN], parse_mode="HTML")

async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    user = get_user_by_id(user_id)
    if not user or ("admin" not in user.get("roles", []) and user.get("role") != "admin"):
        await safe_reply(update, context, "⚠️ У вас нет прав для этой команды.")
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Укажите ID задачи: /task_done <ID>")
        return

    task_id = int(context.args[0])

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"⚠️ Задача #{task_id} не найдена.")
        return

    reserved_by = task.assignee.telegram_user_id if task.assignee else None
    task_title = task.title

    def _mark_done(task_service: TaskService):
        return task_service.mark_done(task_id)

    updated = with_task_service(_mark_done)
    if not updated:
        await safe_reply(update, context, f"⚠️ Не удалось завершить задачу #{task_id}.")
        return

    def _remove_events(event_repo: EventRepository):
        return event_repo.remove_by_task_id(task_id)

    with_event_repo(_remove_events)

    await update.message.reply_text(f"✅ Задача #{task_id} успешно помечена как выполненная.")

    if reserved_by:
        try:
            await context.bot.send_message(
                chat_id=reserved_by,
                text=(
                    f"🎉 Задача <b>{task_title}</b> (#{task_id}) "
                    "помечена как выполненная. Спасибо за вашу работу!"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Не удалось отправить уведомление пользователю: {e}")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    help_text = (
        "🗝️ <b>Админ-команды:</b>\n\n"
        "/add_event – добавить новое событие в календарь\n"
        "/notify – разослать уведомление о событии по ID\n"
        "/give_points – добавить баллы участнику по username\n"
        "/check_points – проверить баллы участника по username\n"
        "/search_task – посмотреть задачи (фильтры: reserved/unreserved/deadline)\n"
        "/task_done – пометить задачу как выполненную и удалить\n"
        "/edit_deadline – редактирование дедлайна задач участников\n"
        "/delete_event – удалить событие по ID\n"
        "/add_task – добавить новую задачу\n"
        "/unassign_task – снять участника с задачи, удалить дедлайн и сделать её доступной\n"
        "/assign_task – Назначить задачу участнику по username\n"
        "/broadcast – отправить сообщение всем участникам или одному (@username)\n"
        "/show_all_events – увидеть полный список всех событий\n"
        "/delete_event – удалить событие по ID\n"
        "/idle_scan_now – вручную запустить проверку бездельников\n"
        # Допиши сюда другие твои админ-команды при необходимости
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

async def edit_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Используй так: /edit_deadline <ID задачи> <новая дата и время>\n"
            "Пример: /edit_deadline 3 2025-07-15T18:00"
        )
        return

    try:
        task_id = int(context.args[0])
        new_dt_str = context.args[1]

        if " " in new_dt_str:
            new_dt_str = new_dt_str.replace(" ", "T")

        new_dt = datetime.fromisoformat(new_dt_str).replace(tzinfo=WORK_TZ)

        def _set_deadline(task_service: TaskService):
            return task_service.set_deadline(task_id, new_dt)

        task = with_task_service(_set_deadline)
        if not task:
            await update.message.reply_text(f"❌ Задача с ID #{task_id} не найдена.")
            return

        def _update_event(event_repo: EventRepository):
            existing = event_repo.update_deadline_event(task_id, new_dt)
            if existing:
                return existing

            next_id = 1
            all_events = event_repo.list_all()
            if all_events:
                next_id = max(e.id for e in all_events) + 1

            assignee_tg_id = task.assignee.telegram_user_id if task.assignee else None
            if assignee_tg_id is None:
                return None

            return event_repo.create_deadline_event(
                event_id=next_id,
                task_id=task_id,
                telegram_user_id=assignee_tg_id,
                title=f"Дедлайн по задаче #{task_id}",
                description="Обновлён администратором.",
                dt_value=new_dt,
            )

        with_event_repo(_update_event)

        await update.message.reply_text(
            f"✅ Дедлайн задачи #{task_id} обновлён!\n"
            f"Новая дата: {format_datetime_rus(new_dt)}"
        )

    except ValueError:
        await update.message.reply_text("❌ Неверный формат даты. Используй ISO-формат: 2025-07-15T18:00")
    except Exception as e:
        await update.message.reply_text(f"❌ Возникла ошибка: {e}")

async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text("⚠️ Используй так: /delete_event <ID события>\nПример: /delete_event 2")
        return

    try:
        event_id = int(context.args[0])

        def _get(event_service: EventService):
            return event_service.get_event_by_id(event_id)

        event = with_event_service(_get)
        if not event:
            await update.message.reply_text(f"❌ Событие с ID #{event_id} не найдено.")
            return

        def _delete(event_service: EventService):
            return event_service.delete_event(event_id)

        with_event_service(_delete)

        await update.message.reply_text(f"✅ Событие \"{event['title']}\" (ID #{event_id}) успешно удалено.")

    except ValueError:
        await update.message.reply_text("❌ ID события должен быть числом.")
    except Exception as e:
        await update.message.reply_text(f"❌ Произошла ошибка: {e}")

async def profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    tg_user = update.effective_user
    if not tg_user:
        return
    user_record = get_user_by_id(tg_user.id)
    if not user_record:
        await safe_reply(update, context, "⚠️ Не найден в реестре империи.")
        return

    text = format_profile_text(tg_user, user_record)
    await safe_reply(update, context, text, markup=profile_root_kb())

async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user
    if not tg_user:
        return
    user_record = get_user_by_id(tg_user.id)
    if not user_record:
        await query.edit_message_text("⚠️ Не найден в реестре империи.")
        return

    data = query.data
    if data == "pf:back":
        await query.edit_message_text(
            format_profile_text(tg_user, user_record),
            reply_markup=profile_root_kb(),
            parse_mode=ParseMode.HTML
        )
        return

    if data == "pf:points":
        text = build_points_text_for_user(user_record)
    elif data == "pf:work":
        text = build_work_text_for_user(tg_user.id)
    elif data == "pf:events":
        now = datetime.now(WORK_TZ)
        kb = _build_month_markup(now.year, now.month, tg_user.id)
        await query.edit_message_text(_calendar_header(now.year, now.month), parse_mode="HTML", reply_markup=kb)
        return
    else:
        text = "Неизвестный раздел."

    await query.edit_message_text(text, reply_markup=profile_back_kb(), parse_mode=ParseMode.HTML)

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    user = update.effective_user
    if not user:
        return
    now = datetime.now(WORK_TZ)
    kb = _build_month_markup(now.year, now.month, user.id)
    await update.message.reply_text(
        _calendar_header(now.year, now.month),
        parse_mode="HTML",
        reply_markup=kb
    )

async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    kind = _parse_cal_cb(q.data)
    if kind[0] == "noop":
        return

    if kind[0] == "today":
        now = datetime.now(WORK_TZ)
        kb = _build_month_markup(now.year, now.month, user.id)
        await q.edit_message_text(_calendar_header(now.year, now.month), parse_mode="HTML", reply_markup=kb)
        return

    if kind[0] == "month":
        _, y, m = kind
        kb = _build_month_markup(y, m, user.id)
        await q.edit_message_text(_calendar_header(y, m), parse_mode="HTML", reply_markup=kb)
        return

    if kind[0] == "day":
        _, y, m, d = kind
        text = _format_day_events_text(user.id, y, m, d)
        back = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад к календарю", callback_data=f"cal:{y}-{m:02d}")]
        ])
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=back)
        return

async def unassign_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Используй: /unassign_task <ID задачи>")
        return

    task_id = int(context.args[0])

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await update.message.reply_text(f"❌ Задача #{task_id} не найдена.")
        return

    reserved_by = task.assignee.telegram_user_id if task.assignee else None
    task_title = task.title

    if not reserved_by:
        await update.message.reply_text(f"⚠️ Задача #{task_id} уже свободна.")
        return

    def _unassign(task_service: TaskService):
        return task_service.unassign_task(task_id)

    with_task_service(_unassign)

    def _remove_events(event_repo: EventRepository):
        return event_repo.remove_by_task_id(task_id)

    removed = with_event_repo(_remove_events)

    await update.message.reply_text(
        f"✅ Задача #{task_id} теперь свободна. Удалено связанных событий: {removed}."
    )

    try:
        await context.bot.send_message(
            chat_id=reserved_by,
            text=(
                f"⚠️ Задача <b>{task_title}</b> (#{task_id}) "
                "была снята с вас администратором и теперь доступна другим."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"❌ Не удалось уведомить участника: {e}")

async def assign_task_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Используй так:\n"
            "<code>/assign_task &lt;ID задачи&gt; &lt;username&gt;</code>\n\n"
            "Пример:\n"
            "<code>/assign_task 2 Franky126866</code>",
            parse_mode="HTML"
        )
        return

    try:
        task_id = int(context.args[0])
        username = context.args[1].lstrip("@").strip().lower()

        def _get_user(user_service: UserService):
            return user_service.get_user_by_telegram_id(
                get_user_by_id(user.id)["user_id"]
            )

        # ищем назначаемого пользователя
        def _target(user_service: UserService):
            user_obj = user_service.user_repo.get_by_username(username)
            return user_obj

        target_user = with_user_service(_target)
        if not target_user:
            await update.message.reply_text(f"❌ Пользователь @{username} не найден.")
            return

        def _get_task(task_service: TaskService):
            return task_service.get_task_by_id(task_id)

        task_before = with_task_service(_get_task)
        if not task_before:
            await update.message.reply_text(f"❌ Задача #{task_id} не найдена.")
            return

        if task_before.assignee_id is not None:
            await update.message.reply_text(f"⚠️ Задача #{task_id} уже назначена.")
            return

        def _assign(task_service: TaskService):
            return task_service.assign_task_to_user(task_id, target_user.telegram_user_id, WORK_TZ)

        task = with_task_service(_assign)
        if not task:
            await update.message.reply_text(f"❌ Не удалось назначить задачу #{task_id}.")
            return

        def _ensure_event(event_repo: EventRepository):
            existing = event_repo.get_by_task_id(task_id)
            if existing:
                return existing

            next_id = 1
            all_events = event_repo.list_all()
            if all_events:
                next_id = max(e.id for e in all_events) + 1

            return event_repo.create_deadline_event(
                event_id=next_id,
                task_id=task.id,
                telegram_user_id=target_user.telegram_user_id,
                title=f"Дедлайн по задаче #{task_id}",
                description="Администратор назначил вам задачу.",
                dt_value=task.deadline,
            )

        with_event_repo(_ensure_event)

        await update.message.reply_text(
            f"✅ Задача #{task_id} успешно назначена пользователю @{username}."
        )

        try:
            await context.bot.send_message(
                chat_id=target_user.telegram_user_id,
                text=(
                    f"📌 Вам назначена новая задача!\n\n"
                    f"<b>{task.title}</b> (#{task_id})\n"
                    f"{html.escape(task.description or '')}\n\n"
                    f"⏰ Дедлайн: {format_datetime_rus(task.deadline)}"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"❌ Не удалось отправить уведомление пользователю: {e}")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args:
        await update.message.reply_text(
            "📣 Введите сообщение для рассылки.\n\n"
            "⚠️ <b>Сообщение будет отправлено всем участникам команды</b>\n"
            "Напишите <code>отмена</code>, чтобы отменить рассылку.",
            parse_mode="HTML"
        )
        return

    raw_input = " ".join(context.args)
    if ";" in raw_input:
        # Личное сообщение одному
        parts = raw_input.split(";", 1)
        username = parts[0].strip().lstrip("@").lower()
        message_text = parts[1].strip()

        users = load_json(USERS_FILE)
        user_obj = next((u for u in users if u["username"].lower() == username), None)
        if not user_obj:
            await update.message.reply_text(f"❌ Пользователь @{username} не найден.")
            return

        try:
            await context.bot.send_message(
                chat_id=user_obj["user_id"],
                text=f"📢 Сообщение от администратора:\n\n{message_text}"
            )
            await update.message.reply_text(f"✅ Сообщение отправлено пользователю @{username}.")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отправке: {e}")

    else:
        # Общая рассылка всем
        message_text = html.escape(raw_input.strip())
        users = load_json(USERS_FILE)
        success, failed = 0, 0

        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u["user_id"],
                    text=f"📢 Сообщение от администратора:\n\n{message_text}",
                    parse_mode="HTML"
                )
                success += 1
            except Exception as e:
                print(f"❌ Ошибка для {u.get('username')}: {e}")
                failed += 1

        await update.message.reply_text(
            f"✅ Рассылка завершена.\nОтправлено: {success} | Ошибок: {failed}."
        )

async def show_all_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    try:
        def _run(event_service: EventService):
            return event_service.get_all_events()

        events = with_event_service(_run)

        if not events:
            await update.message.reply_text("📭 Список событий пуст.")
            return

        now = datetime.now(WORK_TZ)
        events_sorted = sorted(events, key=lambda e: e.get("datetime") or "")

        msg = "<b>📅 Все события:</b>\n\n"
        for event in events_sorted:
            dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
            status = "✅ Актуально" if dt >= now else "⌛ Уже прошло"
            personal_str = " (Персональное)" if event.get("personal", False) else ""

            msg += (
                f"🔹 <b>{event['title']}</b>{personal_str}\n"
                f"🗂️ Тип: {event['type']}\n"
                f"🕒 Когда: {format_datetime_rus(dt)}\n"
                f"📄 {event.get('description') or ''}\n"
                f"📌 Статус: {status}\n"
                f"🆔 ID: {event['id']}\n\n"
            )

        for i in range(0, len(msg), MAX_TELEGRAM_MESSAGE_LEN):
            await update.message.reply_text(msg[i:i + MAX_TELEGRAM_MESSAGE_LEN], parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

def get_task_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("get_task", get_task_start),
            # кнопка из ReplyKeyboard отправляет именно этот текст:
            MessageHandler(filters.Regex(r"^(?:🔧\s*Взять задачу|/get_task)$"), get_task_start),
            # (опционально чуть шире: r"^[🔧🛠️]?\s*Взять задачу$")
        ],
        states={
            SELECT_PROJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_project)
            ],
            SELECT_TASK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_task)
            ],
            CONFIRM: [
                MessageHandler(
                    filters.Regex(re.compile(r"^(да|нет)$", re.IGNORECASE)),
                    confirm_task
                )
            ],
        },
        fallbacks=[],
        allow_reentry=True
    )

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Моё приветствие"),
        BotCommand("help", "Все твои доступные заклинания"),
        BotCommand("upcoming_events", "Посмотреть грядущие события"),
        BotCommand("my_points", "Увидеть свои баллы"),
        BotCommand("my_task", "Посмотреть свои задачи"),
        BotCommand("get_task", "Взять новую задачу"),
    ])


app = ApplicationBuilder().token("7833612109:AAGfBTL2pn5WqDoWLwFYA1cZBd-XF7VzJ_o").post_init(post_init).build()
job_queue = app.job_queue
job_queue.run_repeating(event_auto_notify, interval=300, first=10)

# 🔔 Ежедневная проверка «без задач».
# Запускаем каждый день, но не чаще чем раз в IDLE_REMINDER_DAYS для каждого пользователя.
job_queue.run_repeating(
    idle_reminder_job,
    interval=24 * 60 * 60,              # раз в сутки сканируем
    first=IDLE_REMINDER_START_DELAY_SEC # старт через 5 минут после запуска бота
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(MessageHandler(filters.Regex(r"^👤 Профиль$"), profile_entry))
app.add_handler(CommandHandler("profile", profile_entry))
app.add_handler(CallbackQueryHandler(profile_callback, pattern=r"^pf:"))
app.add_handler(CommandHandler("admin_help", admin_help))
app.add_handler(build_add_event_handler(
    admin_id=ADMIN_ID,
    users_file=USERS_FILE,
    events_file=EVENTS_FILE,
    work_tz=WORK_TZ,
    load_json=load_json,
    save_json=save_json,
    format_datetime_rus=format_datetime_rus,
))
app.add_handler(CommandHandler("notify", notify))
app.add_handler(CommandHandler("upcoming_events", upcoming_events))
app.add_handler(build_give_points_handler(
    admin_id=ADMIN_ID,
    users_file=USERS_FILE,
    load_json=load_json,
    save_json=save_json,
    recalculate_percent_rates=recalculate_percent_rates,
))
app.add_handler(CommandHandler("my_points", my_points))
app.add_handler(CommandHandler("check_points", check_points))
app.add_handler(CommandHandler("my_task", my_task))
app.add_handler(CommandHandler("search_task", search_task))
app.add_handler(CommandHandler("task_done", task_done))
app.add_handler(CommandHandler("edit_deadline", edit_deadline))
app.add_handler(CommandHandler("delete_event", delete_event))
app.add_handler(build_add_task_handler(
    tasks_file=TASKS_FILE,
    users_file=USERS_FILE,
    load_json=load_json,
    save_json=save_json,
    admin_id=ADMIN_ID,
))
app.add_handler(CommandHandler("unassign_task", unassign_task))
app.add_handler(CommandHandler("assign_task", assign_task_to_user))
app.add_handler(CommandHandler("broadcast", broadcast_message))
app.add_handler(CommandHandler("show_all_events", show_all_events))
app.add_handler(CommandHandler("calendar", calendar_command))
app.add_handler(CallbackQueryHandler(calendar_callback, pattern=r"^cal:"))
app.add_handler(CommandHandler("idle_scan_now", idle_scan_now))
app.add_handler(get_task_handler())
app.run_polling()
