import os
import re
from datetime import datetime, timedelta
import html

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
)

from database.db import SessionLocal
from repositories.user_repository import UserRepository
from repositories.task_repository import TaskRepository
from repositories.event_repository import EventRepository
from services.user_service import UserService
from services.task_service import TaskService
from services.event_service import EventService
from services.points_service import PointsService
from repositories.log_repository import LogRepository
from services.log_service import LogService
from handlers.give_points_command import build_give_points_handler
from config import *
import traceback

SELECT_PROJECT, SELECT_TASK, CONFIRM = range(3)
TASK_DONE_K = 200
MEETING_ATTENDANCE_INPUT = 300
SUBMIT_SELECT_TASK = 400

# Interactive task-management command states
ASSIGN_SELECT_TASK, ASSIGN_SELECT_USER = range(500, 502)
UNASSIGN_SELECT_TASK, UNASSIGN_SELECT_USER = range(510, 512)
BLOCK_SELECT_TASK = 520
UNBLOCK_SELECT_TASK = 530

# Points history pagination
PH_PER_PAGE = 10

(
    ADD_PROJECT,
    ADD_TITLE,
    ADD_DESC,
    ADD_ROLE,
    ADD_CATEGORY,
    ADD_PRIORITY,
    ADD_STATUS,
    ADD_MAX_ASSIGNEES,
    ADD_ESTIMATED_DAYS,
    ADD_REVIEW_REQUIRED,
    ADD_J,
    ADD_C,
    ADD_T,
    ADD_CONFIRM,
) = range(100, 114)




# =========================
# DB HELPERS
# =========================

def with_user_service(func):
    db = SessionLocal()
    try:
        service = UserService(UserRepository(db))
        return func(service)
    finally:
        db.close()

def with_task_service(func):
    db = SessionLocal()
    try:
        service = TaskService(TaskRepository(db))
        return func(service)
    finally:
        db.close()

def with_event_service(func):
    db = SessionLocal()
    try:
        service = EventService(EventRepository(db))
        return func(service)
    finally:
        db.close()

def with_event_repo(func):
    db = SessionLocal()
    try:
        repo = EventRepository(db)
        return func(repo)
    finally:
        db.close()

def with_points_service(func):
    db = SessionLocal()
    try:
        service = PointsService(
            UserRepository(db),
            LogRepository(db),
        )
        return func(service)
    finally:
        db.close()

def create_points_service():
    db = SessionLocal()
    service = PointsService(
        UserRepository(db),
        LogRepository(db),
    )
    service._db = db
    return service

def with_log_service(func):
    db = SessionLocal()
    try:
        service = LogService(LogRepository(db))
        return func(service)
    finally:
        db.close()

# =========================
# COMMON HELPERS
# =========================

def format_datetime_rus(dt: datetime) -> str:
    return f"{dt.day} {MONTH_NAMES[dt.month]} в {dt.strftime('%H:%M')}"

def format_task_status(status: str | None) -> str:
    if not status:
        return "⚪ Неизвестно"

    emoji, _ = TASK_STATUS_LABELS.get(status, ("⚪", status))
    title = TASK_STATUS_RU.get(status, status)
    return f"{emoji} {title}"

def get_internal_user_id_by_tg(tg_user_id: int) -> int | None:
    def _run(user_service: UserService):
        user = user_service.get_user_by_telegram_id(tg_user_id)
        return user.id if user else None
    return with_user_service(_run)

def get_user_by_id(user_id: int):
    def _run(user_service: UserService):
        user = user_service.get_user_by_telegram_id(user_id)
        if not user:
            return None
        return user_service.user_to_legacy_dict(user)

    return with_user_service(_run)

async def safe_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, markup=None, parse_mode=None):
    target = update.effective_message
    if target:
        return await target.reply_text(text, reply_markup=markup, parse_mode=parse_mode)

    if update.effective_chat:
        return await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=markup,
            parse_mode=parse_mode,
        )

def clear_add_task_data(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "add_project_id",
        "add_project_title",
        "add_title",
        "add_description",
        "add_role_id",
        "add_role_title",
        "add_category_id",
        "add_category_title",
        "add_priority",
        "add_status",
        "add_max_assignees",
        "add_estimated_days",
        "add_review_required",
        "add_j",
        "add_c",
        "add_t",
    ]:
        context.user_data.pop(key, None)

def clear_meeting_attendance_data(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "meeting_attendance_event_id",
        "meeting_attendance_title",
        "meeting_attendance_project_id",
    ]:
        context.user_data.pop(key, None)

def parse_attendance_usernames(raw_text: str) -> list[str]:
    parts = re.split(r"[\s,\n]+", raw_text.strip())
    result = []

    for part in parts:
        name = part.strip().lstrip("@").lower()
        if name:
            result.append(name)

    return list(dict.fromkeys(result))

def get_teamwork_project_id():
    def _run(task_service: TaskService):
        projects = task_service.list_projects()
        for p in projects:
            if getattr(p, "title", None) == "Teamwork":
                return p.id
        return None

    return with_task_service(_run)

async def check_user_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False

    def _run(user_service: UserService):
        return user_service.is_team_member(user_id)

    is_member = with_user_service(_run)
    if is_member:
        return True

    await safe_reply(
        update,
        context,
        "⚠️ Извините, бот работает только с участниками команды.\nПо вопросам обращайтесь к @StanPaige.",
    )
    return False

async def render_task_page(update_or_query, context, project: str, user_id: int, page: int = 1):
    user_record = get_user_by_id(user_id)
    if not user_record:
        text = "⚠️ Пользователь не найден."
        if hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(text)
        else:
            await safe_reply(update_or_query, context, text)
        return

    def _run(task_service: TaskService):
        return task_service.get_available_tasks_for_user_paginated(
            project=project,
            user_record=user_record,
            page=page,
            per_page=5,
        )

    data = with_task_service(_run)
    items = data["items"]

    if not items:
        text = "😔 Сейчас нет доступных задач для твоей роли."
        if hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(text)
        else:
            await safe_reply(update_or_query, context, text, ReplyKeyboardRemove())
        return

    def _format(task_service: TaskService):
        return [task_service.format_task_card(item) for item in items]

    cards = with_task_service(_format)
    text = "\n\n".join(cards)

    buttons = []
    for item in items:
        buttons.append([
            InlineKeyboardButton(
                text=f"Взять #{item['id']}",
                callback_data=f"take_task:{item['id']}",
            )
        ])

    nav_row = []
    if data["has_prev"]:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"task_page:{page-1}"))
    nav_row.append(InlineKeyboardButton(f"{data['page']}", callback_data="noop"))
    if data["has_next"]:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"task_page:{page+1}"))

    if nav_row:
        buttons.append(nav_row)

    markup = InlineKeyboardMarkup(buttons)

    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await safe_reply(update_or_query, context, text, markup=markup, parse_mode="HTML")

async def run_overdue_check(context: ContextTypes.DEFAULT_TYPE | None = None):
    now = datetime.now(WORK_TZ)

    def _run(task_service: TaskService):
        return task_service.mark_overdue_tasks(now)

    updated_tasks = with_task_service(_run)
    if not updated_tasks:
        return

    for task in updated_tasks:
        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        for link in active_links:
            user = getattr(link, "user", None)
            if not user:
                continue

            try:
                if context:
                    await context.bot.send_message(
                        chat_id=user.telegram_user_id,
                        text=(
                            f"🔥 <b>Задача просрочена</b>\n\n"
                            f"🆔 #{task.id}\n"
                            f"🧩 <b>{task.title}</b>\n"
                            f"📌 Новый статус: {format_task_status('overdue')}"
                        ),
                        parse_mode="HTML",
                    )
            except Exception:
                pass

def parse_ranged_int(text: str, min_value: int, max_value: int, field_name: str):
    if not text.isdigit():
        return None, f"⚠️ {field_name} должен быть целым числом."

    value = int(text)
    if not (min_value <= value <= max_value):
        return None, f"⚠️ {field_name} должен быть в диапазоне {min_value}-{max_value}."

    return value, None

def calculate_task_points(task) -> int:
    j = int(task.j_value or 0)
    c = int(task.c_value or 0)
    t = int(task.t_value or 0)

    base_points = j + c + t
    multiplier = get_priority_multiplier(getattr(task, "priority", None))

    return max(0, round(base_points * multiplier))

def split_points_among_assignees(total_points: int, assignees_count: int) -> list[int]:
    if assignees_count <= 0:
        return []

    base = total_points // assignees_count
    remainder = total_points % assignees_count

    result = [base] * assignees_count
    for i in range(remainder):
        result[i] += 1

    return result

def apply_k_bonus(base_points: int, k_bonus: int) -> int:
    return max(0, base_points + k_bonus)

def parse_k_bonus(text: str):
    text = text.strip()

    if not re.fullmatch(r"-?\d+", text):
        return None, f"⚠️ K должен быть целым числом от {K_BONUS_MIN} до {K_BONUS_MAX}."

    value = int(text)
    if not (K_BONUS_MIN <= value <= K_BONUS_MAX):
        return None, f"⚠️ K должен быть в диапазоне от {K_BONUS_MIN} до {K_BONUS_MAX}."

    return value, None

def clear_task_done_data(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "task_done_task_id",
        "task_done_task_title",
        "task_done_actor_db_id",
        "task_done_active_users",
        "task_done_base_points",
        "task_done_project_id",
        "task_done_project_title",
        "task_done_j_value",
        "task_done_c_value",
        "task_done_t_value",
    ]:
        context.user_data.pop(key, None)

def format_task_priority(priority: str | None) -> str:
    if not priority:
        return "⚪ Неизвестный"
    return PRIORITY_LABELS.get(priority, priority)

def get_priority_multiplier(priority: str | None) -> float:
    if not priority:
        return 1.0
    return PRIORITY_MULTIPLIERS.get(priority, 1.0)

