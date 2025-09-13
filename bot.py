from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters, JobQueue, CallbackQueryHandler
)
from telegram.constants import ParseMode
import json
import os
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo
import shlex
import html
from handlers.add_event_command import build_add_event_handler
from handlers.give_points_command import build_give_points_handler
import calendar as cal

EVENTS_FILE = os.path.join(os.path.dirname(__file__), "events.json")
TASKS_FILE = os.path.join(os.path.dirname(__file__), "tasks.json")
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")
ADMIN_ID = 1847178297

SELECT_PROJECT, SELECT_TASK, CONFIRM = range(3)

# добавь РЯДОМ (уникальные числа, чтобы не конфликтовали):
EV_TYPE, EV_TITLE, EV_DESC, EV_DATE, EV_TIME, EV_PERSONAL, EV_USERS, EV_CONFIRM = range(3, 11)

month_names = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

MONTHS_NOM = {
    1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
    7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"
}

WORK_TZ = ZoneInfo("Europe/Kyiv")

ROLE_CATALOG = {
    "gamedesign":    ("Геймдизайн", "🎮"),
    "narrative":     ("Сценарий и нарратив", "🖋️"),
    "art3d":         ("3D-арт", "🧊"),
    "art2d":         ("2D-арт", "🎨"),
    "animation":     ("Анимация", "🎞️"),
    "programming":   ("Программирование", "💻"),
    "testing":       ("Тестирование", "🧪"),
    "docs":          ("Документация", "📚"),
    "finance_legal": ("Финансы и юр. вопросы", "⚖️"),
    "marketing_pr":  ("Маркетинг и PR", "📣"),
    "management":    ("Управление", "🧭"),
    "audio":         ("Аудио", "🎵")
}

# Простые синонимы для автоподборки id из старых строк roles
ROLE_SYNONYMS = {
    "геймдизайн": "gamedesign",
    "нарратив": "narrative", "сценарий": "narrative",
    "3д": "art3d", "3d": "art3d", "3д-арт": "art3d",
    "2д": "art2d", "2d": "art2d", "2д-арт": "art2d", "визуальная работа": "visual",
    "анимация": "animation",
    "программирование": "programming",
    "тестирование": "testing",
    "документация": "docs",
    "финансы": "finance_legal", "юридические": "finance_legal",
    "маркетинг": "marketing_pr", "pr": "marketing_pr",
    "управление": "management",
    "аудио": "audio",
}

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
    users = load_json(USERS_FILE)
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False

    user = next((u for u in users if u["user_id"] == user_id), None)
    if not user:
        await update.message.reply_text(
            "⚠️ Извините, бот работает только с участниками команды.\n"
            "По вопросам обращайтесь к @StanPaige."
        )
        return False
    return True

def format_datetime_rus(dt: datetime) -> str:
    return f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"

def format_date_only_rus(dt: datetime) -> str:
    return f"{dt.day} {month_names[dt.month]} {dt.year}"

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
    users = load_json(USERS_FILE)
    return next((u for u in users if u.get("user_id") == user_id), None)

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
    tasks = load_json(TASKS_FILE)
    my_tasks = [t for t in tasks if t.get("reserved_by") == user_id]
    if not my_tasks:
        return "🧰 Сейчас у тебя нет активных задач."

    out = ["<b>🧰 Твои текущие задачи:</b>", ""]
    for t in my_tasks:
        if t.get("deadline"):
            dt = datetime.fromisoformat(t["deadline"]).replace(tzinfo=WORK_TZ)
            ddl = f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"
        else:
            ddl = "Не назначен"
        out.append(
            f"• <b>{html.escape(t['title'])}</b> (#{t['id']})\n"
            f"  ⏰ Дедлайн: {ddl}\n"
            f"  🏆 Баллы: {t.get('points', 0)}\n"
        )
    return "\n".join(out).strip()

