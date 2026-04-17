from __future__ import annotations
from datetime import datetime, timedelta
from repositories.task_repository import TaskRepository
from config import (
    TASK_STATUS_LABELS,
    TASK_STATUS_RU,
    PRIORITY_LABELS,
    PRIORITY_MULTIPLIERS,
)


class TaskService:
    def __init__(self, task_repo: TaskRepository):
        self.task_repo = task_repo

    def get_user_tasks(self, telegram_user_id: int):
        return self.task_repo.list_user_tasks(telegram_user_id)

    def count_user_active_tasks(self, telegram_user_id: int) -> int:
        return self.task_repo.count_user_active_tasks(telegram_user_id)

    def task_to_legacy_dict(self, task) -> dict:
        type_value = None
        if getattr(task, "required_work_role", None):
            type_value = task.required_work_role.title
        elif getattr(task, "category", None):
            type_value = task.category.title

        project_value = None
        if getattr(task, "project", None):
            project_value = task.project.title

        reserved_by = None
        assignees = []

        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        for link in active_links:
            user = getattr(link, "user", None)
            if user is None:
                continue

            if reserved_by is None:
                reserved_by = user.telegram_user_id

            assignees.append({
                "id": user.id,
                "telegram_user_id": user.telegram_user_id,
                "username": user.username,
                "full_name": user.full_name,
            })
        
        points = self.calculate_task_points(task)
        for attr in ("j_value", "c_value", "t_value"):
            value = getattr(task, attr, None)
            if value:
                points += value

        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "type": type_value,
            "project": project_value,
            "priority": task.priority,
            "priority_label": self.format_priority_label(task.priority),
            "points": points,
            "estimated_days": task.estimated_days,
            "reserved_by": reserved_by,
            "deadline": task.deadline_at.isoformat() if task.deadline_at else None,
            "status": task.status,
            "assignees": assignees,
            "assignees_count": len(assignees),
            "max_assignees": task.max_assignees,
        }

    def get_available_tasks_for_user(self, project: str, user_record: dict) -> list[dict]:
        role_codes: list[str] = []

        for item in user_record.get("roles_ext", []) or []:
            role_id = item.get("id")
            if role_id:
                role_codes.append(role_id)

        tasks = self.task_repo.list_available_tasks()

        filtered = []
        for task in tasks:
            task_project_title = task.project.title if getattr(task, "project", None) else None
            if project and task_project_title != project:
                continue

            if role_codes:
                task_role_code = None
                if getattr(task, "required_work_role", None):
                    task_role_code = task.required_work_role.code

                if task_role_code and task_role_code not in role_codes:
                    continue

            filtered.append(task)

        return [self.task_to_legacy_dict(t) for t in filtered]

    def assign_task_with_auto_deadline(self, task_id: int, telegram_user_id: int, work_tz):
        task = self.task_repo.get_by_id(task_id)
        if not task:
            return None

        deadline = task.deadline_at
        if deadline is None:
            deadline = datetime.now(work_tz) + timedelta(days=task.estimated_days or 7)

        return self.task_repo.assign_task(task_id, telegram_user_id, deadline=deadline)

    def assign_task_to_user(self, task_id: int, telegram_user_id: int, work_tz):
        task = self.task_repo.get_by_id(task_id)
        if not task:
            return None

        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        if len(active_links) >= (task.max_assignees or 1):
            return None

        deadline = task.deadline_at
        if deadline is None:
            deadline = datetime.now(work_tz) + timedelta(days=task.estimated_days or 7)

        return self.task_repo.assign_task(task_id, telegram_user_id, deadline=deadline)

    def get_task_by_id(self, task_id: int):
        return self.task_repo.get_by_id(task_id)
    
    def list_projects(self):
        return self.task_repo.list_projects()

    def list_work_roles(self):
        return self.task_repo.list_work_roles()

    def list_task_categories(self):
        return self.task_repo.list_task_categories()

    def get_project_by_id(self, project_id: int):
        return self.task_repo.get_project_by_id(project_id)

    def get_work_role_by_id(self, work_role_id: int):
        return self.task_repo.get_work_role_by_id(work_role_id)

    def get_task_category_by_id(self, category_id: int):
        return self.task_repo.get_task_category_by_id(category_id)
    
    def list_projects_for_ui(self) -> list[dict]:
        projects = self.task_repo.list_projects()
        return [
            {
                "id": p.id,
                "code": p.code,
                "title": p.title,
            }
            for p in projects
        ]
    
    def list_work_roles_for_ui(self) -> list[dict]:
        roles = self.task_repo.list_work_roles()
        return [
            {
                "id": r.id,
                "code": r.code,
                "title": r.title,
                "emoji": r.emoji,
            }
            for r in roles
        ]
    
    def list_task_categories_for_ui(self) -> list[dict]:
        categories = self.task_repo.list_task_categories()
        return [
            {
                "id": c.id,
                "code": c.code,
                "title": c.title,
            }
            for c in categories
        ]
    
    def get_next_task_id(self) -> int:
        return self.task_repo.get_next_task_id()
    
    def create_task(
        self,
        *,
        project_id: int,
        title: str,
        description: str | None = None,
        category_id: int | None = None,
        required_work_role_id: int | None = None,
        priority: str = "medium",
        status: str = "available",
        max_assignees: int = 1,
        estimated_days: int | None = 7,
        review_required: bool = True,
        j_value: int | None = 0,
        c_value: int | None = 0,
        t_value: int | None = 0,
        created_by_user_id: int | None = None,
    ):
        task_id = self.task_repo.get_next_task_id()

        return self.task_repo.create_task(
            task_id=task_id,
            project_id=project_id,
            title=title,
            description=description,
            category_id=category_id,
            required_work_role_id=required_work_role_id,
            priority=priority,
            status=status,
            max_assignees=max_assignees,
            estimated_days=estimated_days,
            review_required=review_required,
            j_value=j_value,
            c_value=c_value,
            t_value=t_value,
            created_by_user_id=created_by_user_id,
        )

    def unassign_task(self, task_id: int):
        return self.task_repo.unassign_task(task_id)

    def mark_done(self, task_id: int):
        return self.task_repo.mark_done(task_id)
    
    def set_status(self, task_id: int, new_status: str):
        return self.task_repo.set_status(task_id, new_status)

    def transition_status(self, task_id: int, new_status: str):
        return self.task_repo.transition_status(task_id, new_status)
    
    def submit_for_review(self, task_id: int):
        return self.task_repo.transition_status(task_id, "review")

    def approve_task(self, task_id: int):
        return self.task_repo.transition_status(task_id, "done")

    def return_from_review(self, task_id: int):
        return self.task_repo.transition_status(task_id, "in_progress")
    
    def block_task(self, task_id: int):
        return self.task_repo.transition_status(task_id, "blocked")

    def unblock_task(self, task_id: int, target_status: str = "available"):
        return self.task_repo.transition_status(task_id, target_status)
    
    def can_user_submit_task(self, task, telegram_user_id: int) -> bool:
        if not task:
            return False

        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        for link in active_links:
            user = getattr(link, "user", None)
            if user and user.telegram_user_id == telegram_user_id:
                return True

        return False
    
    def format_task_card(self, task_dict: dict) -> str:
        description = (task_dict.get("description") or "").strip()
        if len(description) > 180:
            description = description[:177] + "..."

        est = task_dict.get("estimated_days")
        est_text = f"{est} дн." if est else "не указано"
        status_text = self.format_status_label(task_dict.get("status"))
        priority_text = task_dict.get("priority_label") or self.format_priority_label(task_dict.get("priority"))

        return (
            f"🧩 <b>{task_dict['title']}</b>\n"
            f"📌 Статус: {status_text}\n"
            f"⚡ Приоритет: {priority_text}\n"
            f"📁 Проект: {task_dict.get('project') or '—'}\n"
            f"👤 Роль: {task_dict.get('type') or '—'}\n"
            f"🏆 Баллы: {task_dict.get('points', 0)}\n"
            f"⏳ Оценка: {est_text}\n"
            f"🆔 ID: #{task_dict['id']}\n"
            f"📝 {description or 'Без описания'}"
        )
    
    def get_available_tasks_for_user_paginated(
        self,
        project: str,
        user_record: dict,
        page: int = 1,
        per_page: int = 5,
    ):
        role_codes: list[str] = []

        for item in user_record.get("roles_ext", []) or []:
            role_id = item.get("id")
            if role_id:
                role_codes.append(role_id)

        tasks = self.task_repo.list_available_tasks()

        filtered = []
        for task in tasks:
            task_project_title = task.project.title if getattr(task, "project", None) else None
            if project and task_project_title != project:
                continue

            if role_codes:
                task_role_code = None
                if getattr(task, "required_work_role", None):
                    task_role_code = task.required_work_role.code

                if task_role_code and task_role_code not in role_codes:
                    continue

            filtered.append(task)

        total = len(filtered)
        start = (page - 1) * per_page
        end = start + per_page
        page_items = filtered[start:end]

        return {
            "items": [self.task_to_legacy_dict(t) for t in page_items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_prev": page > 1,
            "has_next": end < total,
        }
    
    def format_status_label(self, status: str | None) -> str:
        if not status:
            return "⚪ Неизвестно"

        emoji, _ = TASK_STATUS_LABELS.get(status, ("⚪", status))
        title = TASK_STATUS_RU.get(status, status)
        return f"{emoji} {title}"
    
    def set_deadline(self, task_id: int, deadline_at: datetime | None):
        return self.task_repo.set_deadline(task_id, deadline_at)
    
    def mark_overdue_tasks(self, now: datetime):
        tasks = self.task_repo.list_overdue_candidates(now)
        updated = []

        for task in tasks:
            changed = self.task_repo.transition_status(task.id, "overdue")
            if changed:
                updated.append(changed)

        return updated
    
    def list_checklists(self, task_id: int):
        return self.task_repo.list_checklists(task_id)

    def add_checklist_item(self, task_id: int, title: str):
        if not title or not title.strip():
            return None
        return self.task_repo.add_checklist_item(task_id, title.strip())

    def toggle_checklist_item(self, checklist_id: int):
        return self.task_repo.toggle_checklist_item(checklist_id)

    def delete_checklist_item(self, checklist_id: int):
        return self.task_repo.delete_checklist_item(checklist_id)
    
    def format_checklist(self, items) -> str:
        if not items:
            return "—"

        lines = []
        for item in items:
            mark = "✅" if item.is_done else "⬜"
            lines.append(f"{mark} [{item.id}] {item.title}")
        return "\n".join(lines)
    
    def has_open_checklist_items(self, task_id: int) -> bool:
        return self.task_repo.has_open_checklist_items(task_id)

    def count_checklist_items(self, task_id: int) -> int:
        return self.task_repo.count_checklist_items(task_id)
    
    def can_submit_task_to_review(self, task_id: int) -> tuple[bool, str | None]:
        total_items = self.task_repo.count_checklist_items(task_id)
        has_open = self.task_repo.has_open_checklist_items(task_id)

        if total_items == 0:
            return True, None

        if has_open:
            return False, "У задачи есть незавершённые пункты чеклиста."

        return True, None
    
    def get_checklist_text_for_task(self, task_id: int) -> str:
        items = self.task_repo.list_checklists(task_id)
        if not items:
            return ""

        return self.format_checklist(items)
    
    def unassign_task_from_user(self, task_id: int, telegram_user_id: int):
        return self.task_repo.unassign_task_from_user(task_id, telegram_user_id)
    
    def format_priority_label(self, priority: str | None) -> str:
        if not priority:
            return "⚪ Неизвестный"
        return PRIORITY_LABELS.get(priority, priority)

    def get_priority_multiplier(self, priority: str | None) -> float:
        if not priority:
            return 1.0
        return PRIORITY_MULTIPLIERS.get(priority, 1.0)

    def calculate_task_points(self, task) -> int:
        j = int(getattr(task, "j_value", 0) or 0)
        c = int(getattr(task, "c_value", 0) or 0)
        t = int(getattr(task, "t_value", 0) or 0)

        base_points = j + c + t
        multiplier = self.get_priority_multiplier(getattr(task, "priority", None))

        return max(0, round(base_points * multiplier))
    
    def get_all_overdue_tasks(self):
        return self.task_repo.list_overdue_tasks()
    
    def get_submittable_tasks_for_user(self, telegram_user_id: int):
        tasks = self.task_repo.list_user_tasks(telegram_user_id)
        return [t for t in tasks if t.status == "in_progress"]
    
    def get_submittable_tasks_for_admin(self):
        return self.task_repo.list_tasks_by_status(["in_progress"])