async def send_event_notification(event: dict, context: ContextTypes.DEFAULT_TYPE, wave: str):
    dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
    when_str = format_datetime_rus(dt)

    if event.get("type") == "meeting":
        if wave == "24":
            prefix = "📢 Напоминание о собрании за 24 часа"
        elif wave == "2":
            prefix = "📢 Напоминание о собрании за 2 часа"
        elif wave == "start":
            prefix = "📢 Собрание начинается"
        else:
            prefix = "📢 Напоминание о собрании"
    else:
        if wave == "24":
            prefix = "⏰ Напоминание за 24 часа"
        elif wave == "2":
            prefix = "⏰ Напоминание за 2 часа"
        else:
            prefix = "⏰ Напоминание"

    task_line = f"\n🆔 Задача: #{event['task_id']}" if event.get("task_id") else ""
    desc = event.get("description") or "Без описания"

    text = (
        f"{prefix}\n\n"
        f"📌 <b>{html.escape(event['title'])}</b>{task_line}\n"
        f"🕒 {when_str}\n"
        f"📝 {html.escape(desc)}"
    )

    recipient_ids = []

    if event.get("personal"):
        recipient_ids = event.get("users", [])
    else:
        def _team_users(user_service: UserService):
            users = user_service.user_repo.list_active_team_members()
            return [u.telegram_user_id for u in users if getattr(u, "telegram_user_id", None)]

        recipient_ids = with_user_service(_team_users)

    for tg_user_id in recipient_ids:
        try:
            await context.bot.send_message(
                chat_id=tg_user_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

async def event_auto_notify(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(WORK_TZ)

    def _events(event_service: EventService):
        return event_service.get_events_for_notifications(now)

    events = with_event_service(_events)

    for event in events:
        try:
            dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
            delta_hours = (dt - now).total_seconds() / 3600

            if (
                REMINDER_24H_MIN_HOURS <= delta_hours <= REMINDER_24H_MAX_HOURS
                and not event.get("notified_24h")
            ):
                await send_event_notification(event, context, "24")

                def _mark24(event_service: EventService):
                    return event_service.mark_notified_24h(event["id"])

                with_event_service(_mark24)

            if (
                REMINDER_2H_MIN_HOURS <= delta_hours <= REMINDER_2H_MAX_HOURS
                and not event.get("notified_2h")
            ):
                await send_event_notification(event, context, "2")

                def _mark2(event_service: EventService):
                    return event_service.mark_notified_2h(event["id"])

                with_event_service(_mark2)

            if now >= dt:
                if event.get("type") == "meeting":
                    if not event.get("notified_30m"):
                        await send_event_notification(event, context, "start")

                        def _mark_start(event_service: EventService):
                            return event_service.mark_notified_30m(event["id"])

                        with_event_service(_mark_start)

                    continue

                if event.get("task_id"):
                    await run_overdue_check(context)

                def _archive(event_service: EventService):
                    return event_service.archive_event(event["id"])

                with_event_service(_archive)

        except Exception:
            pass

def get_next_weekday_datetime(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    days_ahead = (weekday - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)

    if candidate <= now:
        candidate += timedelta(days=7)

    return candidate

async def ensure_weekly_meeting_exists(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(WORK_TZ)

    def _get(event_service: EventService):
        return event_service.get_next_team_meeting(now)

    next_meeting = with_event_service(_get)

    if next_meeting:
        dt = datetime.fromisoformat(next_meeting["datetime"]).replace(tzinfo=WORK_TZ)
        delta_days = (dt - now).total_seconds() / 86400

        if delta_days <= MEETING_AUTO_CREATE_DAYS_AHEAD:
            return

    meeting_dt = get_next_weekday_datetime(
        now,
        DEFAULT_MEETING_WEEKDAY,
        DEFAULT_MEETING_HOUR,
        DEFAULT_MEETING_MINUTE,
    )

    def _create(event_service: EventService):
        return event_service.create_team_meeting(
            title="Еженедельное собрание команды",
            description="Автоматически созданное еженедельное собрание.",
            dt_value=meeting_dt,
            created_by_user_id=None,
        )

    with_event_service(_create)

def get_user_permissions(user_id: int | None) -> set[str]:
    if not user_id:
        return set()

    def _run(user_service: UserService):
        return set(user_service.get_permission_codes(user_id))

    return with_user_service(_run)

def is_super_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id == ADMIN_ID)

def has_permission(user_id: int | None, permission_code: str) -> bool:
    if not user_id:
        return False

    if is_super_admin(user_id):
        return True

    permissions = get_user_permissions(user_id)
    return "admin_full" in permissions or permission_code in permissions

def is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False

    if is_super_admin(user_id):
        return True

    permissions = get_user_permissions(user_id)
    admin_codes = {
        "admin_full",
        "manage_users",
        "manage_projects",
        "manage_tasks",
        "review_tasks",
        "manage_events",
        "manage_points",
        "view_admin_reports",
    }
    return any(code in permissions for code in admin_codes)

def build_user_bot_commands(user_id: int) -> list[BotCommand]:
    commands = [
        BotCommand("start", "Моё приветствие"),
        BotCommand("help", "Все доступные команды"),
        BotCommand("upcoming_events", "Посмотреть грядущие события"),
        BotCommand("my_points", "Увидеть свои баллы"),
        BotCommand("leaderboard", "Общий рейтинг"),
        BotCommand("set_my_status", "Изменить свой статус (active/inactive)"),
        BotCommand("my_task", "Посмотреть свои задачи"),
        BotCommand("submit_task", "Отправить задачу на проверку"),
        BotCommand("task_checklist", "Чеклист задачи"),
        BotCommand("toggle_checkitem", "Отметить пункт чеклиста"),
        BotCommand("get_task", "Взять новую задачу"),
    ]

    if has_permission(user_id, "manage_points"):
        commands.extend([
            BotCommand("give_points", "Добавить баллы"),
            BotCommand("check_points", "Проверить баллы"),
            BotCommand("points_history", "История начислений"),
        ])

    if has_permission(user_id, "manage_tasks"):
        commands.extend([
            BotCommand("add_task", "Создать задачу"),
            BotCommand("assign_task", "Назначить задачу"),
            BotCommand("unassign_task", "Снять с задачи"),
            BotCommand("block_task", "Заблокировать задачу"),
            BotCommand("unblock_task", "Разблокировать задачу"),
            BotCommand("set_deadline", "Изменить дедлайн"),
            BotCommand("add_checkitem", "Добавить пункт чеклиста"),
            BotCommand("delete_checkitem", "Удалить пункт чеклиста"),
            BotCommand("run_overdue", "Проверить просрочки"),
            BotCommand("overdue_tasks", "Просроченные задачи"),
            BotCommand("run_idle_check", "Напомнить о задачах простаивающим"),
        ])

    if has_permission(user_id, "review_tasks"):
        commands.extend([
            BotCommand("task_done", "Подтвердить задачу"),
            BotCommand("return_task", "Вернуть на доработку"),
        ])

    if has_permission(user_id, "manage_events"):
        commands.extend([
            BotCommand("set_next_meeting", "Перенести собрание"),
            BotCommand("finish_meeting", "Завершить собрание"),
        ])
    
    if has_permission(user_id, "manage_users"):
        commands.extend([
            "/set_user_status — изменить статус участника",
        ])
    
    if has_permission(user_id, "manage_users"):
        commands.append(BotCommand("set_user_status", "Изменить статус участника"))

    if has_permission(user_id, "view_admin_reports"):
        commands.extend([
            BotCommand("logs", "Посмотреть логи"),
            BotCommand("admin_help", "Админ-команды"),
        ])

    return commands

async def apply_user_command_scope(bot, user_id: int):
    commands = build_user_bot_commands(user_id)
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))

def get_user_active_tasks_for_ui(telegram_user_id: int):
    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(telegram_user_id)
        return [task_service.task_to_legacy_dict(t) for t in tasks]
    return with_task_service(_run)

# =========================
# USER COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    user_id = update.effective_user.id

    await apply_user_command_scope(context.bot, user_id)
    main_kb = build_main_menu(user_id)

    await safe_reply(
        update,
        context,
        "Здравствуй. Бот запущен и готов к работе. Используй /help, чтобы увидеть доступные тебе команды.",
        markup=main_kb,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    await apply_user_command_scope(context.bot, user_id)

    lines = [
        "📖 <b>Доступные тебе команды:</b>",
        "",
        "/start — запустить бота",
        "/help — показать список команд",
        "/upcoming_events — ближайшие события",
        "/my_points — мои баллы",
        "/leaderboard — общий рейтинг участников",
        "/set_my_status — переключить свой статус (active/inactive)",
        "/my_task — мои текущие задачи",
        "/submit_task — отправить свою задачу на проверку",
        "/task_checklist — посмотреть чеклист задачи",
        "/toggle_checkitem — отметить пункт чеклиста",
        "/get_task — взять новую задачу",
    ]

    if has_permission(user_id, "manage_points"):
        lines.extend([
            "",
            "<b>Баллы:</b>",
            "/give_points — добавить баллы",
            "/check_points — проверить баллы участника",
            "/points_history — история начислений",
        ])

    if has_permission(user_id, "manage_tasks"):
        lines.extend([
            "",
            "<b>Задачи:</b>",
            "/add_task — создать задачу",
            "/assign_task — назначить задачу участнику",
            "/unassign_task — снять участника с задачи",
            "/block_task — заблокировать задачу",
            "/unblock_task — разблокировать задачу",
            "/set_deadline — изменить дедлайн",
            "/run_overdue — проверить просроченные задачи",
            "/overdue_tasks — показать просрочки",
            "/add_checkitem — добавить чеклист",
            "/delete_checkitem — удалить пункт чеклиста",
        ])

    if has_permission(user_id, "review_tasks"):
        lines.extend([
            "",
            "<b>Проверка:</b>",
            "/task_done — подтвердить задачу",
            "/return_task — вернуть задачу в работу",
        ])

    if has_permission(user_id, "manage_events"):
        lines.extend([
            "",
            "<b>События:</b>",
            "/set_next_meeting — перенести собрание",
            "/finish_meeting — завершить собрание и внести attendance",
        ])

    if has_permission(user_id, "view_admin_reports"):
        lines.extend([
            "",
            "<b>Отчёты и логи:</b>",
            "/logs — посмотреть логи",
            "/admin_help — расширенная справка",
        ])

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def upcoming_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    user = update.effective_user
    print("=== /upcoming_events ===")
    print("user_id =", user.id)
    if not user:
        return

    now = datetime.now(WORK_TZ)

    def _run(event_service: EventService):
        return event_service.get_upcoming_for_user(user.id, now, limit=5)

    upcoming = with_event_service(_run)
    print("upcoming =", upcoming)
    if not upcoming:
        await safe_reply(update, context, "📅 Ближайших событий для тебя не найдено.")
        return

    lines = ["<b>📅 Ближайшие события:</b>", ""]
    for event in upcoming:
        dt = datetime.fromisoformat(event["datetime"]).replace(tzinfo=WORK_TZ)
        lines.append(
            f"📢 <b>{event['title']}</b>\n"
            f"🕒 {format_datetime_rus(dt)}\n"
            f"{event.get('description') or ''}"
        )
        lines.append("")

    await safe_reply(update, context, "\n".join(lines).strip(), parse_mode="HTML")


async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    tg_user_id = update.effective_user.id

    def _run(points_service: PointsService):
        return points_service.get_user_points_summary(tg_user_id)
    
    print("=== /my_points ===")
    print("telegram_user_id =", tg_user_id)

    summary = with_points_service(_run)
    print("summary =", summary)
    if not summary:
        await safe_reply(update, context, "❌ Ты отсутствуешь в системе реестра.")
        return

    projects = summary.get("projects", {})
    if not projects:
        await safe_reply(update, context, "📊 У тебя пока нет баллов.")
        return

    lines = ["📊 <b>Твои баллы и доля по проектам:</b>", ""]
    for project_name in sorted(projects.keys()):
        item = projects[project_name]
        points = item.get("points", 0)
        percent = float(item.get("percent_rate", 0.0)) * 100
        lines.append(f"🔹 <b>{project_name}</b>: {points} баллов ({round(percent)}%)")

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def set_my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    if not context.args or context.args[0].lower() not in {"active", "inactive"}:
        await safe_reply(update, context, "⚠️ Используй: /set_my_status active или /set_my_status inactive")
        return

    new_status = context.args[0].lower() == "active"
    tg_id = update.effective_user.id

    def _set(user_service: UserService):
        return user_service.set_active_status(tg_id, new_status)

    ok = with_user_service(_set)
    if not ok:
        await safe_reply(update, context, "❌ Не удалось изменить статус.")
        return

    actor_db_id = get_internal_user_id_by_tg(tg_id)

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="set_own_status",
            entity_type="user",
            entity_id=actor_db_id,
            payload={"is_active": new_status},
        )

    with_log_service(_log)

    status_text = "🟢 активен" if new_status else "⚪ неактивен"
    await safe_reply(
        update,
        context,
        f"✅ Твой статус обновлён: {status_text}.\n\n"
        + ("" if new_status else
           "ℹ️ В статусе «неактивен» тебе не приходят уведомления о собраниях, "
           "и ты не можешь брать новые задачи — ни сам, ни через назначение администратором.")
    )

async def my_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    print("=== /my_task ===")
    print("user_id =", user_id)

    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(user_id)

        result = []
        for t in tasks:
            task_dict = task_service.task_to_legacy_dict(t)
            checklist_text = task_service.get_checklist_text_for_task(t.id)
            result.append({
                "task": task_dict,
                "checklist_text": checklist_text,
            })

        return result

    reserved_tasks = with_task_service(_run)
    print("reserved_tasks =", reserved_tasks)
    if not reserved_tasks:
        await safe_reply(update, context, "😔 У тебя сейчас нет активных задач. Используй /get_task.")
        return

    lines = ["📝 <b>Твои текущие задачи:</b>", ""]
    for item in reserved_tasks:
        task = item["task"]
        checklist_text = item["checklist_text"]

        status_str = format_task_status(task.get("status"))
        if task.get("deadline"):
            dt = datetime.fromisoformat(task["deadline"]).replace(tzinfo=WORK_TZ)
            deadline_str = format_datetime_rus(dt)
        else:
            deadline_str = "Не назначен"

        checklist_block = ""
        if checklist_text:
            checklist_block = f"\n📋 Чеклист:\n{checklist_text}"

        priority_str = format_task_priority(task.get("priority"))

        lines.append(
            f"🔹 <b>{task['title']}</b> (#{task['id']})\n"
            f"📌 Статус: {status_str}\n"
            f"⚡ Приоритет: {priority_str}\n"
            f"📄 {task.get('description') or 'Без описания'}\n"
            f"📂 Тип: {task['type']}\n"
            f"🏆 Баллы: {task['points']}\n"
            f"⏰ Дедлайн: {deadline_str}"
            f"{checklist_block}"
        )
        lines.append("")

    await safe_reply(update, context, "\n".join(lines).strip(), parse_mode="HTML")


# =========================
# Submit task
# =========================

async def submit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    telegram_user_id = update.effective_user.id

    if context.args and context.args[0].isdigit():
        task_id = int(context.args[0])

        async def reply_func(text: str):
            await safe_reply(update, context, text)

        await submit_task_by_id_common(
            task_id=task_id,
            telegram_user_id=telegram_user_id,
            actor_username=update.effective_user.username,
            reply_func=reply_func,
            bot=context.bot,
        )
        return

    if is_admin(telegram_user_id):
        def _run(task_service: TaskService):
            return task_service.get_submittable_tasks_for_admin()
    else:
        def _run(task_service: TaskService):
            return task_service.get_submittable_tasks_for_user(telegram_user_id)

    tasks = with_task_service(_run)

    if not tasks:
        await safe_reply(update, context, "😔 Нет задач, которые можно отправить на проверку.")
        return

    buttons = []
    for task in tasks[:20]:
        buttons.append([
            InlineKeyboardButton(
                text=f"#{task.id} • {task.title}",
                callback_data=f"submit_select:{task.id}"
            )
        ])

    markup = InlineKeyboardMarkup(buttons)
    await safe_reply(
        update,
        context,
        "🟡 Выбери задачу для отправки на проверку:",
        markup=markup
    )

async def submit_task_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    telegram_user_id = update.effective_user.id
    actor_db_id = get_internal_user_id_by_tg(telegram_user_id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    def _can_submit(task_service: TaskService):
        return task_service.can_user_submit_task(task, telegram_user_id)

    is_owner = with_task_service(_can_submit)
    if not is_owner and not is_admin(telegram_user_id):
        await safe_reply(update, context, "❌ Ты не можешь отправить эту задачу на проверку.")
        return

    if task.status != "in_progress":
        await safe_reply(
            update,
            context,
            f"⚠️ Задачу можно отправить на проверку только из статуса 'В работе'. Сейчас: {format_task_status(task.status)}"
        )
        return

    def _check(task_service: TaskService):
        return task_service.can_submit_task_to_review(task_id)

    can_submit, reason = with_task_service(_check)
    if not can_submit:
        await safe_reply(
            update,
            context,
            f"⚠️ Нельзя отправить задачу #{task_id} на проверку.\n{reason}"
        )
        return

    def _submit(task_service: TaskService):
        return task_service.submit_for_review(task_id)

    updated = with_task_service(_submit)
    if not updated:
        await safe_reply(update, context, f"❌ Не удалось отправить задачу #{task_id} на проверку.")
        return

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="submit_task_for_review",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task.title}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="in_progress",
            new_value="review",
            note="Задача отправлена на проверку"
        )

    with_log_service(_log)

    await safe_reply(update, context, f"🟡 Задача #{task_id} отправлена на проверку.")

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🟡 <b>Задача отправлена на проверку</b>\n\n"
                f"🆔 #{task_id}\n"
                f"🧩 <b>{task.title}</b>\n"
                f"👤 Отправил: @{update.effective_user.username or 'без username'}\n"
                f"📌 Новый статус: {format_task_status('review')}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

async def submit_task_by_id_common(
    *,
    task_id: int,
    telegram_user_id: int,
    actor_username: str | None,
    reply_func,
    bot,
):
    actor_db_id = get_internal_user_id_by_tg(telegram_user_id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await reply_func(f"❌ Задача #{task_id} не найдена.")
        return

    def _can_submit(task_service: TaskService):
        return task_service.can_user_submit_task(task, telegram_user_id)

    is_owner = with_task_service(_can_submit)
    if not is_owner and not is_admin(telegram_user_id):
        await reply_func("❌ Ты не можешь отправить эту задачу на проверку.")
        return

    if task.status != "in_progress":
        await reply_func(
            f"⚠️ Задачу можно отправить на проверку только из статуса 'В работе'. Сейчас: {format_task_status(task.status)}"
        )
        return

    def _check(task_service: TaskService):
        return task_service.can_submit_task_to_review(task_id)

    can_submit, reason = with_task_service(_check)
    if not can_submit:
        await reply_func(
            f"⚠️ Нельзя отправить задачу #{task_id} на проверку.\n{reason}"
        )
        return

    def _submit(task_service: TaskService):
        return task_service.submit_for_review(task_id)

    updated = with_task_service(_submit)
    if not updated:
        await reply_func(f"❌ Не удалось отправить задачу #{task_id} на проверку.")
        return

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="submit_task_for_review",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task.title}
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="in_progress",
            new_value="review",
            note="Задача отправлена на проверку"
        )

    with_log_service(_log)

    await reply_func(f"🟡 Задача #{task_id} отправлена на проверку.")

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🟡 <b>Задача отправлена на проверку</b>\n\n"
                f"🆔 #{task_id}\n"
                f"🧩 <b>{task.title}</b>\n"
                f"👤 Отправил: @{actor_username or 'без username'}\n"
                f"📌 Новый статус: {format_task_status('review')}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