def build_events_text_for_user(user_id: int) -> str:
    events = load_json(EVENTS_FILE)
    now = datetime.now(WORK_TZ)
    upcoming = []
    for e in events:
        try:
            dt = datetime.fromisoformat(e["datetime"]).replace(tzinfo=WORK_TZ)
        except:
            continue
        if dt < now:
            continue
        if not e.get("personal") or user_id in (e.get("users") or []):
            upcoming.append((dt, e))

    if not upcoming:
        return "📅 Ближайших событий для тебя не найдено."

    upcoming.sort(key=lambda x: x[0])
    upcoming = upcoming[:5]
    out = ["<b>📅 Твои ближайшие события:</b>", ""]
    for dt, e in upcoming:
        when = f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"
        out.append(f"• <b>{html.escape(e['title'])}</b>\n  🕒 {when}\n  {html.escape(e.get('description',''))}\n")
    return "\n".join(out).strip()

def _user_future_events_for_month(user_id: int, year: int, month: int):
    """Вернёт словарь {day: [events...]} только будущих событий, доступных пользователю."""
    events = load_json(EVENTS_FILE)
    now = datetime.now(WORK_TZ)
    by_day = {}
    for e in events:
        try:
            dt = datetime.fromisoformat(e["datetime"]).replace(tzinfo=WORK_TZ)
        except:
            continue
        if dt < now:  # только будущее
            continue
        if dt.year != year or dt.month != month:
            continue
        if e.get("personal") and user_id not in (e.get("users") or []):
            continue
        by_day.setdefault(dt.day, []).append((dt, e))
    # сортируем события внутри каждого дня
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

    lines = [f"<b>📅 {day} {month_names[month]} {year}</b>", ""]
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
    events = load_json(EVENTS_FILE)
    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)
    now = datetime.now(WORK_TZ)

    changed = False

    for event in events[:]:
        try:
            dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
            delta = dt - now

            if not event.get("notify_users"):
                continue

            # За 24 часа
            if 23 <= delta.total_seconds() / 3600 <= 25 and not event.get("notified_24h"):
                await send_event_notification(event, users, context, "24")
                event["notified_24h"] = True
                changed = True

            # За 2 часа
            if 1.5 <= delta.total_seconds() / 3600 <= 2.5 and not event.get("notified_2h"):
                await send_event_notification(event, users, context, "2")
                event["notified_2h"] = True
                changed = True
            
             # ⏰ Проверка истечения события
            if now >= dt:
                if event["type"] == "meeting":
                    # Рассылка о начале собрания
                    await send_event_message(event, users, context, f"📣 Собрание \"{event['title']}\" началось!")
                elif event["type"] == "deadline":
                    # Найти задачу и снять её с пользователя
                    task_id = event.get("task_id")
                    if task_id:
                        for t in tasks:
                            if t["id"] == task_id:
                                reserved_by = t.get("reserved_by")
                                if reserved_by:
                                    for u in users:
                                        if reserved_by == u["user_id"]:
                                            if "reserved_tasks" in u and task_id in u["reserved_tasks"]:
                                                u["reserved_tasks"].remove(task_id)
                                t["reserved_by"] = None
                                t["deadline"] = None
                                break

                        await send_event_message(event, users, context, 
                            f"⏰ Дедлайн по задаче \"{event['title']}\" истёк!\n"
                            "Задача изымается и становится доступной другим участникам.")

                    changed = True

                # Удалить событие из списка
                events.remove(event)
                changed = True

        except Exception as e:
            print(f"❌ Ошибка авто-оповещения: {e}")

    if changed:
        save_json(EVENTS_FILE, events)
        save_json(TASKS_FILE, tasks)
        save_json(USERS_FILE, users)

