# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Callable, List, Dict, Any
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler, ContextTypes, filters
import html
import json

# Состояния мастера
AT_PROJECT, AT_TITLE, AT_DESC, AT_TYPE, AT_POINTS, AT_EST, AT_CONFIRM = range(700, 707)

def _collect_projects(load_json: Callable[[str], list], tasks_file: str, users_file: str) -> List[str]:
    """Берём проекты из tasks.json и из percent_rate в users.json, плюс дефолты."""
    seen = set()
    projects = []

    # из задач
    for t in load_json(tasks_file):
        p = (t.get("project") or "").strip()
        if p and p not in seen:
            seen.add(p); projects.append(p)

    # из users.json
    for u in load_json(users_file):
        for p in (u.get("percent_rate") or {}).keys():
            if p and p not in seen:
                seen.add(p); projects.append(p)

    # запасной вариант
    if not projects:
        projects = ["Starky Jungle", "Ideal Abyss", "Unsouled", "Non-project work"]
    return projects

def _collect_types(load_json: Callable[[str], list], tasks_file: str) -> List[str]:
    """Типы (роли) из существующих задач + популярные пресеты."""
    seen = set()
    types = []
    for t in load_json(tasks_file):
        tp = (t.get("type") or "").strip()
        if tp and tp not in seen:
            seen.add(tp); types.append(tp)
    presets = ["программист", "геймдизайн", "анимация", "3D-арт", "2D-арт", "тестирование", "документация"]
    for p in presets:
        if p not in seen:
            types.append(p)
    return types

def _new_task_id(load_json, tasks_file: str) -> int:
    tasks = load_json(tasks_file)
    return (max([t.get("id", 0) for t in tasks], default=0) + 1)