async def submit_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("submit_select:"):
        return

    task_id = int(data.split(":")[1])
    telegram_user_id = update.effective_user.id

    async def reply_func(text: str):
        await query.edit_message_text(text)

    await submit_task_by_id_common(
        task_id=task_id,
        telegram_user_id=telegram_user_id,
        actor_username=update.effective_user.username,
        reply_func=reply_func,
        bot=context.bot,
    )

# =========================
# Checklist
# =========================

async def task_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    telegram_user_id = update.effective_user.id

    # Старый режим по ID оставляем
    if context.args and context.args[0].isdigit():
        task_id = int(context.args[0])
        return await show_task_checklist_by_id(update, context, task_id)

    # Новый режим — выбор задачи кнопками
    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(telegram_user_id)
        return [task_service.task_to_legacy_dict(t) for t in tasks]

    tasks = with_task_service(_run)

    if not tasks:
        await safe_reply(update, context, "😔 У тебя нет активных задач.")
        return

    buttons = []
    for task in tasks[:20]:
        buttons.append([
            InlineKeyboardButton(
                text=f"#{task['id']} • {task['title']}",
                callback_data=f"checklist_task_select:{task['id']}"
            )
        ])

    markup = InlineKeyboardMarkup(buttons)
    await safe_reply(
        update,
        context,
        "📋 Выбери задачу, чтобы посмотреть чеклист:",
        markup=markup
    )

async def show_task_checklist_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    def _get(task_service: TaskService):
        task = task_service.get_task_by_id(task_id)
        items = task_service.list_checklists(task_id)
        return task, items

    result = with_task_service(_get)
    task, items = result

    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    def _fmt(task_service: TaskService):
        return task_service.format_checklist(items)

    checklist_text = with_task_service(_fmt)

    await safe_reply(
        update,
        context,
        f"📋 <b>Чеклист задачи</b>\n\n"
        f"🆔 #{task.id}\n"
        f"🧩 <b>{html.escape(task.title)}</b>\n\n"
        f"{checklist_text}",
        parse_mode="HTML",
    )

async def task_checklist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("checklist_task_select:"):
        return

    task_id = int(data.split(":")[1])

    def _get(task_service: TaskService):
        task = task_service.get_task_by_id(task_id)
        items = task_service.list_checklists(task_id)
        return task, items

    task, items = with_task_service(_get)

    if not task:
        await query.edit_message_text(f"❌ Задача #{task_id} не найдена.")
        return

    def _fmt(task_service: TaskService):
        return task_service.format_checklist(items)

    checklist_text = with_task_service(_fmt)

    await query.edit_message_text(
        f"📋 <b>Чеклист задачи</b>\n\n"
        f"🆔 #{task.id}\n"
        f"🧩 <b>{html.escape(task.title)}</b>\n\n"
        f"{checklist_text}",
        parse_mode="HTML",
    )

async def toggle_checkitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return

    telegram_user_id = update.effective_user.id

    # Старый режим по ID оставляем
    if context.args and context.args[0].isdigit():
        checklist_id = int(context.args[0])
        return await toggle_checkitem_by_id(update, context, checklist_id)

    def _run(task_service: TaskService):
        tasks = task_service.get_user_tasks(telegram_user_id)
        return [task_service.task_to_legacy_dict(t) for t in tasks]

    tasks = with_task_service(_run)

    if not tasks:
        await safe_reply(update, context, "😔 У тебя нет активных задач.")
        return

    buttons = []
    for task in tasks[:20]:
        buttons.append([
            InlineKeyboardButton(
                text=f"#{task['id']} • {task['title']}",
                callback_data=f"toggle_task_select:{task['id']}"
            )
        ])

    markup = InlineKeyboardMarkup(buttons)
    await safe_reply(
        update,
        context,
        "✅ Выбери задачу, в которой хочешь изменить чеклист:",
        markup=markup
    )

async def toggle_checkitem_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, checklist_id: int):
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id if update.effective_user else None)

    def _get_item(task_service: TaskService):
        return task_service.task_repo.get_checklist_item(checklist_id)

    item_before = with_task_service(_get_item)
    if not item_before:
        await safe_reply(update, context, f"❌ Пункт чеклиста #{checklist_id} не найден.")
        return

    task_id = item_before.task_id
    old_value = "done" if item_before.is_done else "not_done"

    def _toggle(task_service: TaskService):
        return task_service.toggle_checklist_item(checklist_id)

    item = with_task_service(_toggle)
    if not item:
        await safe_reply(update, context, "❌ Не удалось изменить пункт чеклиста.")
        return

    new_value = "done" if item.is_done else "not_done"

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="toggle_checklist_item",
            entity_type="task",
            entity_id=task_id,
            payload={"item_id": item.id, "title": item.title, "is_done": item.is_done}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="checklist_toggled",
            old_value=old_value,
            new_value=new_value,
            note=f"Изменён пункт чеклиста: {item.title}"
        )

    with_log_service(_log)

    status_text = "✅ выполнен" if item.is_done else "⬜ снят"
    await safe_reply(update, context, f"{status_text}: [{item.id}] {item.title}")

async def toggle_checkitem_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("toggle_task_select:"):
        return

    task_id = int(data.split(":")[1])

    def _get(task_service: TaskService):
        task = task_service.get_task_by_id(task_id)
        items = task_service.list_checklists(task_id)
        return task, items

    task, items = with_task_service(_get)

    if not task:
        await query.edit_message_text(f"❌ Задача #{task_id} не найдена.")
        return

    if not items:
        await query.edit_message_text("📋 У этой задачи пока нет чеклиста.")
        return

    buttons = []
    for item in items:
        mark = "✅" if item.is_done else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {item.title}",
                callback_data=f"toggle_item_select:{item.id}"
            )
        ])

    await query.edit_message_text(
        f"📋 <b>{html.escape(task.title)}</b>\n\nВыбери пункт для переключения:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )

async def toggle_checkitem_item_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("toggle_item_select:"):
        return

    checklist_id = int(data.split(":")[1])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get_item(task_service: TaskService):
        return task_service.task_repo.get_checklist_item(checklist_id)

    item_before = with_task_service(_get_item)
    if not item_before:
        await query.edit_message_text(f"❌ Пункт чеклиста #{checklist_id} не найден.")
        return

    task_id = item_before.task_id
    old_value = "done" if item_before.is_done else "not_done"

    def _toggle(task_service: TaskService):
        return task_service.toggle_checklist_item(checklist_id)

    item = with_task_service(_toggle)
    if not item:
        await query.edit_message_text("❌ Не удалось изменить пункт чеклиста.")
        return

    new_value = "done" if item.is_done else "not_done"

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="toggle_checklist_item",
            entity_type="task",
            entity_id=task_id,
            payload={"item_id": item.id, "title": item.title, "is_done": item.is_done}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="checklist_toggled",
            old_value=old_value,
            new_value=new_value,
            note=f"Изменён пункт чеклиста: {item.title}"
        )

    with_log_service(_log)

    mark = "✅" if item.is_done else "⬜"
    await query.edit_message_text(
        f"{mark} Пункт обновлён:\n\n[{item.id}] {item.title}"
    )

# =========================
# leaderboard
# =========================

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    def _run(points_service: PointsService):
        return points_service.get_leaderboard()

    data = with_points_service(_run)

    if not data:
        await safe_reply(update, context, "📭 Нет данных для лидерборда.")
        return

    lines = ["🏆 <b>Лидерборд</b>", ""]

    medals = ["🥇", "🥈", "🥉"]

    for i, user in enumerate(data[:10], start=1):
        name = f"@{user['username']}" if user["username"] else user["full_name"]
        points = user["points"]

        if i <= 3:
            prefix = medals[i - 1]
            lines.append(f"{prefix} {name} — <b>{points}</b> баллов")
        else:
            lines.append(f"{i}️⃣ {name} — {points} баллов")

    for i, user in enumerate(data[:10], start=1):
        name = f"@{user['username']}" if user["username"] else user["full_name"]
        lines.append(f"{i}. {name} — {user['points']} баллов")

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def leaderboard_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    if not context.args:
        await safe_reply(update, context, "⚠️ Используй: /leaderboard_project <ID проекта>")
        return

    if not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ ID проекта должен быть числом.")
        return

    project_id = int(context.args[0])

    def _run(points_service: PointsService):
        return points_service.get_leaderboard(project_id=project_id)

    data = with_points_service(_run)

    if not data:
        await safe_reply(update, context, "📭 Нет данных по проекту.")
        return

    lines = [f"🏆 <b>Лидерборд проекта #{project_id}:</b>", ""]

    for i, user in enumerate(data[:10], start=1):
        name = f"@{user['username']}" if user["username"] else user["full_name"]
        lines.append(f"{i}. {name} — {user['points']} баллов")

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def leaderboard_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    def _run(task_service: TaskService):
        return task_service.list_projects()

    projects = with_task_service(_run)

    buttons = []
    for p in projects:
        buttons.append([
            InlineKeyboardButton(
                text=p.title,
                callback_data=f"lb_proj:{p.id}"
            )
        ])

    markup = InlineKeyboardMarkup(buttons)

    await safe_reply(
        update,
        context,
        "📊 <b>Выбери проект:</b>",
        markup=markup,
        parse_mode="HTML"
    )

async def leaderboard_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    project_id = int(data.split(":")[1])

    def _run(points_service: PointsService):
        return points_service.get_leaderboard(project_id=project_id)

    leaderboard = with_points_service(_run)

    if not leaderboard:
        await query.edit_message_text("📭 Нет данных по проекту.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Лидерборд проекта</b>", ""]

    for i, user in enumerate(leaderboard[:10], start=1):
        name = f"@{user['username']}" if user["username"] else user["full_name"]
        points = user["points"]

        if i <= 3:
            lines.append(f"{medals[i-1]} {name} — <b>{points}</b> баллов")
        else:
            lines.append(f"{i}️⃣ {name} — {points} баллов")

    await query.edit_message_text("\n".join(lines), parse_mode="HTML")

# =========================
# GET TASK FLOW
# =========================

async def get_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user:
        return ConversationHandler.END

    user_id = update.effective_user.id

    def _active(user_service: UserService):
        return user_service.get_is_active(user_id)

    is_active = with_user_service(_active)
    if not is_active:
        await safe_reply(
            update,
            context,
            "⚠️ Твой статус сейчас «неактивен». Чтобы брать задачи, "
            "переключи статус: /set_my_status active"
        )
        return ConversationHandler.END
    
    print("=== /get_task ===")
    print("user_id =", user_id)

    user = get_user_by_id(user_id)
    if not user:
        await safe_reply(update, context, "⚠️ Ты не найден в реестре.")
        return ConversationHandler.END

    def _run(task_service: TaskService):
        return task_service.count_user_active_tasks(user_id)

    active_tasks_count = with_task_service(_run)
    print("active_tasks_count =", active_tasks_count)
    if active_tasks_count >= MAX_ACTIVE_TASKS_PER_USER:
        await safe_reply(update, context, f"⚠️ Нельзя брать более {MAX_ACTIVE_TASKS_PER_USER} задач одновременно.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["user_id"] = user_id

    markup = ReplyKeyboardMarkup(
        [[p] for p in DEFAULT_PROJECTS],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await safe_reply(update, context, "🔧 Выбери проект:", markup)
    return SELECT_PROJECT

async def select_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    project = update.message.text.strip()
    context.user_data["project"] = project
    context.user_data["page"] = 1

    user_id = context.user_data.get("user_id")
    if not user_id:
        await safe_reply(update, context, "⚠️ Не удалось определить пользователя.")
        return ConversationHandler.END

    await safe_reply(update, context, "🔎 Подбираю задачи...", ReplyKeyboardRemove())
    await render_task_page(update, context, project=project, user_id=user_id, page=1)
    return SELECT_TASK


async def task_catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return SELECT_TASK

    await query.answer()

    data = query.data or ""
    user_id = context.user_data.get("user_id")
    project = context.user_data.get("project")

    if not user_id or not project:
        await query.edit_message_text("⚠️ Контекст выбора задачи потерян. Запусти /get_task заново.")
        return ConversationHandler.END

    if data == "noop":
        return SELECT_TASK

    if data.startswith("task_page:"):
        page = int(data.split(":")[1])
        context.user_data["page"] = page
        await render_task_page(query, context, project=project, user_id=user_id, page=page)
        return SELECT_TASK

    if data.startswith("take_task:"):
        task_id = int(data.split(":")[1])
        context.user_data["task_id"] = task_id

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_take:yes"),
                InlineKeyboardButton("❌ Отмена", callback_data="confirm_take:no"),
            ]
        ])
        await query.edit_message_text(
            f"Подтвердить взятие задачи #{task_id}?",
            reply_markup=markup
        )
        return CONFIRM

    return SELECT_TASK

async def confirm_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()
    data = query.data or ""

    if data == "confirm_take:no":
        await query.edit_message_text("❌ Выбор отменён.")
        return ConversationHandler.END

    if data != "confirm_take:yes":
        return CONFIRM

    task_id = context.user_data.get("task_id")
    user_id = context.user_data.get("user_id")
    if not task_id or not user_id:
        await query.edit_message_text("⚠️ Не удалось подтвердить выбор.")
        return ConversationHandler.END

    def _count(task_service: TaskService):
        return task_service.count_user_active_tasks(user_id)

    active_count = with_task_service(_count)
    if active_count >= MAX_ACTIVE_TASKS_PER_USER:
        await query.edit_message_text(f"⚠️ Нельзя иметь более {MAX_ACTIVE_TASKS_PER_USER} задач одновременно.")
        return ConversationHandler.END

    def _active(user_service: UserService):
        return user_service.get_is_active(user_id)

    is_active = with_user_service(_active)
    if not is_active:
        await query.edit_message_text("⚠️ Твой статус «неактивен». Сначала включи: /set_my_status active")
        return ConversationHandler.END
    
    def _assign(task_service: TaskService):
        return task_service.assign_task_with_auto_deadline(task_id, user_id, WORK_TZ)

    task = with_task_service(_assign)
    actor_db_id = get_internal_user_id_by_tg(user_id)

    if not task:
        await query.edit_message_text(f"⚠️ Задача #{task_id} не найдена или недоступна.")
        return ConversationHandler.END

    def _ensure_event(event_repo: EventRepository):
        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        tg_user_ids = []

        for link in active_links:
            user = getattr(link, "user", None)
            if user:
                tg_user_ids.append(user.telegram_user_id)

        if not tg_user_ids or not task.deadline_at:
            event_repo.remove_by_task_id(task.id)
            return None

        return event_repo.upsert_deadline_event(
            task_id=task.id,
            telegram_user_ids=tg_user_ids,
            title=f"Дедлайн по задаче #{task.id}",
            description="Пожалуйста, заверши работу в срок.",
            dt_value=task.deadline_at,
        )

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="take_task",
            entity_type="task",
            entity_id=task.id,
            payload={
                "title": task.title,
                "deadline": task.deadline_at.isoformat() if task.deadline_at else None
            }
        )

        log_service.log_task_history(
            task_id=task.id,
            actor_user_id=actor_db_id,
            action_type="assigned",
            old_value="available",
            new_value="in_progress",
            note="Пользователь взял задачу себе"
        )

    with_log_service(_log)
    with_event_repo(_ensure_event)

    await query.edit_message_text(f"✅ Задача #{task.id} назначена тебе.")
    return ConversationHandler.END