async def send_event_notification(event, users, context, when_str):
    dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
    simple_time = f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"
    event_text = (
        f"⏰ Напоминание! До события <b>{event['title']}</b> осталось {when_str} часа(ов)!\n\n"
        f"🕒 Когда: {simple_time}\n\n"
        f"{event['description']}"
    )
    success, failed = 0, 0

    for u in users:
        if event.get("personal") and u["user_id"] not in event.get("users", []):
            continue

        try:
            await context.bot.send_message(chat_id=u["user_id"], text=event_text, parse_mode="HTML")
            success += 1
        except Exception as e:
            failed += 1
            print(f"❌ Не удалось отправить {u['full_name']}: {e}")

    print(f"📣 Рассылка по событию #{event['id']} ({when_str}h): Успешно: {success}, Ошибок: {failed}")

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
        return  # пользователь не в команде — дальше не идём
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

    # Загрузка события
    if not os.path.exists(EVENTS_FILE):
        await message.reply_text("❌ Файл событий не найден.")
        return

    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)

    event = next((e for e in events if e["id"] == event_id), None)
    if not event:
        await message.reply_text("❌ Событие с таким ID не найдено.")
        return

    dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
    simple_time = f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"
    # Формируем текст уведомления
    event_text = (
        f"📢 <b>{event['title']}</b>\n\n"
        f"🕒 Когда: {simple_time}\n\n"
        f"{event['description']}"
    )

    # Загрузка пользователей
    if not os.path.exists(USERS_FILE):
        await message.reply_text("❌ Файл users.json не найден.")
        return

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    # Рассылка
    success, failed = 0, 0
    for u in users:
        try:
            # Если событие персональное и пользователь не в списке — пропускаем
            if event.get("personal", False) and u["user_id"] not in event.get("users", []):
                continue

            await context.bot.send_message(chat_id=u["user_id"], text=event_text, parse_mode="HTML")
            success += 1
        except Exception as e:
            failed += 1
            print(f"❗ Не удалось отправить {u['full_name']} ({u['user_id']}): {e}")

    await message.reply_text(f"✅ Рассылка завершена.\nУспешно: {success} | Ошибок: {failed}")

async def upcoming_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    user = update.effective_user
    chat = update.effective_chat

    if user is None or chat is None:
        print("❌ update.effective_user или update.effective_chat вернули None")
        return
    
    user_id = user.id

    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            events = json.load(f)
    except Exception as e:
        await context.bot.send_message(chat_id=chat.id, text=f"⚠️ Какие то силы мешают мне видеть будущее:\n<code>{e}</code>", parse_mode="HTML")
        return

    now = datetime.now(WORK_TZ)

    # Отбираем события по времени и доступности (общие или персональные с включением юзера)
    upcoming = []
    for event in events:
        try:
            dt = datetime.fromisoformat(event["datetime"])
            if dt < now:
                continue

            is_personal = event.get("personal", False)
            if not is_personal or (is_personal and user_id in event.get("users", [])):
                upcoming.append((dt, event))
        except:
            continue

    # Сортировка и ограничение до 5 ближайших
    upcoming.sort(key=lambda e: e[0])
    upcoming = upcoming[:5]

    if not upcoming:
        await context.bot.send_message(chat_id=chat.id, text="😌 Видимо в будущем тебя не ждут какие либо события.")
        return

    text = "<b>📅 Ближайшие события:</b>\n\n"
    for dt, event in upcoming:
        date_str = format_datetime_rus(dt)
        text += f"📢 <b>{event['title']}</b>\n🕒 {date_str}\n{event['description']}\n\n"

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
        return  # пользователь не в команде — дальше не идём
    users = load_json(USERS_FILE)
    user_id = update.effective_user.id if update.effective_user else None
    user = next((u for u in users if u["user_id"] == user_id), None)

    if not user:
        await safe_reply(update, context, "⚠️ Почему тебя нет в реестре империи?")
        return ConversationHandler.END

    reserved = user.get("reserved_tasks", [])
    if len(reserved) >= 3:
        await safe_reply(update, context, "⚠️ Ты не можешь брать более 3 задач одновременно!")
        return ConversationHandler.END

    # Пока только один проект
    projects = ["Starky Jungle", "Ideal Abyss", "Short film", "Non-project work"]
    context.user_data["user_id"] = user_id

    markup = ReplyKeyboardMarkup([[p] for p in projects], one_time_keyboard=True, resize_keyboard=True)
    await safe_reply(update, context, "🔧 Выберите проект:", markup)
    return SELECT_PROJECT