def build_add_task_handler(
    *,
    tasks_file: str,
    users_file: str,
    load_json: Callable[[str], list],
    save_json: Callable[[str, list], None],
    admin_id: int,
):
    """Возвращает ConversationHandler для /add_task с поддержкой старого «;» синтаксиса."""

    # ===== entry =====
    async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != admin_id:
            if update.message:
                await update.message.reply_text("❌ Ты слишком слаб чтобы использовать это заклинание")
            return ConversationHandler.END

        # Быстрый старый формат: /add_task p;title;desc;type;points;days
        if context.args:
            try:
                raw = " ".join(context.args)
                parts = [x.strip() for x in raw.split(";")]
                if len(parts) < 6:
                    raise ValueError("Недостаточно параметров")
                project, title, desc, typ, points, days = parts[:6]
                points = int(points); days = int(days)
                tasks = load_json(tasks_file)
                tasks.append({
                    "id": _new_task_id(load_json, tasks_file),
                    "project": project,
                    "title": title,
                    "description": desc,
                    "type": typ,
                    "points": points,
                    "estimated_days": days,
                    "deadline": None,
                    "reserved_by": None,
                })
                save_json(tasks_file, tasks)
                await update.message.reply_text(
                    f"✅ Задача добавлена:\n\n"
                    f"<b>{html.escape(title)}</b>\n"
                    f"Проект: {html.escape(project)}\n"
                    f"Роль: {html.escape(typ)}\n"
                    f"Баллы: {points}\n"
                    f"Оценка: {days} дн.",
                    parse_mode="HTML",
                )
                return ConversationHandler.END
            except Exception as e:
                await update.message.reply_text(f"⚠️ {e}\nЗапускаю мастер добавления…")

        # Мастер: шаг 1 — проект
        projects = _collect_projects(load_json, tasks_file, users_file)
        rows = [[p] for p in projects[:20]]
        rows.append(["✏️ Ввести проект вручную"])
        kb = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("📁 Выбери проект (или нажми «✏️ Ввести проект вручную»):", reply_markup=kb)
        return AT_PROJECT

    async def at_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        text = update.message.text.strip()
        if text == "✏️ Ввести проект вручную":
            await update.message.reply_text("Введи название проекта текстом:", reply_markup=ReplyKeyboardRemove())
            return AT_PROJECT
        context.user_data["t_project"] = text
        await update.message.reply_text("🧠 Введи <b>название задачи</b>:", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return AT_TITLE

    async def at_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        context.user_data["t_title"] = update.message.text.strip()
        kb = ReplyKeyboardMarkup([["Пропустить"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("📝 Введи <b>описание</b> (или нажми «Пропустить»):", parse_mode="HTML", reply_markup=kb)
        return AT_DESC

    async def at_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
        desc = ""
        if update.message and update.message.text and update.message.text.strip().lower() != "пропустить":
            desc = update.message.text.strip()
        context.user_data["t_desc"] = desc

        types = _collect_types(load_json, tasks_file)
        rows = [types[i:i+2] for i in range(0, min(len(types), 10), 2)]
        rows.append(["✏️ Другая роль"])
        kb = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("👤 Выбери <b>роль/тип</b> задачи (или «✏️ Другая роль»):", parse_mode="HTML", reply_markup=kb)
        return AT_TYPE

    async def at_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        text = update.message.text.strip()
        if text == "✏️ Другая роль":
            await update.message.reply_text("Введи название роли/типа текстом:", reply_markup=ReplyKeyboardRemove())
            return AT_TYPE
        context.user_data["t_type"] = text

        kb = ReplyKeyboardMarkup([["5", "10", "15"], ["20", "30", "50"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("🏆 Сколько <b>баллов</b> поставить? (можешь ввести своё число)", parse_mode="HTML", reply_markup=kb)
        return AT_POINTS

    async def at_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        try:
            context.user_data["t_points"] = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Число не распознано. Пример: 20")
            return AT_POINTS

        kb = ReplyKeyboardMarkup([["1", "3", "5"], ["7", "14", "30"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("⏳ Примерная длительность в <b>днях</b>? (можешь ввести своё число)", parse_mode="HTML", reply_markup=kb)
        return AT_EST

    async def at_est(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        try:
            context.user_data["t_days"] = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Число не распознано. Пример: 7")
            return AT_EST

        p = html.escape(context.user_data["t_project"])
        t = html.escape(context.user_data["t_title"])
        d = html.escape(context.user_data["t_desc"] or "—")
        r = html.escape(context.user_data["t_type"])
        pts = context.user_data["t_points"]
        days = context.user_data["t_days"]

        text = (f"Проверим карточку задачи:\n\n"
                f"📁 Проект: <b>{p}</b>\n"
                f"🧠 Название: <b>{t}</b>\n"
                f"📝 Описание: {d}\n"
                f"👤 Роль: <b>{r}</b>\n"
                f"🏆 Баллы: <b>{pts}</b>\n"
                f"⏳ Оценка: <b>{days} дн.</b>\n\n"
                f"Создать?")
        kb = ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return AT_CONFIRM

    async def at_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return ConversationHandler.END
        if update.message.text.lower().startswith("д"):
            tasks = load_json(tasks_file)
            tasks.append({
                "id": _new_task_id(load_json, tasks_file),
                "project": context.user_data["t_project"],
                "title": context.user_data["t_title"],
                "description": context.user_data["t_desc"],
                "type": context.user_data["t_type"],
                "points": context.user_data["t_points"],
                "estimated_days": context.user_data["t_days"],
                "deadline": None,
                "reserved_by": None,
            })
            save_json(tasks_file, tasks)
            await update.message.reply_text("✅ Задача создана.", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Отменено.", reply_markup=ReplyKeyboardRemove())

        # очистка
        for k in ("t_project","t_title","t_desc","t_type","t_points","t_days"):
            context.user_data.pop(k, None)
        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[CommandHandler("add_task", entry)],
        states={
            AT_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, at_project)],
            AT_TITLE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, at_title)],
            AT_DESC:    [MessageHandler(filters.TEXT & ~filters.COMMAND, at_desc)],
            AT_TYPE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, at_type)],
            AT_POINTS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, at_points)],
            AT_EST:     [MessageHandler(filters.TEXT & ~filters.COMMAND, at_est)],
            AT_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, at_confirm)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