def get_task_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("get_task", get_task_start),
            MessageHandler(filters.Regex(r"^(?:🔧\s*Взять задачу|/get_task)$"), get_task_start),
        ],
        states={
            SELECT_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_project)],
            SELECT_TASK: [CallbackQueryHandler(task_catalog_callback)],
            CONFIRM: [CallbackQueryHandler(confirm_task_callback)],
        },
        fallbacks=[],
        allow_reentry=True,
    )

# =========================
# ADD TASK FLOW
# =========================

async def add_task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return ConversationHandler.END

    clear_add_task_data(context)

    def _run(task_service: TaskService):
        return task_service.list_projects_for_ui()

    projects = with_task_service(_run)
    if not projects:
        await safe_reply(update, context, "❌ В базе нет доступных проектов.")
        return ConversationHandler.END

    lines = ["📁 <b>Выбери проект для новой задачи:</b>", ""]
    for p in projects:
        lines.append(f"{p['id']} — {p['title']}")

    await safe_reply(
        update,
        context,
        "\n".join(lines) + "\n\nВведи ID проекта:",
        parse_mode="HTML",
    )
    return ADD_PROJECT

async def add_task_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await safe_reply(update, context, "⚠️ Введи числовой ID проекта.")
        return ADD_PROJECT

    project_id = int(text)

    def _run(task_service: TaskService):
        return task_service.get_project_by_id(project_id)

    project = with_task_service(_run)
    if not project:
        await safe_reply(update, context, f"❌ Проект #{project_id} не найден.")
        return ADD_PROJECT

    context.user_data["add_project_id"] = project.id
    context.user_data["add_project_title"] = project.title

    await safe_reply(update, context, "🧠 Введи название задачи:")
    return ADD_TITLE

async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    title = update.message.text.strip()
    if not title:
        await safe_reply(update, context, "⚠️ Название не может быть пустым.")
        return ADD_TITLE

    context.user_data["add_title"] = title
    await safe_reply(update, context, "📝 Введи описание задачи. Можно написать '-' если без описания:")
    return ADD_DESC

async def add_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    desc = update.message.text.strip()
    if desc == "-":
        desc = ""

    context.user_data["add_description"] = desc

    def _run(task_service: TaskService):
        return task_service.list_work_roles_for_ui()

    roles = with_task_service(_run)
    if not roles:
        await safe_reply(update, context, "❌ В базе нет ролей.")
        return ConversationHandler.END

    lines = ["👤 <b>Выбери роль:</b>", ""]
    for r in roles:
        emoji = f"{r['emoji']} " if r.get("emoji") else ""
        lines.append(f"{r['id']} — {emoji}{r['title']}")

    await safe_reply(
        update,
        context,
        "\n".join(lines) + "\n\nВведи ID роли:",
        parse_mode="HTML",
    )
    return ADD_ROLE

async def add_task_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await safe_reply(update, context, "⚠️ Введи числовой ID роли.")
        return ADD_ROLE

    role_id = int(text)

    def _run(task_service: TaskService):
        return task_service.get_work_role_by_id(role_id)

    role = with_task_service(_run)
    if not role:
        await safe_reply(update, context, f"❌ Роль #{role_id} не найдена.")
        return ADD_ROLE

    context.user_data["add_role_id"] = role.id
    context.user_data["add_role_title"] = role.title

    def _run_categories(task_service: TaskService):
        return task_service.list_task_categories_for_ui()

    categories = with_task_service(_run_categories)
    if not categories:
        await safe_reply(update, context, "❌ В базе нет категорий задач.")
        return ConversationHandler.END

    lines = ["📂 <b>Выбери категорию:</b>", ""]
    for c in categories:
        lines.append(f"{c['id']} — {c['title']}")

    await safe_reply(
        update,
        context,
        "\n".join(lines) + "\n\nВведи ID категории:",
        parse_mode="HTML",
    )
    return ADD_CATEGORY

async def add_task_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await safe_reply(update, context, "⚠️ Введи числовой ID категории.")
        return ADD_CATEGORY

    category_id = int(text)

    def _run(task_service: TaskService):
        return task_service.get_task_category_by_id(category_id)

    category = with_task_service(_run)
    if not category:
        await safe_reply(update, context, f"❌ Категория #{category_id} не найдена.")
        return ADD_CATEGORY

    context.user_data["add_category_id"] = category.id
    context.user_data["add_category_title"] = category.title

    await safe_reply(
        update,
        context,
        "⚡ Введи приоритет: low / medium / high / critical\n\nПо умолчанию можно написать: medium"
    )
    return ADD_PRIORITY

async def add_task_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    priority = update.message.text.strip().lower()
    allowed = {"low", "medium", "high", "critical"}

    if priority not in allowed:
        await safe_reply(update, context, "⚠️ Допустимые значения: low / medium / high / critical")
        return ADD_PRIORITY

    context.user_data["add_priority"] = priority

    await safe_reply(
        update,
        context,
        "📌 Введи статус: backlog / available\n\nРекомендую available, если задача должна сразу появиться участникам."
    )
    return ADD_STATUS

async def add_task_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    status = update.message.text.strip().lower()
    allowed = {"backlog", "available"}

    if status not in allowed:
        await safe_reply(update, context, "⚠️ Для создания доступны только backlog или available.")
        return ADD_STATUS

    context.user_data["add_status"] = status
    await safe_reply(update, context, "👥 Введи max_assignees (например 1):")
    return ADD_MAX_ASSIGNEES

async def add_task_max_assignees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await safe_reply(update, context, "⚠️ Введи целое число, например 1.")
        return ADD_MAX_ASSIGNEES

    value = int(text)
    if value < 1:
        await safe_reply(update, context, "⚠️ max_assignees должен быть не меньше 1.")
        return ADD_MAX_ASSIGNEES

    context.user_data["add_max_assignees"] = value
    await safe_reply(update, context, "⏳ Введи estimated_days (например 7):")
    return ADD_ESTIMATED_DAYS

async def add_task_estimated_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await safe_reply(update, context, "⚠️ Введи целое число дней, например 7.")
        return ADD_ESTIMATED_DAYS

    value = int(text)
    if value < 1:
        await safe_reply(update, context, "⚠️ estimated_days должен быть не меньше 1.")
        return ADD_ESTIMATED_DAYS

    context.user_data["add_estimated_days"] = value
    await safe_reply(update, context, "🔍 Требуется проверка после выполнения? yes / no")
    return ADD_REVIEW_REQUIRED

async def add_task_review_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip().lower()
    if text not in {"yes", "no", "y", "n"}:
        await safe_reply(update, context, "⚠️ Напиши yes или no.")
        return ADD_REVIEW_REQUIRED

    context.user_data["add_review_required"] = text in {"yes", "y"}
    await safe_reply(update, context, "🏆 Введи J value (например 5):")
    return ADD_J

async def add_task_j(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    value, error = parse_ranged_int(
        update.message.text.strip(),
        J_VALUE_MIN,
        J_VALUE_MAX,
        "J"
    )
    if error:
        await safe_reply(update, context, error)
        return ADD_J

    context.user_data["add_j"] = value
    await safe_reply(
        update,
        context,
        f"🏆 Введи C value ({C_VALUE_MIN}-{C_VALUE_MAX}):"
    )
    return ADD_C

async def add_task_c(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    value, error = parse_ranged_int(
        update.message.text.strip(),
        C_VALUE_MIN,
        C_VALUE_MAX,
        "C"
    )
    if error:
        await safe_reply(update, context, error)
        return ADD_C

    context.user_data["add_c"] = value
    await safe_reply(
        update,
        context,
        f"🏆 Введи T value ({T_VALUE_MIN}-{T_VALUE_MAX}):"
    )
    return ADD_T

async def add_task_t(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    value, error = parse_ranged_int(
        update.message.text.strip(),
        T_VALUE_MIN,
        T_VALUE_MAX,
        "T"
    )
    if error:
        await safe_reply(update, context, error)
        return ADD_T

    context.user_data["add_t"] = value

    text = (
        "📋 <b>Проверь новую задачу:</b>\n\n"
        f"📁 Проект: {context.user_data['add_project_title']}\n"
        f"🧩 Название: {html.escape(context.user_data['add_title'])}\n"
        f"📝 Описание: {html.escape(context.user_data['add_description'] or 'Без описания')}\n"
        f"👤 Роль: {context.user_data['add_role_title']}\n"
        f"📂 Категория: {context.user_data['add_category_title']}\n"
        f"⚡ Приоритет: {context.user_data['add_priority']}\n"
        f"📌 Статус: {format_task_status(context.user_data['add_status'])}\n"
        f"👥 max_assignees: {context.user_data['add_max_assignees']}\n"
        f"⏳ estimated_days: {context.user_data['add_estimated_days']}\n"
        f"🔍 review_required: {'yes' if context.user_data['add_review_required'] else 'no'}\n"
        f"🏆 J/C/T: {context.user_data['add_j']}/{context.user_data['add_c']}/{context.user_data['add_t']}\n\n"
        "Напиши yes для создания или no для отмены."
    )

    await safe_reply(update, context, text, parse_mode="HTML")
    return ADD_CONFIRM

async def add_task_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip().lower()
    if text not in {"yes", "no", "y", "n"}:
        await safe_reply(update, context, "⚠️ Напиши yes или no.")
        return ADD_CONFIRM

    if text in {"no", "n"}:
        clear_add_task_data(context)
        await safe_reply(update, context, "❌ Создание задачи отменено.")
        return ConversationHandler.END

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id if update.effective_user else None)

    def _create(task_service: TaskService):
        return task_service.create_task(
            project_id=context.user_data["add_project_id"],
            title=context.user_data["add_title"],
            description=context.user_data["add_description"],
            category_id=context.user_data["add_category_id"],
            required_work_role_id=context.user_data["add_role_id"],
            priority=context.user_data["add_priority"],
            status=context.user_data["add_status"],
            max_assignees=context.user_data["add_max_assignees"],
            estimated_days=context.user_data["add_estimated_days"],
            review_required=context.user_data["add_review_required"],
            j_value=context.user_data["add_j"],
            c_value=context.user_data["add_c"],
            t_value=context.user_data["add_t"],
            created_by_user_id=actor_db_id,
        )

    task = with_task_service(_create)
    if not task:
        await safe_reply(update, context, "❌ Не удалось создать задачу.")
        return ConversationHandler.END

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="create_task",
            entity_type="task",
            entity_id=task.id,
            payload={
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
            }
        )

        log_service.log_task_history(
            task_id=task.id,
            actor_user_id=actor_db_id,
            action_type="created",
            old_value=None,
            new_value=task.status,
            note="Админ создал задачу"
        )

    with_log_service(_log)

    clear_add_task_data(context)

    await safe_reply(
        update,
        context,
        f"✅ Задача создана: <b>{html.escape(task.title)}</b> (#{task.id})",
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def add_task_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_add_task_data(context)
    await safe_reply(update, context, "❌ Мастер создания задачи отменён.")
    return ConversationHandler.END

def get_add_task_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("add_task", add_task_entry)],
        states={
            ADD_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_project)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_desc)],
            ADD_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_role)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_category)],
            ADD_PRIORITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_priority)],
            ADD_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_status)],
            ADD_MAX_ASSIGNEES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_max_assignees)],
            ADD_ESTIMATED_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_estimated_days)],
            ADD_REVIEW_REQUIRED: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_review_required)],
            ADD_J: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_j)],
            ADD_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_c)],
            ADD_T: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_t)],
            ADD_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_confirm)],
        },
        fallbacks=[CommandHandler("cancel", add_task_cancel)],
        allow_reentry=True,
    )

# =========================
# ADMIN COMMANDS
# =========================

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    lines = ["🗝️ <b>Доступные админ-команды:</b>", ""]

    if has_permission(user.id, "manage_points"):
        lines.extend([
            "<b>Баллы:</b>",
            "/give_points — добавить баллы участнику",
            "/check_points — проверить баллы участника",
            "/points_history [username] — история начислений",
            "",
        ])

    if has_permission(user.id, "review_tasks"):
        lines.extend([
            "<b>Проверка задач:</b>",
            "/task_done — пометить задачу выполненной",
            "/return_task — вернуть задачу на доработку",
            "",
        ])

    if has_permission(user.id, "manage_tasks"):
        lines.extend([
            "<b>Управление задачами:</b>",
            "/unassign_task — снять участника с задачи",
            "/assign_task — назначить задачу участнику",
            "/add_task — создать новую задачу",
            "/block_task — заблокировать задачу",
            "/unblock_task — разблокировать задачу",
            "/set_deadline — изменить дедлайн задачи",
            "/run_overdue — проверить просрочки",
            "/overdue_tasks — показать просроченные задачи",
            "/add_checkitem — добавить пункт чеклиста",
            "/delete_checkitem — удалить пункт чеклиста",
            "",
        ])

    if has_permission(user.id, "manage_events"):
        lines.extend([
            "<b>События:</b>",
            "/set_next_meeting — изменить дату ближайшего собрания",
            "/finish_meeting — завершить собрание и внести attendance",
            "",
        ])

    if has_permission(user.id, "view_admin_reports"):
        lines.extend([
            "<b>Логи и отчёты:</b>",
            "/logs [audit|errors|tasks|points] — посмотреть логи",
            "",
        ])

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def check_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.message or not update.effective_user:
        return
    if not has_permission(update.effective_user.id, "manage_points"):
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Формат: /check_points <Полное имя>")
        return

    full_name = " ".join(context.args).strip()

    def _run(points_service: PointsService):
        return points_service.get_user_points_summary_by_full_name(full_name)

    summary = with_points_service(_run)
    if not summary:
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    projects = summary.get("projects", {})
    if not projects:
        await update.message.reply_text(f"📊 У {html.escape(full_name)} пока нет баллов.")
        return

    lines = [f"📊 <b>Баллы {html.escape(full_name)}:</b>", ""]
    for project_name in sorted(projects.keys()):
        item = projects[project_name]
        points = item.get("points", 0)
        percent = float(item.get("percent_rate", 0.0)) * 100
        lines.append(f"🔹 <b>{project_name}</b>: {points} баллов ({round(percent)}%)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user:
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "review_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return ConversationHandler.END

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Укажи ID задачи: /task_done <ID>")
        return ConversationHandler.END

    task_id = int(context.args[0])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"⚠️ Задача #{task_id} не найдена.")
        return ConversationHandler.END

    if task.status != "review":
        await safe_reply(
            update,
            context,
            f"⚠️ Подтвердить можно только задачу в статусе 'На проверке'. Сейчас: {format_task_status(task.status)}"
        )
        return ConversationHandler.END

    task_title = task.title

    def _approve(task_service: TaskService):
        return task_service.approve_task(task_id)


    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    active_users = []

    for link in active_links:
        user = getattr(link, "user", None)
        if user:
            active_users.append(user)

    
    base_points = calculate_task_points(task)

    project_id = getattr(task, "project_id", None)
    project_title = getattr(getattr(task, "project", None), "title", None) or "Без проекта"


    clear_task_done_data(context)
    context.user_data["task_done_task_id"] = task_id
    context.user_data["task_done_task_title"] = task.title
    context.user_data["task_done_actor_db_id"] = actor_db_id
    context.user_data["task_done_active_users"] = [
        {
            "telegram_user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": getattr(user, "full_name", None),
        }
        for user in active_users
    ]
    context.user_data["task_done_base_points"] = base_points
    context.user_data["task_done_project_id"] = project_id
    context.user_data["task_done_project_title"] = project_title
    context.user_data["task_done_j_value"] = getattr(task, "j_value", None)
    context.user_data["task_done_c_value"] = getattr(task, "c_value", None)
    context.user_data["task_done_t_value"] = getattr(task, "t_value", None)

    priority_str = format_task_priority(getattr(task, "priority", None))
    
    await safe_reply(
        update,
        context,
        f"✅ Подтверждение задачи #{task_id}\n\n"
        f"🧩 {task.title}\n"
        f"⚡ Приоритет: {priority_str}\n"
        f"🏆 Базовые баллы: {base_points}\n\n"
        f"Введи коэффициент K от {K_BONUS_MIN} до {K_BONUS_MAX}.\n"
        f"Пример: -2, 0, 1, 3"
    )
    return TASK_DONE_K