async def select_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    project = update.message.text
    context.user_data["project"] = project

    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)
    user_id = context.user_data["user_id"]
    user = next((u for u in users if u["user_id"] == user_id), None)

    if not user:
        return await safe_reply(update, context, "⚠️ Кто ты, воин?")

    roles = [r.lower() for r in user.get("roles", [])]

    relevant_tasks = [
        t for t in tasks
        if t.get("project") == project and
           t.get("reserved_by") is None and
           t.get("type", "").lower() in roles
    ]

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
        msg += (f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
                f"📄 {t['description']}\n"
                f"📂 Тип: {t['type']}\n"
                f"🏆 Баллы: {t['points']}\n"
                f"⏰ Примерное время: {time_str}\n\n")

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
        return await safe_reply(update, context, "⚠️ Не удалось подтвердить выбор")

    tasks = load_json(TASKS_FILE)
    users = load_json(USERS_FILE)
    events = load_json(EVENTS_FILE)
    
    user = next((u for u in users if u["user_id"] == user_id), None)
    reserved = user.get("reserved_tasks", []) if user else []

    if len(reserved) >= 3:
        await safe_reply(update, context, "⚠️ Ты не можешь иметь более 3 задач одновременно!")
        return ConversationHandler.END

    # Если уже есть хотя бы 1, но меньше 3 и не было повторного подтверждения
    if len(reserved) >= 1 and not context.user_data.get("confirmed_multiple"):
        context.user_data["confirmed_multiple"] = True
        await safe_reply(update, context,
            "⚠️ Ты берешь ещё одну задачу.\n"
            "Будь осторожен: более одной задачи может усложнить твою работу.\n"
            "Ты точно уверен? Напиши ещё раз 'да' чтобы подтвердить."
        )
        return CONFIRM
    
    deadline = None
    for task in tasks:
        if task["id"] == task_id:
            task["reserved_by"] = user_id

            # Если дедлайна нет, генерируем его
            if not task.get("deadline"):
                estimated_days = task.get("estimated_days", 7)
                new_deadline = datetime.now(WORK_TZ) + timedelta(days=estimated_days)
                task["deadline"] = new_deadline.isoformat()
                deadline = task["deadline"]
            else:
                deadline = task["deadline"]
            break

    for user in users:
        if user["user_id"] == user_id:
            user.setdefault("reserved_tasks", []).append(task_id)
            break

    if deadline:
        events.append({
            "id": max([e["id"] for e in events], default=0) + 1,
            "type": "deadline",
            "title": f"Дедлайн по задаче #{task_id}",
            "description": "Пожалуйста, завершите работу в срок.",
            "datetime": deadline,
            "notify_users": True,
            "personal": True,
            "users": [user_id],
            "task_id": task_id
        })

    save_json("tasks.json", tasks)
    save_json("users.json", users)
    save_json("events.json", events)

    await safe_reply(update, context, "✅ Миссия принадлежит теперь вам. Проявите себя достойно!")
    return ConversationHandler.END

async def my_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    user_id = update.effective_user.id
    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)

    user = next((u for u in users if u["user_id"] == user_id), None)
    if not user:
        await update.message.reply_text("⚠️ Почему тебя нет в реестре империи?")
        return

    # Найдем задачи, которые зарезервированы текущим пользователем
    reserved_tasks = [t for t in tasks if t.get("reserved_by") == user_id]

    if not reserved_tasks:
        await update.message.reply_text(
            "😔 Вы не обременены миссией\n"
            "Чтобы это исправить, используйте заклинание /get_task"
        )
        return

    msg = "📝 Ваши текущие задачи:\n\n"
    for t in reserved_tasks:
        # ✅ Защита от null дедлайна
        if t.get("deadline"):
            dt = datetime.fromisoformat(t["deadline"]).replace(tzinfo=WORK_TZ)
            date_str = f"{dt.day} {month_names[dt.month]} в {dt.strftime('%H:%M')}"
        else:
            date_str = "Не назначен"
        msg += (f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
                f"📄 {t['description']}\n"
                f"📂 Тип: {t['type']}\n"
                f"🏆 Баллы: {t['points']}\n"
                f"⏰ Дедлайн: {date_str}\n\n")

    await update.message.reply_text(msg, parse_mode="HTML")

