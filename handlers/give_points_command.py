# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable, List

import html
import shlex

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters

from services.points_service import PointsService


GP_USER, GP_PROJECT, GP_POINTS, GP_CONFIRM = range(200, 204)


def _all_usernames(get_points_service: Callable[[], PointsService]) -> List[str]:
    points_service = get_points_service()
    users = points_service.user_repo.list_all()

    out = []
    for u in users:
        name = (u.username or "").strip()
        if name:
            out.append(name)

    return sorted(out, key=str.lower)


def _all_projects(get_points_service: Callable[[], PointsService]) -> List[str]:
        return ["Общее"]

def _apply_points(
    get_points_service: Callable[[], PointsService],
    username: str,
    project: str,
    delta: int,
) -> None:
    points_service = get_points_service()
    summary = points_service.get_user_points_summary_by_username(username)
    if not summary:
        raise ValueError(f"Пользователь @{username} не найден")

    telegram_user_id = summary["user_id"]

    ok = points_service.add_points(
        telegram_user_id=telegram_user_id,
        points_to_add=int(delta),
        project_id=1,
        project_name=project,
        reason="Ручное начисление админом",
        source_type="manual",
    )

    if not ok:
        raise ValueError(f"Не удалось начислить баллы пользователю @{username}")


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

                username = args[0].lstrip("@")
                project = args[1]
                delta = int(args[2])

                _apply_points(get_points_service, username, project, delta)

                await update.message.reply_text(
                    f"✅ Добавлено {delta} балл(ов) пользователю @{username} по «{project}»."
                )
                return ConversationHandler.END

            except Exception as e:
                await update.message.reply_text(f"⚠️ {e}\nЗапускаю мастер добавления баллов…")

        usernames = _all_usernames(get_points_service)
        kb = ReplyKeyboardMarkup([[f"@{u}"] for u in usernames], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Кому начислим баллы? Выбери @username:", reply_markup=kb)
        return GP_USER

    async def step_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        context.user_data["gp_username"] = update.message.text.lstrip("@").strip()

        projects = _all_projects(get_points_service)
        rows = [[p] for p in projects[:20]]
        rows.append(["✏️ Ввести проект вручную"])

        await update.message.reply_text(
            "Выбери проект (или нажми «✏️ Ввести проект вручную»):",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
        )
        return GP_PROJECT

    async def step_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END

        text = update.message.text.strip()
        if text == "✏️ Ввести проект вручную":
            await update.message.reply_text("Введи точное имя проекта:", reply_markup=ReplyKeyboardRemove())
            return GP_PROJECT

        context.user_data["gp_project"] = text
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

        u = context.user_data["gp_username"]
        p = context.user_data["gp_project"]
        d = context.user_data["gp_delta"]

        text = (
            f"Подтверди начисление:\n\n"
            f"🧑 @{u}\n"
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
                _apply_points(
                    get_points_service,
                    context.user_data["gp_username"],
                    context.user_data["gp_project"],
                    context.user_data["gp_delta"],
                )
                await update.message.reply_text("✅ Готово!", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove())

        for k in ("gp_username", "gp_project", "gp_delta"):
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