async def task_done_apply_k(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        clear_task_done_data(context)
        return ConversationHandler.END

    k_bonus, error = parse_k_bonus(update.message.text)
    if error:
        await safe_reply(update, context, error)
        return TASK_DONE_K

    task_id = context.user_data.get("task_done_task_id")
    task_title = context.user_data.get("task_done_task_title")
    actor_db_id = context.user_data.get("task_done_actor_db_id")
    active_users_data = context.user_data.get("task_done_active_users", [])
    base_points = int(context.user_data.get("task_done_base_points", 0))
    project_id = context.user_data.get("task_done_project_id")
    project_title = context.user_data.get("task_done_project_title")
    j_value = context.user_data.get("task_done_j_value")
    c_value = context.user_data.get("task_done_c_value")
    t_value = context.user_data.get("task_done_t_value")

    if not task_id or task_title is None or actor_db_id is None:
        clear_task_done_data(context)
        await safe_reply(update, context, "⚠️ Контекст подтверждения задачи потерян. Запусти /task_done заново.")
        return ConversationHandler.END
    
    if not project_id:
        clear_task_done_data(context)
        await safe_reply(update, context, f"⚠️ У задачи #{task_id} не найден project_id. Проверь привязку задачи к проекту.")
        return ConversationHandler.END

    final_points = apply_k_bonus(base_points, k_bonus)
    split_points = split_points_among_assignees(final_points, len(active_users_data))

    def _approve(task_service: TaskService):
        return task_service.approve_task(task_id)

    updated = with_task_service(_approve)
    if not updated:
        clear_task_done_data(context)
        await safe_reply(update, context, f"⚠️ Не удалось подтвердить задачу #{task_id}.")
        return ConversationHandler.END

    def _award_points(points_service: PointsService):
        awarded = []

        for user_data, amount in zip(active_users_data, split_points):
            if amount <= 0:
                continue

            ok = points_service.add_points(
                telegram_user_id=user_data["telegram_user_id"],
                points_to_add=amount,
                project_id=project_id,
                project_name=project_title,
                reason=f"Подтверждена задача #{task_id}: {task_title}",
                task_id=task_id,
                source_type="task_done",
                created_by_user_id=actor_db_id,
                j_value=j_value,
                c_value=c_value,
                t_value=t_value,
                k_value=k_bonus,
            )

            if ok:
                awarded.append({
                    "telegram_user_id": user_data["telegram_user_id"],
                    "username": user_data.get("username"),
                    "full_name": user_data.get("full_name"),
                    "amount": amount,
                })

        return awarded

    awarded_points = with_points_service(_award_points) or []

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="approve_task",
            entity_type="task",
            entity_id=task_id,
            payload={
                "title": task_title,
                "base_points": base_points,
                "k_bonus": k_bonus,
                "final_points": final_points,
                "assignees_count": len(active_users_data),
                "awarded": [
                    {
                        "telegram_user_id": item["telegram_user_id"],
                        "amount": item["amount"],
                    }
                    for item in awarded_points
                ]
            }
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="review",
            new_value="done",
            note=f"Админ подтвердил задачу. K={k_bonus}, база={base_points}, итог={final_points}"
        )

        for item in awarded_points:
            log_service.log_task_history(
                task_id=task_id,
                actor_user_id=actor_db_id,
                action_type="points_awarded",
                old_value=None,
                new_value=str(item["amount"]),
                note=f"Автоначислены баллы пользователю tg={item['telegram_user_id']} с K={k_bonus}"
            )

    with_log_service(_log)

    def _remove_events(event_repo: EventRepository):
        return event_repo.remove_by_task_id(task_id)

    with_event_repo(_remove_events)

    if awarded_points:
        lines = []
        for item in awarded_points:
            label = (
                f"@{item['username']}" if item.get("username")
                else f"tg:{item['telegram_user_id']}"
            )
            lines.append(f"• {label} — {item['amount']} баллов")

        await safe_reply(
            update,
            context,
            f"✅ Задача #{task_id} подтверждена и завершена.\n\n"
            f"🏆 Баллы с учётом приоритета: {base_points}\n"
            f"⚖️ K: {k_bonus}\n"
            f"🎯 Итог: {final_points}\n\n"
            f"Начислено:\n" + "\n".join(lines)
        )
    else:
        await safe_reply(
            update,
            context,
            f"✅ Задача #{task_id} подтверждена и завершена.\n\n"
            f"🏆 Баллы с учётом приоритета: {base_points}\n"
            f"⚖️ K: {k_bonus}\n"
            f"🎯 Итог: {final_points}\n\n"
            f"⚠️ Но активных исполнителей для начисления баллов не найдено."
        )

    for item in awarded_points:
        try:
            await context.bot.send_message(
                chat_id=item["telegram_user_id"],
                text=(
                    f"🎉 Задача <b>{task_title}</b> (#{task_id}) подтверждена.\n"
                    f"🏆 База: <b>{base_points}</b>\n"
                    f"⚖️ K: <b>{k_bonus}</b>\n"
                    f"🎯 Тебе начислено: <b>{item['amount']}</b> баллов."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    clear_task_done_data(context)
    return ConversationHandler.END

async def task_done_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_task_done_data(context)
    await safe_reply(update, context, "❌ Подтверждение задачи отменено.")
    return ConversationHandler.END

def get_task_done_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("task_done", task_done)],
        states={
            TASK_DONE_K: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_done_apply_k)],
        },
        fallbacks=[CommandHandler("cancel", task_done_cancel)],
        allow_reentry=True,
    )

async def set_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    user = update.effective_user
    if not user or not (is_super_admin(user.id) or has_permission(user.id, "manage_users")):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if len(context.args) < 2 or context.args[-1].lower() not in {"active", "inactive"}:
        await safe_reply(
            update,
            context,
            "⚠️ Используй: /set_user_status <Полное имя> <active|inactive>\n\n"
            "Пример:\n/set_user_status Станислав Палий inactive"
        )
        return

    new_status = context.args[-1].lower() == "active"
    full_name = " ".join(context.args[:-1]).strip()

    def _target(user_service: UserService):
        return user_service.user_repo.get_by_full_name(full_name)

    target_user = with_user_service(_target)
    if not target_user:
        await safe_reply(update, context, f"❌ Пользователь «{html.escape(full_name)}» не найден.")
        return

    tg_id = target_user.telegram_user_id

    def _set(user_service: UserService):
        return user_service.set_active_status(tg_id, new_status)

    ok = with_user_service(_set)
    if not ok:
        await safe_reply(update, context, "❌ Не удалось изменить статус.")
        return

    actor_db_id = get_internal_user_id_by_tg(user.id)
    target_db_id = get_internal_user_id_by_tg(tg_id)

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="set_user_status",
            entity_type="user",
            entity_id=target_db_id,
            payload={"target_full_name": full_name, "is_active": new_status},
        )

    with_log_service(_log)

    status_text = "🟢 активен" if new_status else "⚪ неактивен"
    await safe_reply(
        update,
        context,
        f"✅ Статус «{html.escape(full_name)}» обновлён: {status_text}."
    )

    try:
        await context.bot.send_message(
            chat_id=tg_id,
            text=(
                f"ℹ️ Твой статус изменён администратором: <b>{status_text}</b>.\n"
                + ("" if new_status else
                   "В статусе «неактивен» тебе не приходят уведомления о собраниях, "
                   "и ты не можешь брать новые задачи.")
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

async def return_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "review_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Используй: /return_task <ID задачи>")
        return

    task_id = int(context.args[0])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    if task.status != "review":
        await safe_reply(
            update,
            context,
            f"⚠️ Вернуть можно только задачу в статусе 'На проверке'. Сейчас: {format_task_status(task.status)}"
        )
        return

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    reserved_users = []

    for link in active_links:
        user = getattr(link, "user", None)
        if user:
            reserved_users.append(user.telegram_user_id)

    task_title = task.title

    def _return(task_service: TaskService):
        return task_service.return_from_review(task_id)

    updated = with_task_service(_return)
    if not updated:
        await safe_reply(update, context, f"❌ Не удалось вернуть задачу #{task_id} в работу.")
        return

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="return_task_from_review",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task_title}
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="review",
            new_value="in_progress",
            note="Админ вернул задачу на доработку"
        )

    with_log_service(_log)

    await safe_reply(update, context, f"🛠 Задача #{task_id} возвращена в работу.")

    for tg_id in reserved_users:
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=f"⚠️ Задача <b>{task_title}</b> (#{task_id}) возвращена на доработку.",
                parse_mode="HTML",
            )
        except Exception:
            pass

async def _do_block_task(task_id: int, actor_db_id: int, reason: str | None):
    """Shared block logic. Returns (task, error_str)."""
    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        return None, f"❌ Задача #{task_id} не найдена."

    blockable = {"available", "in_progress", "review", "overdue"}
    if task.status not in blockable:
        return None, f"⚠️ Нельзя заблокировать задачу из статуса {format_task_status(task.status)}."

    old_status = task.status
    task_title = task.title

    def _block(task_service: TaskService):
        return task_service.block_task(task_id)

    updated = with_task_service(_block)
    if not updated:
        return None, f"❌ Не удалось заблокировать задачу #{task_id}."

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="block_task",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task_title, "reason": reason}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value=old_status,
            new_value="blocked",
            note=reason or "Админ заблокировал задачу"
        )

    with_log_service(_log)
    return updated, None


async def block_task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return ConversationHandler.END

    def _tasks(task_service: TaskService):
        return task_service.list_all_non_done_tasks()

    tasks = with_task_service(_tasks)
    blockable = {"available", "in_progress", "review", "overdue"}
    tasks = [t for t in tasks if t.status in blockable]

    if not tasks:
        await safe_reply(update, context, "😔 Нет задач, которые можно заблокировать.")
        return ConversationHandler.END

    buttons = []
    for t in tasks[:40]:
        project = getattr(getattr(t, "project", None), "title", "—")
        emoji = TASK_STATUS_LABELS.get(t.status, ("⚪", ""))[0]
        buttons.append([InlineKeyboardButton(
            text=f"#{t.id} {emoji}[{project}] {t.title[:35]}",
            callback_data=f"bt_task:{t.id}"
        )])

    await safe_reply(update, context, "📋 Выбери задачу для блокировки:", markup=InlineKeyboardMarkup(buttons))
    return BLOCK_SELECT_TASK


async def block_task_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("bt_task:"):
        return BLOCK_SELECT_TASK

    task_id = int(data.split(":")[1])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    task, error = await _do_block_task(task_id, actor_db_id, reason=None)
    if error:
        await query.edit_message_text(error)
        return ConversationHandler.END

    await query.edit_message_text(f"⛔ Задача #{task_id} заблокирована.")

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    for link in active_links:
        user = getattr(link, "user", None)
        if not user:
            continue
        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"⛔ <b>Задача заблокирована</b>\n\n"
                    f"🆔 #{task_id}\n"
                    f"🧩 <b>{task.title}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return ConversationHandler.END


def get_block_task_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("block_task", block_task_entry)],
        states={
            BLOCK_SELECT_TASK: [CallbackQueryHandler(block_task_select_callback, pattern="^bt_task:")],
        },
        fallbacks=[],
        allow_reentry=True,
    )

async def unblock_task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return ConversationHandler.END

    def _tasks(task_service: TaskService):
        return task_service.task_repo.list_tasks_by_status(["blocked"])

    tasks = with_task_service(_tasks)
    if not tasks:
        await safe_reply(update, context, "😔 Нет заблокированных задач.")
        return ConversationHandler.END

    buttons = []
    for t in tasks[:40]:
        project = getattr(getattr(t, "project", None), "title", "—")
        buttons.append([InlineKeyboardButton(
            text=f"#{t.id} ⛔[{project}] {t.title[:35]}",
            callback_data=f"ubt_task:{t.id}"
        )])

    await safe_reply(update, context, "📋 Выбери задачу для разблокировки:", markup=InlineKeyboardMarkup(buttons))
    return UNBLOCK_SELECT_TASK


async def unblock_task_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("ubt_task:"):
        return UNBLOCK_SELECT_TASK

    task_id = int(data.split(":")[1])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await query.edit_message_text(f"❌ Задача #{task_id} не найдена.")
        return ConversationHandler.END

    if task.status != "blocked":
        await query.edit_message_text(f"⚠️ Задача #{task_id} уже не заблокирована.")
        return ConversationHandler.END

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    target_status = "in_progress" if active_links else "available"
    task_title = task.title

    def _unblock(task_service: TaskService):
        return task_service.unblock_task(task_id, target_status=target_status)

    updated = with_task_service(_unblock)
    if not updated:
        await query.edit_message_text(f"❌ Не удалось разблокировать задачу #{task_id}.")
        return ConversationHandler.END

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="unblock_task",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task_title, "target_status": target_status}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="blocked",
            new_value=target_status,
            note="Админ разблокировал задачу"
        )

    with_log_service(_log)

    await query.edit_message_text(
        f"🟢 Задача #{task_id} разблокирована. Новый статус: {format_task_status(target_status)}"
    )

    for link in active_links:
        user = getattr(link, "user", None)
        if not user:
            continue
        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"🟢 <b>Задача разблокирована</b>\n\n"
                    f"🆔 #{task_id}\n"
                    f"🧩 <b>{task_title}</b>\n"
                    f"📌 Новый статус: {format_task_status(target_status)}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return ConversationHandler.END


