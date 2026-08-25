# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from typing import Callable, List, Tuple

import html
import shlex

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters

from services.points_service import PointsService, AmbiguousFullNameError


GP_USER, GP_PROJECT, GP_POINTS, GP_CONFIRM = range(200, 204)


def _build_name_picker(get_points_service: Callable[[], PointsService]) -> Tuple[List[List[str]], dict]:
    """Build reply-keyboard rows for picking a team member by name.

    full_name is not unique-constrained, so when two users share a name we
    disambiguate the button label with their @username (or db id, if no
    username) rather than showing two identical, unpickable buttons.
    Returns (rows_for_keyboard, {label: telegram_user_id}).
    """
    points_service = get_points_service()
    users = points_service.user_repo.list_all()
    named = [u for u in users if (u.full_name or "").strip()]

    name_counts = Counter((u.full_name or "").strip().lower() for u in named)

    label_map: dict[str, int] = {}
    for u in sorted(named, key=lambda u: u.full_name.strip().lower()):
        name = u.full_name.strip()
        if name_counts[name.lower()] > 1:
            tag = f"@{u.username}" if u.username else f"id{u.telegram_user_id}"
            label = f"{name} ({tag})"
        else:
            label = name
        label_map[label] = u.telegram_user_id

    rows = [[label] for label in label_map.keys()]
    return rows, label_map


def _all_projects(get_points_service: Callable[[], PointsService]) -> List[dict]:
    points_service = get_points_service()
    db = points_service.user_repo.db

    from database.models import Project

    projects = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .order_by(Project.title.asc())
        .all()
    )

    return [
        {
            "id": p.id,
            "title": p.title,
        }
        for p in projects
    ]

def _apply_points_by_user_id(
    get_points_service: Callable[[], PointsService],
    telegram_user_id: int,
    display_name: str,
    project_id: int,
    project_title: str,
    delta: int,
) -> None:
    """Give points to a user already resolved to a specific telegram_user_id.
    No name lookup happens here, so there's no ambiguity risk left to hit."""
    points_service = get_points_service()
    ok = points_service.add_points(
        telegram_user_id=telegram_user_id,
        points_to_add=int(delta),
        project_id=project_id,
        project_name=project_title,
        reason="Ручное начисление админом",
        source_type="manual",
    )
    if not ok:
        raise ValueError(f"Не удалось начислить баллы пользователю «{display_name}»")


def _apply_points_by_name_or_tag(
    get_points_service: Callable[[], PointsService],
    name_or_tag: str,
    project_id: int,
    project_title: str,
    delta: int,
) -> None:
    """Used by the quick-args path (typed text, no picker involved).

    Accepts either a plain full name or an @username tag. A bare full name
    that matches more than one person raises AmbiguousFullNameError instead
    of guessing — the caller must catch it and show the candidates.
    """
    points_service = get_points_service()

    if name_or_tag.startswith("@") and len(name_or_tag) > 1:
        user = points_service.user_repo.get_by_username(name_or_tag[1:])
        if not user:
            raise ValueError(f"Пользователь «{name_or_tag}» не найден")
        _apply_points_by_user_id(
            get_points_service, user.telegram_user_id, user.full_name or name_or_tag,
            project_id, project_title, delta,
        )
        return

    summary = points_service.get_user_points_summary_by_full_name(name_or_tag)
    if not summary:
        raise ValueError(f"Пользователь «{name_or_tag}» не найден")

    _apply_points_by_user_id(
        get_points_service, summary["user_id"], name_or_tag,
        project_id, project_title, delta,
    )