async def search_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    user_id = update.effective_user.id
    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)

    user = next((u for u in users if u["user_id"] == user_id), None)
    if not user or "admin" not in user.get("roles", []) and user.get("role") != "admin":
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    # Обработка параметров команды (аргументы)
    args = context.args  # список аргументов после /search_task

    filtered_tasks = tasks

    # Например, фильтр по статусу: reserved/unreserved
    if args:
        arg = args[0].lower()
        if arg == "reserved":
            filtered_tasks = [t for t in tasks if t.get("reserved_by") is not None]
        elif arg == "unreserved":
            filtered_tasks = [t for t in tasks if t.get("reserved_by") is None]
        elif arg == "deadline":
            filtered_tasks = sorted(tasks, key=lambda t: t.get("deadline") or "")

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
            if days == 0:
                time_str = f"{weeks} нед."
            else:
                time_str = f"{weeks} нед. {days} дн."
        else:
            time_str = f"{estimated_days} дн."
        msg += (f"🔹 <b>{t['title']}</b> (#{t['id']})\n"
                f"📄 {t['description']}\n"
                f"📂 Тип: {t['type']}\n"
                f"🏆 Баллы: {t['points']}\n"
                f"⏰ Примерное время: {time_str}\n"
                f"📌 Статус: {reserved_str}\n\n")

    # Разбиваем сообщение на части по 4000 символов, чтобы не превышать лимит Телеграма
    max_len = 4000
    for i in range(0, len(msg), max_len):
        await update.message.reply_text(msg[i:i+max_len], parse_mode="HTML")