def get_unblock_task_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("unblock_task", unblock_task_entry)],
        states={
            UNBLOCK_SELECT_TASK: [CallbackQueryHandler(unblock_task_select_callback, pattern="^ubt_task:")],
        },
        fallbacks=[],
        allow_reentry=True,
    )

async def _do_unassign_task_from_user(task_id: int, target_user, actor_db_id: int):
    """Shared unassign logic, returns (updated_task, error_str)."""
    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        return None, f"❌ Задача #{task_id} не найдена."

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    active_tg_ids = [getattr(a.user, "telegram_user_id", None) for a in active_links if getattr(a, "user", None)]

    if target_user.telegram_user_id not in active_tg_ids:
        full_name = target_user.full_name or str(target_user.telegram_user_id)
        return None, f"⚠️ Пользователь «{html.escape(full_name)}» не является активным исполнителем задачи #{task_id}."

    task_title = task.title

    def _unassign(task_service: TaskService):
        return task_service.unassign_task_from_user(task_id, target_user.telegram_user_id)

    updated_task = with_task_service(_unassign)
    if not updated_task:
        full_name = target_user.full_name or str(target_user.telegram_user_id)
        return None, f"❌ Не удалось снять пользователя «{html.escape(full_name)}» с задачи #{task_id}."

    removed_user_db_id = get_internal_user_id_by_tg(target_user.telegram_user_id)
    full_name = target_user.full_name or str(target_user.telegram_user_id)

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="unassign_task",
            entity_type="task",
            entity_id=task_id,
            payload={
                "title": task_title,
                "removed_from_user_id": removed_user_db_id,
                "removed_from_telegram_user_id": target_user.telegram_user_id,
                "removed_from_full_name": target_user.full_name,
            }
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="unassigned",
            old_value=str(removed_user_db_id) if removed_user_db_id is not None else None,
            new_value=None,
            note=f"Админ снял пользователя «{full_name}» с задачи"
        )

    with_log_service(_log)

    remaining_active = [a for a in getattr(updated_task, "assignees", []) if getattr(a, "is_active", False)]
    if not remaining_active:
        def _remove_events(event_repo: EventRepository):
            return event_repo.remove_by_task_id(task_id)
        with_event_repo(_remove_events)

    return updated_task, None


async def unassign_task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return ConversationHandler.END

    def _tasks(task_service: TaskService):
        return task_service.list_assigned_tasks()

    tasks = with_task_service(_tasks)
    if not tasks:
        await safe_reply(update, context, "😔 Нет задач с активными исполнителями.")
        return ConversationHandler.END

    buttons = []
    for t in tasks[:40]:
        project = getattr(getattr(t, "project", None), "title", "—")
        active_count = sum(1 for a in getattr(t, "assignees", []) if getattr(a, "is_active", False))
        buttons.append([InlineKeyboardButton(
            text=f"#{t.id} [{project}] {t.title[:35]} ({active_count}👤)",
            callback_data=f"uat_task:{t.id}"
        )])

    await safe_reply(update, context, "📋 Выбери задачу, с которой нужно снять исполнителя:", markup=InlineKeyboardMarkup(buttons))
    return UNASSIGN_SELECT_TASK


async def unassign_task_select_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return UNASSIGN_SELECT_TASK
    await query.answer()

    data = query.data or ""
    if not data.startswith("uat_task:"):
        return UNASSIGN_SELECT_TASK

    task_id = int(data.split(":")[1])

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await query.edit_message_text(f"❌ Задача #{task_id} не найдена.")
        return ConversationHandler.END

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    if not active_links:
        await query.edit_message_text(f"⚠️ У задачи #{task_id} нет активных исполнителей.")
        return ConversationHandler.END

    context.user_data["uat_task_id"] = task_id

    # If only one assignee — skip user selection, unassign immediately
    if len(active_links) == 1:
        user = getattr(active_links[0], "user", None)
        if not user:
            await query.edit_message_text("❌ Не удалось определить исполнителя.")
            return ConversationHandler.END

        context.user_data["uat_task_id"] = task_id
        actor_db_id = get_internal_user_id_by_tg(query.from_user.id if query.from_user else None)
        updated_task, error = await _do_unassign_task_from_user(task_id, user, actor_db_id)

        if error:
            await query.edit_message_text(error)
            return ConversationHandler.END

        full_name = user.full_name or f"@{user.username}"
        remaining = [a for a in getattr(updated_task, "assignees", []) if getattr(a, "is_active", False)]
        if remaining:
            msg = f"✅ Пользователь «{html.escape(full_name)}» снят с задачи #{task_id}. У задачи всё ещё есть активные исполнители."
        else:
            msg = f"✅ Пользователь «{html.escape(full_name)}» снят с задачи #{task_id}. Задача снова свободна."
        await query.edit_message_text(msg)

        try:
            await query._bot.send_message(
                chat_id=user.telegram_user_id,
                text=f"⚠️ Задача <b>{task.title}</b> (#{task_id}) была снята с тебя администратором.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return ConversationHandler.END

    # Multiple assignees — let admin pick which one
    buttons = []
    for link in active_links:
        user = getattr(link, "user", None)
        if not user:
            continue
        label = user.full_name or f"@{user.username}" or str(user.telegram_user_id)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"uat_user:{user.telegram_user_id}")])

    await query.edit_message_text(
        f"👤 Выбери исполнителя для снятия с задачи #{task_id} «{html.escape(task.title[:50])}»:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return UNASSIGN_SELECT_USER


async def unassign_task_select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("uat_user:"):
        return UNASSIGN_SELECT_USER

    tg_user_id = int(data.split(":")[1])
    task_id = context.user_data.get("uat_task_id")
    if not task_id:
        await query.edit_message_text("⚠️ Контекст потерян. Запусти /unassign_task заново.")
        return ConversationHandler.END

    def _target(user_service: UserService):
        return user_service.get_user_by_telegram_id(tg_user_id)

    target_user = with_user_service(_target)
    if not target_user:
        await query.edit_message_text("❌ Пользователь не найден.")
        return ConversationHandler.END

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)
    updated_task, error = await _do_unassign_task_from_user(task_id, target_user, actor_db_id)

    if error:
        await query.edit_message_text(error)
        return ConversationHandler.END

    full_name = target_user.full_name or f"@{target_user.username}"
    remaining = [a for a in getattr(updated_task, "assignees", []) if getattr(a, "is_active", False)]
    if remaining:
        msg = f"✅ Пользователь «{html.escape(full_name)}» снят с задачи #{task_id}. У задачи всё ещё есть активные исполнители."
    else:
        msg = f"✅ Пользователь «{html.escape(full_name)}» снят с задачи #{task_id}. Задача снова свободна."
    await query.edit_message_text(msg)

    try:
        await context.bot.send_message(
            chat_id=target_user.telegram_user_id,
            text=f"⚠️ Задача <b>{updated_task.title}</b> (#{task_id}) была снята с тебя администратором.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    context.user_data.pop("uat_task_id", None)
    return ConversationHandler.END


def get_unassign_task_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("unassign_task", unassign_task_entry)],
        states={
            UNASSIGN_SELECT_TASK: [CallbackQueryHandler(unassign_task_select_task_callback, pattern="^uat_task:")],
            UNASSIGN_SELECT_USER: [CallbackQueryHandler(unassign_task_select_user_callback, pattern="^uat_user:")],
        },
        fallbacks=[],
        allow_reentry=True,
    )

async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if len(context.args) < 3:
        await safe_reply(
            update,
            context,
            "⚠️ Используй: /set_deadline <ID задачи> <YYYY-MM-DD> <HH:MM>\n"
            "Пример: /set_deadline 5 2026-04-10 12:30"
        )
        return

    if not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ ID задачи должен быть числом.")
        return

    task_id = int(context.args[0])
    raw_dt = f"{context.args[1]} {context.args[2]}"

    try:
        naive_dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M")
        deadline_at = naive_dt.replace(tzinfo=WORK_TZ)
    except ValueError:
        await safe_reply(
            update,
            context,
            "⚠️ Неверный формат даты.\nИспользуй: YYYY-MM-DD HH:MM\nПример: 2026-04-10 12:30"
        )
        return

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    old_deadline = task.deadline_at.isoformat() if task.deadline_at else None
    task_title = task.title

    def _set(task_service: TaskService):
        return task_service.set_deadline(task_id, deadline_at)

    updated = with_task_service(_set)
    if not updated:
        await safe_reply(update, context, f"❌ Не удалось изменить дедлайн задачи #{task_id}.")
        return

    def _sync_event(event_repo: EventRepository):
        active_links = [a for a in getattr(updated, "assignees", []) if getattr(a, "is_active", False)]
        tg_user_ids = []

        for link in active_links:
            user = getattr(link, "user", None)
            if user:
                tg_user_ids.append(user.telegram_user_id)

        if not tg_user_ids:
            event_repo.remove_by_task_id(task_id)
            return None

        return event_repo.upsert_deadline_event(
            task_id=task_id,
            telegram_user_ids=tg_user_ids,
            title=f"Дедлайн по задаче #{task_id}",
            description="Администратор обновил дедлайн задачи.",
            dt_value=deadline_at,
        )

    synced_event = with_event_repo(_sync_event)

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="set_deadline",
            entity_type="task",
            entity_id=task_id,
            payload={
                "title": task_title,
                "old_deadline": old_deadline,
                "new_deadline": deadline_at.isoformat(),
            }
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="deadline_changed",
            old_value=old_deadline,
            new_value=deadline_at.isoformat(),
            note="Админ изменил дедлайн"
        )

    with_log_service(_log)

    await safe_reply(
        update,
        context,
        f"⏰ Дедлайн задачи #{task_id} изменён на {format_datetime_rus(deadline_at)}."
    )

    active_links = [a for a in getattr(updated, "assignees", []) if getattr(a, "is_active", False)]
    for link in active_links:
        user = getattr(link, "user", None)
        if not user:
            continue

        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"⏰ <b>Дедлайн обновлён</b>\n\n"
                    f"🆔 #{task_id}\n"
                    f"🧩 <b>{task_title}</b>\n"
                    f"📅 Новый дедлайн: {format_datetime_rus(deadline_at)}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

async def run_overdue_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    now = datetime.now(WORK_TZ)

    def _run(task_service: TaskService):
        return task_service.mark_overdue_tasks(now)

    updated_tasks = with_task_service(_run)

    if not updated_tasks:
        await safe_reply(update, context, "✅ Просроченных задач не найдено.")
        return

    lines = [f"🔥 Обновлено просроченных задач: {len(updated_tasks)}", ""]
    for task in updated_tasks:
        lines.append(f"• #{task.id} — {task.title}")

    await safe_reply(update, context, "\n".join(lines))

async def show_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    def _run(task_service: TaskService):
        return task_service.get_all_overdue_tasks()

    tasks = with_task_service(_run)

    if not tasks:
        await safe_reply(update, context, "✅ Нет просроченных задач.")
        return

    lines = ["🔥 <b>Просроченные задачи:</b>", ""]

    for task in tasks:
        dt = format_datetime_rus(task.deadline_at) if task.deadline_at else "—"

        lines.append(
            f"• #{task.id} — {task.title}\n"
            f"⏰ Дедлайн: {dt}"
        )
        lines.append("")

    await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")

async def _do_assign_task(context, task_id: int, target_user, actor_db_id: int):
    """Shared logic: assign a task and fire the side-effects."""
    def _get_task(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task_before = with_task_service(_get_task)
    if not task_before:
        return None, f"❌ Задача #{task_id} не найдена."

    active_links = [a for a in getattr(task_before, "assignees", []) if getattr(a, "is_active", False)]
    if len(active_links) >= (task_before.max_assignees or 1):
        return None, f"⚠️ Задача #{task_id} уже заполнена по исполнителям."

    if not getattr(target_user, "is_active", True):
        full_name = target_user.full_name or f"@{target_user.username}" or str(target_user.telegram_user_id)
        return None, (
            f"⚠️ Пользователь «{html.escape(full_name)}» сейчас неактивен. "
            f"Сначала переведи его в активный статус: /set_user_status {full_name} active"
        )

    def _assign(task_service: TaskService):
        return task_service.assign_task_to_user(task_id, target_user.telegram_user_id, WORK_TZ)

    task = with_task_service(_assign)
    if not task:
        return None, f"❌ Не удалось назначить задачу #{task_id}."

    def _ensure_event(event_repo: EventRepository):
        links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        tg_ids = [getattr(a.user, "telegram_user_id", None) for a in links if getattr(a, "user", None)]
        tg_ids = [i for i in tg_ids if i]
        if not tg_ids or not task.deadline_at:
            event_repo.remove_by_task_id(task.id)
            return None
        return event_repo.upsert_deadline_event(
            task_id=task.id,
            telegram_user_ids=tg_ids,
            title=f"Дедлайн по задаче #{task.id}",
            description="Пожалуйста, заверши работу в срок.",
            dt_value=task.deadline_at,
        )

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="assign_task",
            entity_type="task",
            entity_id=task_id,
            payload={
                "assigned_to_user_id": target_user.id,
                "assigned_to_telegram_user_id": target_user.telegram_user_id,
                "task_title": task.title,
            }
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="assigned_by_admin",
            old_value=None,
            new_value=str(target_user.id),
            note="Админ назначил задачу пользователю"
        )

    with_log_service(_log)
    with_event_repo(_ensure_event)
    return task, None

