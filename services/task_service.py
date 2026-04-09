from __future__ import annotations

from datetime import datetime, timedelta

from repositories.task_repository import TaskRepository


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
        active_links = [a for a in getattr(task, "assignees", []) if getattr(a, "is_active", False)]
        if active_links:
            user = getattr(active_links[0], "user", None)
            if user is not None:
                reserved_by = user.telegram_user_id

        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "type": type_value,
            "project": project_value,
            "points": 0,  # если позже захочешь, можно считать из J/C/T/K или отдельной формулы
            "estimated_days": task.estimated_days,
            "reserved_by": reserved_by,
            "deadline": task.deadline_at.isoformat() if task.deadline_at else None,
            "status": task.status,
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

    def unassign_task(self, task_id: int):
        return self.task_repo.unassign_task(task_id)

    def mark_done(self, task_id: int):
        return self.task_repo.mark_done(task_id)