def build_give_points_handler(
    *,
    get_points_service: Callable[[], PointsService],
    has_access: Callable[[int], bool],
):
    async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not has_access(update.effective_user.id):
            if update.message:
                await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
            return ConversationHandler.END

        if context.args:
            try:
                args = shlex.split(" ".join(context.args))
                if len(args) < 3:
                    raise ValueError("Мало аргументов")

                name_or_tag = args[0]
                project = args[1]
                delta = int(args[2])

                _apply_points_by_name_or_tag(get_points_service, name_or_tag, project, delta)

                await update.message.reply_text(
                    f"✅ Добавлено {delta} балл(ов) пользователю «{name_or_tag}» по «{project}»."
                )
                return ConversationHandler.END

            except AmbiguousFullNameError as e:
                lines = [f"⚠️ Найдено несколько участников с именем «{e.full_name}»:", ""]
                for u in e.candidates:
                    tag = f"@{u.username}" if u.username else f"id{u.telegram_user_id}"
                    status = "🟢" if u.is_active else "⚪"
                    lines.append(f"{status} {u.full_name} ({tag})")
                lines.append("")
                lines.append(f"Уточни: /give_points \"{e.full_name}\" @username <проект> <баллы>")
                lines.append("Или выбери участника ниже — там имена уже без путаницы:")
                await update.message.reply_text("\n".join(lines))

            except Exception as e:
                await update.message.reply_text(f"⚠️ {e}\nЗапускаю мастер добавления баллов…")

        rows, label_map = _build_name_picker(get_points_service)
        context.user_data["gp_label_map"] = label_map
        kb = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Кому начислим баллы? Выбери участника:", reply_markup=kb)
        return GP_USER

    async def step_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        label = update.message.text.strip()
        label_map = context.user_data.get("gp_label_map", {})

        if label not in label_map:
            await update.message.reply_text("⚠️ Выбери участника кнопкой из списка.")
            return GP_USER

        # Resolved to an exact telegram_user_id at picker time — no name
        # lookup (and therefore no ambiguity) happens later in this flow.
        context.user_data["gp_telegram_user_id"] = label_map[label]
        context.user_data["gp_full_name"] = label

        projects = _all_projects(get_points_service)

        context.user_data["gp_projects_map"] = {
            p["title"]: p["id"] for p in projects
        }

        rows = [[p["title"]] for p in projects[:20]]

        await update.message.reply_text(
            "Выбери проект:",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
        )
        return GP_PROJECT

    async def step_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        title = update.message.text.strip()
        projects_map = context.user_data.get("gp_projects_map", {})

        if title not in projects_map:
            await update.message.reply_text("⚠️ Выбери проект кнопкой из списка.")
            return GP_PROJECT

        context.user_data["gp_project_title"] = title
        context.user_data["gp_project_id"] = projects_map[title]

        await update.message.reply_text(
            "Сколько баллов добавить? Примеры: 20, +15, -5",
            reply_markup=ReplyKeyboardRemove(),
        )
        return GP_POINTS

    async def step_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        raw = update.message.text.replace("+", "").strip()
        try:
            context.user_data["gp_delta"] = int(raw)
        except ValueError:
            await update.message.reply_text("Число не распознано. Введи целое число, например 15 или -5.")
            return GP_POINTS

        u = context.user_data["gp_full_name"]
        p = context.user_data["gp_project_title"]
        d = context.user_data["gp_delta"]

        text = (
            f"Подтверди начисление:\n\n"
            f"🧑 {html.escape(u)}\n"
            f"📂 Проект: {html.escape(p)}\n"
            f"🏆 Баллы: {d}\n\n"
            f"Отправить?"
        )
        kb = ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(text, reply_markup=kb)
        return GP_CONFIRM

    async def step_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        if update.message.text.lower().startswith("д"):
            try:
                _apply_points_by_user_id(
                    get_points_service,
                    context.user_data["gp_telegram_user_id"],
                    context.user_data["gp_full_name"],
                    context.user_data["gp_project_id"],
                    context.user_data["gp_project_title"],
                    context.user_data["gp_delta"],
                )
                await update.message.reply_text("✅ Готово!", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove())

        for k in ("gp_full_name", "gp_telegram_user_id", "gp_label_map", "gp_project_id", "gp_project_title", "gp_projects_map", "gp_delta"):
            context.user_data.pop(k, None)

        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[CommandHandler("give_points", entry)],
        states={
            GP_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_user)],
            GP_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_project)],
            GP_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_points)],
            GP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_confirm)],
        },
        fallbacks=[MessageHandler(filters.Regex(r"^(отмена|cancel)$"), step_confirm)],
        allow_reentry=True,
    )