async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return  # пользователь не в команде — дальше не идём
    user_id = update.effective_user.id if update.effective_user else None
    users = load_json(USERS_FILE)
    tasks = load_json(TASKS_FILE)
    events = load_json(EVENTS_FILE)  # допустим, события в отдельном файле

    # Проверка, что вызывающий - админ
    user = next((u for u in users if u["user_id"] == user_id), None)
    if not user or ("admin" not in user.get("roles", []) and user.get("role") != "admin"):
        await safe_reply(update, context, "⚠️ У вас нет прав для этой команды.")
        return

    # Проверяем аргументы команды — должен быть ID задачи
    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Укажите ID задачи: /task_done <ID>")
        return
    task_id = int(context.args[0])

    # Найдем задачу по ID
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        await safe_reply(update, context, f"⚠️ Задача #{task_id} не найдена.")
        return

    reserved_by = task.get("reserved_by")
    tasks = [t for t in tasks if t["id"] != task_id]
    save_json(TASKS_FILE, tasks)

    # Удаляем связанные ивенты по task_id (если есть)
    events = [e for e in events if e.get("task_id") != task_id]
    save_json(EVENTS_FILE, events)
    
    if reserved_by:
        for u in users:
            if task_id in u.get("reserved_tasks", []):
                u["reserved_tasks"].remove(task_id)
                break
        save_json(USERS_FILE, users)

    await update.message.reply_text(f"✅ Задача #{task_id} успешно помечена как выполненная и удалена.")

    # Отправляем уведомление пользователю, если задача была зарезервирована
    if reserved_by:
        try:
            await context.bot.send_message(
                chat_id=reserved_by,
                text=(f"🎉 Задача <b>{task['title']}</b> (#{task_id}) "
                      "помечена как выполненная. Спасибо за вашу работу!"),
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

        # Если дата дана без T, но с пробелом, заменяем
        if " " in new_dt_str:
            new_dt_str = new_dt_str.replace(" ", "T")

        new_dt = datetime.fromisoformat(new_dt_str).replace(tzinfo=WORK_TZ)

        tasks = load_json(TASKS_FILE)
        events = load_json(EVENTS_FILE)

        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            await update.message.reply_text(f"❌ Задача с ID #{task_id} не найдена.")
            return

        task["deadline"] = new_dt.isoformat()

        # Обновляем событие или создаём новое
        event = next((e for e in events if e.get("task_id") == task_id), None)
        if event:
            event["datetime"] = new_dt.isoformat()
        else:
            new_event = {
                "id": max([e["id"] for e in events], default=0) + 1,
                "type": "deadline",
                "title": f"Дедлайн по задаче #{task_id}",
                "description": "Обновлён администратором.",
                "datetime": new_dt.isoformat(),
                "notify_users": True,
                "personal": True,
                "users": [task.get("reserved_by")] if task.get("reserved_by") else [],
                "task_id": task_id
            }
            events.append(new_event)

        save_json(TASKS_FILE, tasks)
        save_json(EVENTS_FILE, events)

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
        events = load_json(EVENTS_FILE)
        event = next((e for e in events if e["id"] == event_id), None)

        if not event:
            await update.message.reply_text(f"❌ Событие с ID #{event_id} не найдено.")
            return

        events = [e for e in events if e["id"] != event_id]
        save_json(EVENTS_FILE, events)

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

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Используй так:\n"
            "<code>/add_task project;title;description;type;points;estimated_days</code>\n\n"
            "Пример:\n"
            "<code>/add_task Starky Jungle;Новая механика;Описание механики;программист;20;14</code>",
            parse_mode="HTML"
        )
        return

    try:
        raw_input = " ".join(context.args)
        parts = raw_input.split(";")
        if len(parts) < 6:
            raise ValueError("Недостаточно параметров")

        project = parts[0].strip()
        title = parts[1].strip()
        description = parts[2].strip()
        task_type = parts[3].strip()
        points = int(parts[4].strip())
        estimated_days = int(parts[5].strip())

        tasks = load_json(TASKS_FILE)
        new_id = max([t["id"] for t in tasks], default=0) + 1

        new_task = {
            "id": new_id,
            "project": project,
            "title": title,
            "description": description,
            "type": task_type,
            "points": points,
            "estimated_days": estimated_days,
            "deadline": None,
            "reserved_by": None
        }

        tasks.append(new_task)
        save_json(TASKS_FILE, tasks)

        await update.message.reply_text(
            f"✅ Задача добавлена:\n\n"
            f"<b>{title}</b>\n"
            f"Проект: {project}\n"
            f"Роль: {task_type}\n"
            f"Баллы: {points}\n"
            f"Оценка: {estimated_days} дн.",
            parse_mode="HTML"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

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

    tasks = load_json(TASKS_FILE)
    users = load_json(USERS_FILE)
    events = load_json(EVENTS_FILE)

    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        await update.message.reply_text(f"❌ Задача #{task_id} не найдена.")
        return

    reserved_by = task.get("reserved_by")
    if not reserved_by:
        await update.message.reply_text(f"⚠️ Задача #{task_id} уже свободна.")
        return

    # Найти пользователя и убрать задачу из его списка
    for u in users:
        if u["user_id"] == reserved_by:
            if "reserved_tasks" in u and task_id in u["reserved_tasks"]:
                u["reserved_tasks"].remove(task_id)
            break

    # Обнулить задачу
    task["reserved_by"] = None
    task["deadline"] = None

    # Удалить связанный дедлайн-ивент
    old_events = len(events)
    events = [e for e in events if e.get("task_id") != task_id]
    removed = old_events - len(events)

    save_json(TASKS_FILE, tasks)
    save_json(USERS_FILE, users)
    save_json(EVENTS_FILE, events)

    await update.message.reply_text(
        f"✅ Задача #{task_id} теперь свободна. "
        f"Удалено связанных событий: {removed}."
    )

    # Если хочешь, можно уведомить бывшего исполнителя
    try:
        await context.bot.send_message(
            chat_id=reserved_by,
            text=(f"⚠️ Задача <b>{task['title']}</b> (#{task_id}) "
                  "была снята с вас администратором и теперь доступна другим."),
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

        tasks = load_json(TASKS_FILE)
        users = load_json(USERS_FILE)
        events = load_json(EVENTS_FILE)

        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            await update.message.reply_text(f"❌ Задача #{task_id} не найдена.")
            return

        if task.get("reserved_by"):
            await update.message.reply_text(f"⚠️ Задача #{task_id} уже назначена.")
            return

        user_obj = next((u for u in users if u["username"].lower() == username), None)
        if not user_obj:
            await update.message.reply_text(f"❌ Пользователь @{username} не найден.")
            return

        # Проставляем резерв
        user_id = user_obj["user_id"]
        task["reserved_by"] = user_id

        # Генерируем дедлайн, если его ещё нет
        if not task.get("deadline"):
            estimated_days = task.get("estimated_days", 7)
            deadline = datetime.now(WORK_TZ) + timedelta(days=estimated_days)
            task["deadline"] = deadline.isoformat()
        else:
            deadline = datetime.fromisoformat(task["deadline"])

        # Добавляем задачу в список пользователя
        user_obj.setdefault("reserved_tasks", []).append(task_id)

        # Добавляем ивент-дедлайн
        new_event = {
            "id": max([e["id"] for e in events], default=0) + 1,
            "type": "deadline",
            "title": f"Дедлайн по задаче #{task_id}",
            "description": "Администратор назначил вам задачу.",
            "datetime": deadline.isoformat(),
            "notify_users": True,
            "personal": True,
            "users": [user_id],
            "task_id": task_id
        }
        events.append(new_event)

        # Сохраняем изменения
        save_json(TASKS_FILE, tasks)
        save_json(USERS_FILE, users)
        save_json(EVENTS_FILE, events)

        await update.message.reply_text(
            f"✅ Задача #{task_id} успешно назначена пользователю @{username}."
        )

        # Отправляем уведомление назначенному пользователю
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"📌 Вам назначена новая задача!\n\n"
                    f"<b>{task['title']}</b> (#{task_id})\n"
                    f"{html.escape(task['description'])}\n\n"
                    f"⏰ Дедлайн: {format_datetime_rus(deadline)}"
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
            "⚠️ *Сообщение будет отправлено всем участникам команды*\n"
            "Напишите `отмена`, чтобы отменить рассылку.",
            parse_mode="MarkdownV2"
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
        events = load_json(EVENTS_FILE)
        if not events:
            await update.message.reply_text("📭 Список событий пуст.")
            return

        now = datetime.now(WORK_TZ)

        # Сортируем по дате и времени
        events_sorted = sorted(events, key=lambda e: e.get("datetime") or "")

        msg = "<b>📅 Все события:</b>\n\n"
        for event in events_sorted:
            dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
            status = "✅ Актуально" if dt >= now else "⌛ Уже прошло"

            personal_str = ""
            if event.get("personal", False):
                personal_str = " (Персональное)"
            
            msg += (
                f"🔹 <b>{event['title']}</b>{personal_str}\n"
                f"🗂️ Тип: {event['type']}\n"
                f"🕒 Когда: {format_datetime_rus(dt)}\n"
                f"📄 {event['description']}\n"
                f"📌 Статус: {status}\n"
                f"🆔 ID: {event['id']}\n\n"
            )

        # Если сообщение слишком большое — разбиваем
        max_len = 4000
        for i in range(0, len(msg), max_len):
            await update.message.reply_text(msg[i:i+max_len], parse_mode="HTML")

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


app = ApplicationBuilder().token("7833612109:AAGfBTL2pn5WqDoWLwFYA1cZBd-XF7VzJ_o").build()
app.bot.set_my_commands([
    BotCommand("start", "Моё приветствие"),
    BotCommand("help", "Все твои доступные заклинания"),
    BotCommand("profile", "Открыть профиль"),
    BotCommand("upcoming_events", "Посмотреть грядущие события"),
    BotCommand("my_points", "Увидеть свои баллы"),
    BotCommand("my_task", "Посмотреть свои задачи"),
    BotCommand("get_task", "Взять новую задачу"),
])
job_queue = app.job_queue
job_queue.run_repeating(event_auto_notify, interval=300, first=10)
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
app.add_handler(CommandHandler("add_task", add_task))
app.add_handler(CommandHandler("unassign_task", unassign_task))
app.add_handler(CommandHandler("assign_task", assign_task_to_user))
app.add_handler(CommandHandler("broadcast", broadcast_message))
app.add_handler(CommandHandler("show_all_events", show_all_events))
app.add_handler(CommandHandler("calendar", calendar_command))
app.add_handler(CallbackQueryHandler(calendar_callback, pattern=r"^cal:"))
app.add_handler(get_task_handler())
app.run_polling()