async def run_idle_check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    now = datetime.now(WORK_TZ)

    def _idle(user_service: UserService):
        return user_service.get_idle_users(now, IDLE_REMINDER_DAYS)

    idle_users = with_user_service(_idle)
    if not idle_users:
        await safe_reply(update, context, "✅ Нет участников, которым нужно напоминание о задачах.")
        return

    notified = []
    skipped = []

    for user in idle_users:
        tg_id = user.telegram_user_id
        user_record = get_user_by_id(tg_id)
        if not user_record:
            skipped.append(user.full_name or str(tg_id))
            continue

        def _suggest(task_service: TaskService):
            return task_service.get_suggested_tasks_for_user(user_record, limit=3)

        suggested = with_task_service(_suggest)

        if suggested:
            def _cards(task_service: TaskService):
                return [task_service.format_task_card(t) for t in suggested]

            cards = with_task_service(_cards)

            text = (
                "👋 Привет! Ты давно не брал задачи.\n"
                "Вот несколько подходящих под твою роль вариантов:\n\n"
                + "\n\n".join(cards)
                + "\n\nИспользуй /get_task, чтобы взять задачу."
            )

            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    parse_mode="HTML",
                )
                notified.append(user.full_name or str(tg_id))
            except Exception:
                skipped.append(user.full_name or str(tg_id))
        else:
            skipped.append(user.full_name or str(tg_id))

        def _mark(user_service: UserService):
            return user_service.update_last_idle_reminder(tg_id, now)

        with_user_service(_mark)

    lines = [f"📨 Проверка простоя завершена.", ""]
    lines.append(f"✅ Уведомлено: {len(notified)}")
    if notified:
        lines.append(", ".join(notified))
    lines.append("")
    lines.append(f"⏭ Пропущено (нет задач/ошибка): {len(skipped)}")
    if skipped:
        lines.append(", ".join(skipped))

    await safe_reply(update, context, "\n".join(lines))

async def run_idle_check(context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[list[str], list[str]]:
    now = datetime.now(WORK_TZ)

    def _idle(user_service: UserService):
        return user_service.get_idle_users(now, IDLE_REMINDER_DAYS)

    idle_users = with_user_service(_idle)
    notified, skipped = [], []

    for user in idle_users:
        tg_id = user.telegram_user_id
        user_record = get_user_by_id(tg_id)
        label = user.full_name or str(tg_id)

        if not user_record:
            skipped.append(label)
            continue

        def _suggest(task_service: TaskService):
            return task_service.get_suggested_tasks_for_user(user_record, limit=3)

        suggested = with_task_service(_suggest)

        if suggested and context:
            def _cards(task_service: TaskService):
                return [task_service.format_task_card(t) for t in suggested]

            cards = with_task_service(_cards)
            text = (
                "👋 Привет! Ты давно не брал задачи.\n"
                "Вот несколько подходящих под твою роль вариантов:\n\n"
                + "\n\n".join(cards)
                + "\n\nИспользуй /get_task, чтобы взять задачу."
            )
            try:
                await context.bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
                notified.append(label)
            except Exception:
                skipped.append(label)
        else:
            skipped.append(label)

        def _mark(user_service: UserService):
            return user_service.update_last_idle_reminder(tg_id, now)

        with_user_service(_mark)

    return notified, skipped

async def idle_task_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await run_idle_check(context)

async def run_idle_check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    notified, skipped = await run_idle_check(context)

    if not notified and not skipped:
        await safe_reply(update, context, "✅ Нет участников, которым нужно напоминание о задачах.")
        return

    lines = ["📨 Проверка простоя завершена.", ""]
    lines.append(f"✅ Уведомлено: {len(notified)}")
    if notified:
        lines.append(", ".join(notified))
    lines.append("")
    lines.append(f"⏭ Пропущено: {len(skipped)}")
    if skipped:
        lines.append(", ".join(skipped))

    await safe_reply(update, context, "\n".join(lines))

# ---- /assign_task conversation ----

async def assign_task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return ConversationHandler.END

    def _tasks(task_service: TaskService):
        return task_service.task_repo.list_available_tasks()

    tasks = with_task_service(_tasks)
    if not tasks:
        await safe_reply(update, context, "😔 Нет доступных задач для назначения.")
        return ConversationHandler.END

    buttons = []
    for t in tasks[:40]:
        project = getattr(getattr(t, "project", None), "title", "—")
        buttons.append([InlineKeyboardButton(
            text=f"#{t.id} [{project}] {t.title[:40]}",
            callback_data=f"at_task:{t.id}"
        )])

    await safe_reply(update, context, "📋 Выбери задачу для назначения:", markup=InlineKeyboardMarkup(buttons))
    return ASSIGN_SELECT_TASK

async def assign_task_select_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ASSIGN_SELECT_TASK
    await query.answer()

    data = query.data or ""
    if not data.startswith("at_task:"):
        return ASSIGN_SELECT_TASK

    task_id = int(data.split(":")[1])

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await query.edit_message_text(f"❌ Задача #{task_id} не найдена.")
        return ConversationHandler.END

    active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
    if len(active_links) >= (task.max_assignees or 1):
        await query.edit_message_text(f"⚠️ Задача #{task_id} уже заполнена по исполнителям ({len(active_links)}/{task.max_assignees}).")
        return ConversationHandler.END

    context.user_data["at_task_id"] = task_id

    def _users(user_service: UserService):
        return user_service.user_repo.list_active_team_members()

    members = with_user_service(_users)
    # Exclude already-assigned users
    assigned_tg_ids = {
        getattr(a.user, "telegram_user_id", None)
        for a in active_links if getattr(a, "user", None)
    }
    members = [m for m in members if m.telegram_user_id not in assigned_tg_ids]

    if not members:
        await query.edit_message_text("😔 Нет доступных участников для назначения.")
        return ConversationHandler.END

    buttons = []
    for m in members:
        label = m.full_name or f"@{m.username}" or str(m.telegram_user_id)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"at_user:{m.telegram_user_id}")])

    await query.edit_message_text(
        f"👤 Выбери участника для задачи #{task_id} «{html.escape(task.title[:50])}»:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return ASSIGN_SELECT_USER

async def assign_task_select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("at_user:"):
        return ASSIGN_SELECT_USER

    tg_user_id = int(data.split(":")[1])
    task_id = context.user_data.get("at_task_id")
    if not task_id:
        await query.edit_message_text("⚠️ Контекст потерян. Запусти /assign_task заново.")
        return ConversationHandler.END

    def _target(user_service: UserService):
        return user_service.get_user_by_telegram_id(tg_user_id)

    target_user = with_user_service(_target)
    if not target_user:
        await query.edit_message_text("❌ Пользователь не найден.")
        return ConversationHandler.END

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)
    task, error = await _do_assign_task(context, task_id, target_user, actor_db_id)

    if error:
        await query.edit_message_text(error)
        return ConversationHandler.END

    full_name = target_user.full_name or f"@{target_user.username}"
    await query.edit_message_text(f"✅ Задача #{task_id} назначена пользователю «{html.escape(full_name)}».")

    try:
        await context.bot.send_message(
            chat_id=target_user.telegram_user_id,
            text=(
                f"📌 Тебе назначена новая задача!\n\n"
                f"<b>{task.title}</b> (#{task_id})\n"
                f"{html.escape(task.description or '')}\n\n"
                f"⏰ Дедлайн: {format_datetime_rus(task.deadline_at)}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    context.user_data.pop("at_task_id", None)
    return ConversationHandler.END

def get_assign_task_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("assign_task", assign_task_entry)],
        states={
            ASSIGN_SELECT_TASK: [CallbackQueryHandler(assign_task_select_task_callback, pattern="^at_task:")],
            ASSIGN_SELECT_USER: [CallbackQueryHandler(assign_task_select_user_callback, pattern="^at_user:")],
        },
        fallbacks=[],
        allow_reentry=True,
    )

async def add_checkitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if len(context.args) < 2 or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Используй: /add_checkitem <ID задачи> <текст пункта>")
        return

    task_id = int(context.args[0])
    title = " ".join(context.args[1:]).strip()
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    def _add(task_service: TaskService):
        return task_service.add_checklist_item(task_id, title)

    item = with_task_service(_add)
    if not item:
        await safe_reply(update, context, "❌ Не удалось добавить пункт чеклиста.")
        return

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="add_checklist_item",
            entity_type="task",
            entity_id=task_id,
            payload={"item_id": item.id, "title": item.title}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="checklist_added",
            old_value=None,
            new_value=item.title,
            note="Добавлен пункт чеклиста"
        )

    with_log_service(_log)

    await safe_reply(update, context, f"✅ Пункт чеклиста добавлен: [{item.id}] {item.title}")

async def delete_checkitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Используй: /delete_checkitem <ID пункта>")
        return

    checklist_id = int(context.args[0])
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    def _get_item(task_service: TaskService):
        return task_service.task_repo.get_checklist_item(checklist_id)

    item = with_task_service(_get_item)
    if not item:
        await safe_reply(update, context, f"❌ Пункт чеклиста #{checklist_id} не найден.")
        return

    task_id = item.task_id
    item_title = item.title

    def _delete(task_service: TaskService):
        return task_service.delete_checklist_item(checklist_id)

    ok = with_task_service(_delete)
    if not ok:
        await safe_reply(update, context, "❌ Не удалось удалить пункт чеклиста.")
        return

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="delete_checklist_item",
            entity_type="task",
            entity_id=task_id,
            payload={"item_id": checklist_id, "title": item_title}
        )
        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="checklist_deleted",
            old_value=item_title,
            new_value=None,
            note="Удалён пункт чеклиста"
        )

    with_log_service(_log)

    await safe_reply(update, context, f"🗑 Удалён пункт чеклиста: {item_title}")

