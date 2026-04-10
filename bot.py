import os
import re
from datetime import datetime
import html

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
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
from config import ADMIN_ID, WORK_TZ, MONTH_NAMES, DEFAULT_PROJECTS, MAX_ACTIVE_TASKS_PER_USER
import traceback

SELECT_PROJECT, SELECT_TASK, CONFIRM = range(3)


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
        service = PointsService(UserRepository(db))
        return func(service)
    finally:
        db.close()


def create_points_service():
    db = SessionLocal()
    repo = UserRepository(db)
    service = PointsService(repo)
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


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id == ADMIN_ID)


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

# =========================
# USER COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    main_kb = ReplyKeyboardMarkup(
        [["🔧 Взять задачу"]],
        resize_keyboard=True,
    )
    await safe_reply(
        update,
        context,
        "Здравствуй. Бот запущен и готов к работе. Используй /help, чтобы увидеть доступные команды.",
        markup=main_kb,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return

    text = (
        "📖 <b>Доступные команды:</b>\n\n"
        "/start — запустить бота\n"
        "/help — показать список команд\n"
        "/upcoming_events — ближайшие события\n"
        "/my_points — мои баллы\n"
        "/my_task — мои текущие задачи\n"
        "/get_task — взять новую задачу"
    )
    await safe_reply(update, context, text, parse_mode="HTML")


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
        return [task_service.task_to_legacy_dict(t) for t in tasks]

    reserved_tasks = with_task_service(_run)
    print("reserved_tasks =", reserved_tasks)
    if not reserved_tasks:
        await safe_reply(update, context, "😔 У тебя сейчас нет активных задач. Используй /get_task.")
        return

    lines = ["📝 <b>Твои текущие задачи:</b>", ""]
    for task in reserved_tasks:
        if task.get("deadline"):
            dt = datetime.fromisoformat(task["deadline"]).replace(tzinfo=WORK_TZ)
            deadline_str = format_datetime_rus(dt)
        else:
            deadline_str = "Не назначен"

        lines.append(
            f"🔹 <b>{task['title']}</b> (#{task['id']})\n"
            f"📄 {task['description']}\n"
            f"📂 Тип: {task['type']}\n"
            f"🏆 Баллы: {task['points']}\n"
            f"⏰ Дедлайн: {deadline_str}"
        )
        lines.append("")

    await safe_reply(update, context, "\n".join(lines).strip(), parse_mode="HTML")


# =========================
# GET TASK FLOW
# =========================

async def get_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return ConversationHandler.END
    if not update.effective_user:
        return ConversationHandler.END

    user_id = update.effective_user.id

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

    def _assign(task_service: TaskService):
        return task_service.assign_task_with_auto_deadline(task_id, user_id, WORK_TZ)

    task = with_task_service(_assign)
    actor_db_id = get_internal_user_id_by_tg(user_id)

    if not task:
        await query.edit_message_text(f"⚠️ Задача #{task_id} не найдена или недоступна.")
        return ConversationHandler.END

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
            telegram_user_id=user_id,
            title=f"Дедлайн по задаче #{task.id}",
            description="Пожалуйста, заверши работу в срок.",
            dt_value=task.deadline,
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
# ADMIN COMMANDS
# =========================

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    help_text = (
        "🗝️ <b>Админ-команды:</b>\n\n"
        "/give_points — добавить баллы участнику\n"
        "/check_points — проверить баллы участника\n"
        "/task_done — пометить задачу выполненной\n"
        "/unassign_task — снять участника с задачи\n"
        "/assign_task — назначить задачу участнику\n"
        "/logs [audit|errors|tasks|points] — посмотреть логи\n"
    )
    await safe_reply(update, context, help_text, parse_mode="HTML")


async def check_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Формат: /check_points <username>")
        return

    username = context.args[0].lstrip("@")

    def _run(points_service: PointsService):
        return points_service.get_user_points_summary_by_username(username)

    summary = with_points_service(_run)
    if not summary:
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    projects = summary.get("projects", {})
    if not projects:
        await update.message.reply_text(f"📊 У @{username} пока нет баллов.")
        return

    lines = [f"📊 <b>Баллы @{username}:</b>", ""]
    for project_name in sorted(projects.keys()):
        item = projects[project_name]
        points = item.get("points", 0)
        percent = float(item.get("percent_rate", 0.0)) * 100
        lines.append(f"🔹 <b>{project_name}</b>: {points} баллов ({round(percent)}%)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        await safe_reply(update, context, "⚠️ У тебя нет прав для этой команды.")
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Укажи ID задачи: /task_done <ID>")
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
    
    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)
    
    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="task_done",
            entity_type="task",
            entity_id=task_id,
            payload={"title": task_title}
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="status_changed",
            old_value="in_progress",
            new_value="done",
            note="Админ пометил задачу выполненной"
        )

    with_log_service(_log)

    def _remove_events(event_repo: EventRepository):
        return event_repo.remove_by_task_id(task_id)

    with_event_repo(_remove_events)
    await safe_reply(update, context, f"✅ Задача #{task_id} помечена как выполненная.")

    if reserved_by:
        try:
            await context.bot.send_message(
                chat_id=reserved_by,
                text=f"🎉 Задача <b>{task_title}</b> (#{task_id}) помечена как выполненная. Спасибо за работу!",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def unassign_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not is_admin(update.effective_user.id):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, context, "⚠️ Используй: /unassign_task <ID задачи>")
        return

    task_id = int(context.args[0])

    def _get(task_service: TaskService):
        return task_service.get_task_by_id(task_id)

    task = with_task_service(_get)
    if not task:
        await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
        return

    reserved_by = task.assignee.telegram_user_id if task.assignee else None
    task_title = task.title
    if not reserved_by:
        await safe_reply(update, context, f"⚠️ Задача #{task_id} уже свободна.")
        return

    def _unassign(task_service: TaskService):
        return task_service.unassign_task(task_id)


    with_task_service(_unassign)

    actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)
    removed_user_db_id = get_internal_user_id_by_tg(reserved_by)

    def _log(log_service: LogService):
        log_service.log_audit(
            actor_user_id=actor_db_id,
            action_type="unassign_task",
            entity_type="task",
            entity_id=task_id,
            payload={
                "title": task_title,
                "removed_from_user_id": removed_user_db_id,
                "removed_from_telegram_user_id": reserved_by,
            }
        )

        log_service.log_task_history(
            task_id=task_id,
            actor_user_id=actor_db_id,
            action_type="unassigned",
            old_value=str(removed_user_db_id) if removed_user_db_id is not None else None,
            new_value=None,
            note="Админ снял пользователя с задачи"
        )
    
    with_log_service(_log)

    def _remove_events(event_repo: EventRepository):
        return event_repo.remove_by_task_id(task_id)

    removed = with_event_repo(_remove_events)
    await safe_reply(update, context, f"✅ Задача #{task_id} теперь свободна. Удалено связанных событий: {removed}.")

    try:
        await context.bot.send_message(
            chat_id=reserved_by,
            text=f"⚠️ Задача <b>{task_title}</b> (#{task_id}) была снята с тебя администратором.",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def assign_task_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_membership(update, context):
        return
    if not update.effective_user or not is_admin(update.effective_user.id):
        await safe_reply(update, context, "❌ Ты слишком слаб чтобы использовать это заклинание")
        return

    if not context.args or len(context.args) < 2:
        await safe_reply(
            update,
            context,
            "⚠️ Используй так:\n<code>/assign_task &lt;ID задачи&gt; &lt;username&gt;</code>\n\nПример:\n<code>/assign_task 2 Franky126866</code>",
            parse_mode="HTML",
        )
        return

    try:
        task_id = int(context.args[0])
        username = context.args[1].lstrip("@").strip().lower()

        def _target(user_service: UserService):
            return user_service.user_repo.get_by_username(username)

        target_user = with_user_service(_target)
        if not target_user:
            await safe_reply(update, context, f"❌ Пользователь @{username} не найден.")
            return

        def _get_task(task_service: TaskService):
            return task_service.get_task_by_id(task_id)

        task_before = with_task_service(_get_task)
        if not task_before:
            await safe_reply(update, context, f"❌ Задача #{task_id} не найдена.")
            return

        active_links = [a for a in getattr(task_before, "assignees", []) if getattr(a, "is_active", False)]
        if len(active_links) >= (task_before.max_assignees or 1):
            await safe_reply(update, context, f"⚠️ Задача #{task_id} уже заполнена по исполнителям.")
            return

        def _assign(task_service: TaskService):
            return task_service.assign_task_to_user(task_id, target_user.telegram_user_id, WORK_TZ)

        task = with_task_service(_assign)
        if not task:
            await safe_reply(update, context, f"❌ Не удалось назначить задачу #{task_id}.")
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
                description="Администратор назначил тебе задачу.",
                dt_value=task.deadline,
            )
        
        actor_db_id = get_internal_user_id_by_tg(update.effective_user.id)
        
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
        await safe_reply(update, context, f"✅ Задача #{task_id} назначена пользователю @{username}.")

        try:
            await context.bot.send_message(
                chat_id=target_user.telegram_user_id,
                text=(
                    f"📌 Тебе назначена новая задача!\n\n"
                    f"<b>{task.title}</b> (#{task_id})\n"
                    f"{html.escape(task.description or '')}\n\n"
                    f"⏰ Дедлайн: {format_datetime_rus(task.deadline)}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    except Exception as e:
        await safe_reply(update, context, f"❌ Ошибка: {e}")


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
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

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Моё приветствие"),
        BotCommand("help", "Все доступные команды"),
        BotCommand("upcoming_events", "Посмотреть грядущие события"),
        BotCommand("my_points", "Увидеть свои баллы"),
        BotCommand("my_task", "Посмотреть свои задачи"),
        BotCommand("get_task", "Взять новую задачу"),
    ])

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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin_help", admin_help))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("upcoming_events", upcoming_events))

    app.add_handler(build_give_points_handler(
        admin_id=ADMIN_ID,
        get_points_service=create_points_service,
    ))

    app.add_handler(CommandHandler("my_points", my_points))
    app.add_handler(CommandHandler("check_points", check_points))
    app.add_handler(CommandHandler("my_task", my_task))
    app.add_handler(CommandHandler("task_done", task_done))
    app.add_handler(CommandHandler("unassign_task", unassign_task))
    app.add_handler(CommandHandler("assign_task", assign_task_to_user))
    app.add_handler(get_task_handler())
    app.add_error_handler(error_handler)

    app.run_polling()


if __name__ == "__main__":
    main()
