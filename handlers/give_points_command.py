# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable, List

import html
import shlex

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters

from services.points_service import PointsService


GP_USER, GP_PROJECT, GP_POINTS, GP_REASON, GP_CONFIRM = range(200, 205)

SKIP_REASON_LABEL = "Пропустить"


def _all_target_users(get_points_service: Callable[[], PointsService]) -> List[dict]:
    """List selectable users for the wizard, with disambiguated labels.

    full_name is not unique-constrained, so when two+ users share a name,
    their button labels get a distinguishing @username (or id fallback)
    appended. The label is only ever a display string — resolution always
    happens via telegram_user_id, never by re-parsing the name later.
    """
    points_service = get_points_service()
    users = points_service.user_repo.list_all()

    name_counts: dict[str, int] = {}
    for u in users:
        name = (u.full_name or "").strip()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1

    out = []
    for u in users:
        name = (u.full_name or "").strip()
        if not name:
            continue

        if name_counts[name] > 1:
            tag = f"@{u.username}" if u.username else f"id{u.telegram_user_id}"
            label = f"{name} ({tag})"
        else:
            label = name

        out.append({
            "label": label,
            "full_name": name,
            "telegram_user_id": u.telegram_user_id,
        })

    return sorted(out, key=lambda t: t["label"].lower())


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


def _format_user_matches(users: list) -> str:
    lines = []
    for u in users:
        tag = f"@{u.username}" if u.username else f"id{u.telegram_user_id}"
        lines.append(f"• {u.full_name} ({tag})")
    return "\n".join(lines)


def _resolve_target_user(
    get_points_service: Callable[[], PointsService],
    full_name: str,
    username_hint: str | None = None,
):
    """Resolve exactly one user for /give_points quick-args.

    Returns (user, error_text). If full_name is ambiguous and no
    username_hint was given to disambiguate, error_text explains how to
    fix it (never silently picks a match).
    """
    points_service = get_points_service()
    user_repo = points_service.user_repo

    if username_hint:
        user = user_repo.get_by_username(username_hint)
        if not user:
            return None, f"Пользователь «@{username_hint}» не найден"
        return user, None

    candidates = user_repo.get_all_by_full_name(full_name)
    if len(candidates) == 0:
        return None, f"Пользователь «{full_name}» не найден"
    if len(candidates) == 1:
        return candidates[0], None

    return None, (
        f"Найдено несколько участников с именем «{full_name}»:\n"
        f"{_format_user_matches(candidates)}\n\n"
        f"Уточни через @username, например:\n"
        f'/give_points "{full_name}" @username Проект 20'
    )


def _apply_points(
    get_points_service: Callable[[], PointsService],
    telegram_user_id: int,
    full_name: str,
    project_id: int,
    project_title: str,
    delta: int,
    reason: str | None = None,
) -> None:
    points_service = get_points_service()

    ok = points_service.add_points(
        telegram_user_id=telegram_user_id,
        points_to_add=int(delta),
        project_id=project_id,
        project_name=project_title,
        reason=(reason.strip() if reason and reason.strip() else "Ручное начисление админом"),
        source_type="manual",
    )

    if not ok:
        raise ValueError(f"Не удалось начислить баллы пользователю «{full_name}»")


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

                username_hint = None
                for i, a in enumerate(args):
                    if a.startswith("@") and len(a) > 1:
                        username_hint = args.pop(i)[1:]
                        break

                if len(args) < 3:
                    raise ValueError("Мало аргументов")

                full_name = args[0]
                project = args[1]
                delta = int(args[2])
                reason = args[3] if len(args) > 3 else None

                target_user, err = _resolve_target_user(get_points_service, full_name, username_hint)
                if err:
                    raise ValueError(err)

                _apply_points(
                    get_points_service,
                    target_user.telegram_user_id,
                    target_user.full_name or full_name,
                    project,
                    project,
                    delta,
                    reason=reason,
                )

                suffix = f" Причина: «{reason}»." if reason else ""
                await update.message.reply_text(
                    f"✅ Добавлено {delta} балл(ов) пользователю «{target_user.full_name}» по «{project}».{suffix}"
                )
                return ConversationHandler.END

            except Exception as e:
                await update.message.reply_text(f"⚠️ {e}\nЗапускаю мастер добавления баллов…")

        targets = _all_target_users(get_points_service)
        context.user_data["gp_targets_map"] = {t["label"]: t for t in targets}
        kb = ReplyKeyboardMarkup([[t["label"]] for t in targets], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Кому начислим баллы? Выбери участника:", reply_markup=kb)
        return GP_USER

    async def step_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        label = update.message.text.strip()
        targets_map = context.user_data.get("gp_targets_map", {})
        target = targets_map.get(label)

        if not target:
            await update.message.reply_text("⚠️ Выбери участника кнопкой из списка.")
            return GP_USER

        context.user_data["gp_full_name"] = target["full_name"]
        context.user_data["gp_target_telegram_id"] = target["telegram_user_id"]

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

        kb = ReplyKeyboardMarkup([[SKIP_REASON_LABEL]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "Укажи причину начисления (необязательно). Можно написать текст или нажать «Пропустить».",
            reply_markup=kb,
        )
        return GP_REASON

    async def step_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        text_in = update.message.text.strip()
        reason = "" if text_in == SKIP_REASON_LABEL else text_in
        context.user_data["gp_reason"] = reason

        u = context.user_data["gp_full_name"]
        p = context.user_data["gp_project_title"]
        d = context.user_data["gp_delta"]

        reason_line = f"📝 Причина: {html.escape(reason)}\n" if reason else ""
        text = (
            f"Подтверди начисление:\n\n"
            f"🧑 {html.escape(u)}\n"
            f"📂 Проект: {html.escape(p)}\n"
            f"🏆 Баллы: {d}\n"
            f"{reason_line}\n"
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
                _apply_points(
                    get_points_service,
                    context.user_data["gp_target_telegram_id"],
                    context.user_data["gp_full_name"],
                    context.user_data["gp_project_id"],
                    context.user_data["gp_project_title"],
                    context.user_data["gp_delta"],
                    reason=context.user_data.get("gp_reason"),
                )
                await update.message.reply_text("✅ Готово!", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove())

        for k in (
            "gp_full_name",
            "gp_target_telegram_id",
            "gp_project_id",
            "gp_project_title",
            "gp_projects_map",
            "gp_targets_map",
            "gp_delta",
            "gp_reason",
        ):
            context.user_data.pop(k, None)

        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[CommandHandler("give_points", entry)],
        states={
            GP_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_user)],
            GP_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_project)],
            GP_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_points)],
            GP_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_reason)],
            GP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_confirm)],
        },
        fallbacks=[MessageHandler(filters.Regex(r"^(отмена|cancel)$"), step_confirm)],
        allow_reentry=True,
    )