def _build_points_history_page(items: list, page: int, total: int, header: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build paginated points history text + nav keyboard for one page of items."""
    per_page = PH_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)

    lines = [header, ""]

    for item in items:
        amount = int(item.amount) if item.amount is not None else 0
        sign = "+" if amount >= 0 else ""
        task_part = f"#{item.task_id}" if item.task_id else "—"
        source_part = item.source_type or "unknown"
        reason_part = item.reason or "Без причины"
        k_part = f"\n⚖️ K: {item.k_value}" if getattr(item, "k_value", None) is not None else ""

        created_at = getattr(item, "created_at", None)
        if created_at:
            try:
                dt_text = format_datetime_rus(created_at.astimezone(WORK_TZ))
            except Exception:
                dt_text = created_at.strftime("%Y-%m-%d %H:%M")
        else:
            dt_text = "Дата неизвестна"

        lines.append(
            f"#{item.id} | user_id={item.user_id}\n"
            f"🏆 {sign}{amount} баллов\n"
            f"📌 Источник: {html.escape(source_part)}\n"
            f"🧩 task_id: {task_part}"
            f"{k_part}\n"
            f"📝 {html.escape(reason_part)}\n"
            f"🕒 {dt_text}"
        )
        lines.append("")

    lines.append(f"📄 Страница {page}/{total_pages} (всего {total} записей)")

    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:3900] + "\n\n...[обрезано]"

    # Build nav row
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"ph_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ph_noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"ph_page:{page+1}"))

    markup = InlineKeyboardMarkup([nav]) if len(nav) > 1 else None
    return text, markup

async def points_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_points"):
        await safe_reply(update, context, "❌ У тебя нет доступа к истории баллов.")
        return

    full_name = None
    target_user_id = None

    if context.args:
        full_name = " ".join(context.args).strip()

        def _find_user(user_service: UserService):
            return user_service.user_repo.get_by_full_name(full_name)

        target_user = with_user_service(_find_user)
        if not target_user:
            await safe_reply(update, context, f"❌ Пользователь «{html.escape(full_name)}» не найден.")
            return
        target_user_id = target_user.id

    # Fetch ALL records (no limit) so we can paginate properly
    def _run(log_service: LogService):
        return log_service.get_recent_points_ledger_filtered(
            user_id=target_user_id,
            limit=10000,
        )

    all_items = with_log_service(_run)
    if not all_items:
        msg = f"📭 История баллов для {html.escape(full_name)} пуста." if full_name else "📭 История баллов пуста."
        await safe_reply(update, context, msg)
        return

    # Store for pagination callback
    context.user_data["ph_items"] = all_items
    context.user_data["ph_full_name"] = full_name

    header = f"🏆 <b>История баллов {html.escape(full_name)}</b>" if full_name else "🏆 <b>Общая история баллов</b>"
    page_items = all_items[:PH_PER_PAGE]
    text, markup = _build_points_history_page(page_items, 1, len(all_items), header)

    await safe_reply(update, context, text, markup=markup, parse_mode="HTML")

async def points_history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "ph_noop":
        return
    if not data.startswith("ph_page:"):
        return

    page = int(data.split(":")[1])
    all_items = context.user_data.get("ph_items", [])
    full_name = context.user_data.get("ph_full_name")

    if not all_items:
        await query.edit_message_text("⚠️ Данные устарели. Запусти /points_history заново.")
        return

    per_page = PH_PER_PAGE
    total = len(all_items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    page_items = all_items[start:start + per_page]

    header = f"🏆 <b>История баллов {html.escape(full_name)}</b>" if full_name else "🏆 <b>Общая история баллов</b>"
    text, markup = _build_points_history_page(page_items, page, total, header)

    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

async def set_next_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_events"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if len(context.args) < 2:
        await safe_reply(
            update,
            context,
            "⚠️ Используй: /set_next_meeting <YYYY-MM-DD> <HH:MM>\n"
            "Пример: /set_next_meeting 2026-04-22 18:30"
        )
        return

    raw_dt = f"{context.args[0]} {context.args[1]}"

    try:
        naive_dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M")
        new_dt = naive_dt.replace(tzinfo=WORK_TZ)
    except ValueError:
        await safe_reply(update, context, "⚠️ Формат даты: YYYY-MM-DD HH:MM")
        return

    now = datetime.now(WORK_TZ)

    def _get(event_service: EventService):
        return event_service.get_next_team_meeting(now)

    next_meeting = with_event_service(_get)

    if not next_meeting:
        def _create(event_service: EventService):
            return event_service.create_team_meeting(
                title="Еженедельное собрание команды",
                description="Создано администратором вручную.",
                dt_value=new_dt,
                created_by_user_id=get_internal_user_id_by_tg(update.effective_user.id),
            )
        created = with_event_service(_create)

        await safe_reply(
            update,
            context,
            f"✅ Ближайшее собрание создано на {format_datetime_rus(new_dt)}."
        )
        return

    def _update(event_service: EventService):
        return event_service.update_event_datetime(next_meeting["id"], new_dt)

    updated = with_event_service(_update)
    if not updated:
        await safe_reply(update, context, "❌ Не удалось изменить ближайшее собрание.")
        return

    await safe_reply(
        update,
        context,
        f"✅ Ближайшее собрание перенесено на {format_datetime_rus(new_dt)}."
    )

async def idle_task_reminder(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(WORK_TZ)

    def _idle(user_service: UserService):
        return user_service.get_idle_users(now, IDLE_REMINDER_DAYS)

    idle_users = with_user_service(_idle)
    if not idle_users:
        return

    for user in idle_users:
        tg_id = user.telegram_user_id
        user_record = get_user_by_id(tg_id)
        if not user_record:
            continue

        def _suggest(task_service: TaskService):
            return task_service.get_suggested_tasks_for_user(user_record, limit=3)

        suggested = with_task_service(_suggest)

        if suggested:
            def _cards(task_service: TaskService):
                return [task_service.format_task_card(t) for t in suggested]

            cards = with_task_service(_cards)

            text = (
                "👋 Привет! Ты давно не брал задачи.\n"
                "Вот несколько подходящих под твою роль вариантов:\n\n"
                + "\n\n".join(cards)
                + "\n\nИспользуй /get_task, чтобы взять задачу."
            )

            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # обновляем метку независимо от результата, чтобы не спамить каждый прогон
        def _mark(user_service: UserService):
            return user_service.update_last_idle_reminder(tg_id, now)

        with_user_service(_mark)

async def finish_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_events"):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return ConversationHandler.END

    now = datetime.now(WORK_TZ)

    def _get(event_service: EventService):
        return event_service.get_last_started_meeting(now)

    meeting = with_event_service(_get)
    if not meeting:
        await safe_reply(update, context, "⚠️ Не найдено начатого собрания, которое можно завершить.")
        return ConversationHandler.END

    def _finish(event_service: EventService):
        return event_service.finish_meeting(meeting["id"], now)

    finished = with_event_service(_finish)
    if not finished:
        await safe_reply(update, context, "❌ Не удалось завершить собрание.")
        return ConversationHandler.END

    context.user_data["meeting_attendance_event_id"] = finished["id"]
    context.user_data["meeting_attendance_title"] = finished["title"]
    context.user_data["meeting_attendance_project_id"] = finished.get("project_id")

    def _team_users(user_service: UserService):
        users = user_service.user_repo.list_active_team_members()
        return [u.telegram_user_id for u in users if getattr(u, "telegram_user_id", None)]

    recipient_ids = with_user_service(_team_users)

    for tg_user_id in recipient_ids:
        try:
            await context.bot.send_message(
                chat_id=tg_user_id,
                text=(
                    f"📢 <b>Собрание окончено</b>\n\n"
                    f"📌 <b>{html.escape(finished['title'])}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await safe_reply(
        update,
        context,
        "✅ Собрание отмечено как завершённое.\n\n"
        "Теперь отправь список присутствовавших участников.\n"
        "Пример:\n"
        "@StanPaige @user2 @user3"
    )
    return MEETING_ATTENDANCE_INPUT

async def finish_meeting_attendance_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_user:
        clear_meeting_attendance_data(context)
        return ConversationHandler.END

    event_id = context.user_data.get("meeting_attendance_event_id")
    meeting_title = context.user_data.get("meeting_attendance_title")

    if not event_id:
        clear_meeting_attendance_data(context)
        await safe_reply(update, context, "⚠️ Контекст attendance потерян. Запусти /finish_meeting заново.")
        return ConversationHandler.END

    usernames = parse_attendance_usernames(update.message.text)

    if not usernames:
        await safe_reply(update, context, "⚠️ Не удалось распознать usernames. Отправь список вида: @user1 @user2")
        return MEETING_ATTENDANCE_INPUT

    def _find_users(user_service: UserService):
        found = []
        for username in usernames:
            user = user_service.user_repo.get_by_username(username)
            if user:
                found.append(user)
        return found

    found_users = with_user_service(_find_users)
    if not found_users:
        await safe_reply(update, context, "⚠️ Ни один пользователь не найден. Проверь usernames.")
        return MEETING_ATTENDANCE_INPUT

    present_tg_ids = [u.telegram_user_id for u in found_users]

    def _save(event_service: EventService):
        return event_service.save_attendance(
            event_id=event_id,
            present_tg_ids=present_tg_ids,
            marked_by_tg_id=update.effective_user.id,
        )

    save_result = with_event_service(_save)
    if save_result is None:
        clear_meeting_attendance_data(context)
        await safe_reply(update, context, "⚠️ Attendance для этого собрания уже был внесён ранее.")
        return ConversationHandler.END

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)

    project_id = get_teamwork_project_id()
    if not project_id:
        clear_meeting_attendance_data(context)
        await safe_reply(update, context, "⚠️ Не найден проект Teamwork для начисления баллов за attendance.")
        return ConversationHandler.END

    awarded = []

    def _award(points_service: PointsService):
        local_awarded = []
        for user in found_users:
            ok = points_service.add_points(
                telegram_user_id=user.telegram_user_id,
                points_to_add=5,
                project_id=project_id,
                project_name="Teamwork",
                reason=f"Посещение собрания #{event_id}: {meeting_title}",
                event_id=event_id,
                source_type="meeting_attendance",
                created_by_user_id=actor_db_id,
            )
            if ok:
                local_awarded.append(user)
        return local_awarded

    awarded_users = with_points_service(_award) or []

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="save_meeting_attendance",
            entity_type="event",
            entity_id=event_id,
            payload={
                "title": meeting_title,
                "present_usernames": [u.username for u in found_users],
                "present_count": len(found_users),
            }
        )

    with_log_service(_log)

    present_labels = []
    for user in awarded_users:
        label = f"@{user.username}" if user.username else user.full_name or str(user.telegram_user_id)
        present_labels.append(label)

    await safe_reply(
        update,
        context,
        "✅ Attendance сохранён.\n\n"
        f"👥 Присутствовали: {len(awarded_users)}\n"
        f"🏆 Всем отмеченным начислено +5 баллов.\n\n"
        + "\n".join(f"• {name}" for name in present_labels)
    )

    for user in awarded_users:
        try:
            await context.bot.send_message(
                chat_id=user.telegram_user_id,
                text=(
                    f"✅ Ты отмечен как присутствовавший на собрании.\n"
                    f"🏆 Начислено <b>+5</b> баллов."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    clear_meeting_attendance_data(context)
    return ConversationHandler.END

async def finish_meeting_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_meeting_attendance_data(context)
    await safe_reply(update, context, "❌ Ввод attendance отменён.")
    return ConversationHandler.END

def get_finish_meeting_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("finish_meeting", finish_meeting)],
        states={
            MEETING_ATTENDANCE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, finish_meeting_attendance_input)
            ],
        },
        fallbacks=[CommandHandler("cancel", finish_meeting_cancel)],
        allow_reentry=True,
    )

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "view_admin_reports"):
        await safe_reply(update, context, "❌ У тебя нет доступа к логам.")
        return

    mode = context.args[0].lower() if context.args else "audit"

    def _run(log_service: LogService):
        if mode == "errors":
            return ("errors", log_service.get_recent_error_logs(limit=15))
        if mode == "tasks":
            return ("tasks", log_service.get_recent_task_history(limit=15))
        if mode == "points":
            return ("points", log_service.get_recent_points_ledger(limit=15))
        return ("audit", log_service.get_recent_audit_logs(limit=15))

    log_type, items = with_log_service(_run)

    if not items:
        await safe_reply(update, context, "📭 Логи пусты.")
        return

    lines = [f"📜 <b>Логи: {log_type}</b>", ""]

    if log_type == "audit":
        for item in items:
            lines.append(
                f"#{item.id} | {item.action_type}\n"
                f"entity: {item.entity_type} ({item.entity_id})\n"
                f"actor_user_id: {item.actor_user_id}\n"
                f"{item.payload_json or ''}"
            )
            lines.append("")

    elif log_type == "errors":
        for item in items:
            lines.append(
                f"#{item.id} | {item.source}\n"
                f"{item.message}"
            )
            lines.append("")

    elif log_type == "tasks":
        for item in items:
            lines.append(
                f"#{item.id} | task_id={item.task_id}\n"
                f"{item.action_type}: {item.old_value} → {item.new_value}\n"
                f"{item.note or ''}"
            )
            lines.append("")

    elif log_type == "points":
        for item in items:
            lines.append(
                f"#{item.id} | user_id={item.user_id} | project_id={item.project_id}\n"
                f"{item.source_type}: {item.amount}\n"
                f"{item.reason or ''}"
            )
            lines.append("")

    text = "\n".join(lines).strip()

    if len(text) > 4000:
        text = text[:3900] + "\n\n...[обрезано]"

    await safe_reply(update, context, text, parse_mode="HTML")

# =========================
# APP INIT
# =========================

def build_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        ["🔧 Взять задачу", "📝 Мои задачи"],
        ["📅 События", "🏆 Мои баллы"],
        ["📊 Лидерборд", "❓ Помощь"],
    ]

    if has_permission(user_id, "manage_tasks") or has_permission(user_id, "review_tasks"):
        rows.append(["🛠 Админ задачи"])

    if has_permission(user_id, "manage_events"):
        rows.append(["📢 Управление событиями"])

    if has_permission(user_id, "manage_points"):
        rows.append(["💰 Управление баллами"])

    if has_permission(user_id, "view_admin_reports"):
        rows.append(["📜 Логи"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def admin_tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_tasks"):
        await safe_reply(update, context, "❌ Нет доступа.")
        return

    await safe_reply(
        update,
        context,
        "🛠 Раздел задач администратора:\n"
        "/add_task\n"
        "/assign_task\n"
        "/unassign_task\n"
        "/block_task\n"
        "/unblock_task\n"
        "/set_deadline\n"
        "/run_overdue\n"
        "/overdue_tasks"
    )

async def admin_events_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_events"):
        await safe_reply(update, context, "❌ Нет доступа.")
        return

    await safe_reply(
        update,
        context,
        "📢 Раздел событий:\n"
        "/set_next_meeting\n"
        "/finish_meeting\n"
        "/upcoming_events"
    )

async def admin_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "manage_points"):
        await safe_reply(update, context, "❌ Нет доступа.")
        return

    await safe_reply(
        update,
        context,
        "💰 Раздел баллов:\n"
        "/give_points\n"
        "/check_points <username>\n"
        "/points_history [username]\n"
        "/leaderboard\n"
        "/leaderboard_project <ID>"
    )

async def admin_logs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not has_permission(update.effective_user.id, "view_admin_reports"):
        await safe_reply(update, context, "❌ Нет доступа.")
        return

    await safe_reply(
        update,
        context,
        "📜 Раздел логов:\n"
        "/logs audit\n"
        "/logs errors\n"
        "/logs tasks\n"
        "/logs points"
    )

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Моё приветствие"),
        BotCommand("help", "Все доступные команды"),
        BotCommand("upcoming_events", "Посмотреть грядущие события"),
        BotCommand("my_points", "Увидеть свои баллы"),
        BotCommand("my_task", "Посмотреть свои задачи"),
        BotCommand("submit_task", "Отправить задачу на проверку"),
        BotCommand("get_task", "Взять новую задачу"),
    ])

    if app.job_queue:
        app.job_queue.run_repeating(
            event_auto_notify,
            interval=60,
            first=10,
            name="event_auto_notify",
        )

        app.job_queue.run_repeating(
            ensure_weekly_meeting_exists,
            interval=3600,
            first=15,
            name="ensure_weekly_meeting_exists",
        )

        app.job_queue.run_repeating(
            idle_task_reminder_job,
            interval=86400,            # раз в сутки
            first=IDLE_REMINDER_START_DELAY_SEC,
            name="idle_task_reminder",
        )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("=== ERROR HANDLER ===")
    print("Update:", update)
    print("Error:", context.error)

    tb_text = "".join(traceback.format_exception(
        type(context.error),
        context.error,
        context.error.__traceback__,
    ))
    print(tb_text)

    try:
        def _log(log_service: LogService):
            return log_service.log_error(
                source="telegram_bot",
                message=str(context.error),
                traceback_text=tb_text,
            )
        with_log_service(_log)
    except Exception as e:
        print("FAILED TO WRITE ERROR LOG:", e)

def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не найден в переменных окружения")

    app = ApplicationBuilder().token(bot_token).post_init(post_init).build()

    app.add_handler(MessageHandler(filters.Regex(r"^📝 Мои задачи$"), my_task))
    app.add_handler(MessageHandler(filters.Regex(r"^📅 События$"), upcoming_events))
    app.add_handler(MessageHandler(filters.Regex(r"^🏆 Мои баллы$"), my_points))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Лидерборд$"), leaderboard))
    app.add_handler(MessageHandler(filters.Regex(r"^❓ Помощь$"), help_command))
    app.add_handler(MessageHandler(filters.Regex(r"^🛠 Админ задачи$"), admin_tasks_menu))
    app.add_handler(MessageHandler(filters.Regex(r"^📢 Управление событиями$"), admin_events_menu))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Управление баллами$"), admin_points_menu))
    app.add_handler(MessageHandler(filters.Regex(r"^📜 Логи$"), admin_logs_menu))
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin_help", admin_help))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("upcoming_events", upcoming_events))
    app.add_handler(CommandHandler("set_my_status", set_my_status))

    app.add_handler(build_give_points_handler(
        get_points_service=create_points_service,
        has_access=lambda user_id: has_permission(user_id, "manage_points"),
    ))

    app.add_handler(CommandHandler("my_points", my_points))
    app.add_handler(CommandHandler("check_points", check_points))
    app.add_handler(CommandHandler("points_history", points_history))
    app.add_handler(CallbackQueryHandler(points_history_page_callback, pattern="^ph_page:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer() if u.callback_query else None, pattern="^ph_noop$"))
    app.add_handler(CommandHandler("leaderboard", leaderboard_menu))
    app.add_handler(CallbackQueryHandler(leaderboard_project_callback, pattern="^lb_proj:"))
    app.add_handler(CommandHandler("leaderboard_project", leaderboard_project))
    app.add_handler(CommandHandler("my_task", my_task))
    app.add_handler(CallbackQueryHandler(submit_task_callback, pattern="^submit_select:"))
    app.add_handler(CommandHandler("submit_task", submit_task))
    app.add_handler(get_task_done_handler())
    app.add_handler(CommandHandler("return_task", return_task))
    app.add_handler(get_unassign_task_handler())
    app.add_handler(get_assign_task_handler())
    app.add_handler(get_block_task_handler())
    app.add_handler(get_unblock_task_handler())
    app.add_handler(CommandHandler("set_user_status", set_user_status))
    app.add_handler(CommandHandler("set_deadline", set_deadline))
    app.add_handler(CommandHandler("run_overdue", run_overdue_now))
    app.add_handler(CommandHandler("overdue_tasks", show_overdue))
    app.add_handler(CommandHandler("run_idle_check", run_idle_check_now))
    app.add_handler(CommandHandler("set_next_meeting", set_next_meeting))
    app.add_handler(CommandHandler("task_checklist", task_checklist))
    app.add_handler(CommandHandler("add_checkitem", add_checkitem))
    app.add_handler(CommandHandler("toggle_checkitem", toggle_checkitem))
    app.add_handler(CommandHandler("delete_checkitem", delete_checkitem))
    app.add_handler(CallbackQueryHandler(task_checklist_callback, pattern="^checklist_task_select:"))
    app.add_handler(CallbackQueryHandler(toggle_checkitem_task_callback, pattern="^toggle_task_select:"))
    app.add_handler(CallbackQueryHandler(toggle_checkitem_item_callback, pattern="^toggle_item_select:"))
    app.add_handler(get_finish_meeting_handler())
    app.add_handler(get_add_task_handler())
    app.add_handler(get_task_handler())
    app.add_error_handler(error_handler)

    app.run_polling()


if __name__ == "__main__":
    main()