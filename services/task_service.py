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
        type_value = task.type_code
        if not type_value and task.required_role:
            type_value = task.required_role.title

        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "type": type_value,
            "project": task.project,
            "points": task.points,
            "estimated_days": task.estimated_days,
            "reserved_by": task.assignee.telegram_user_id if task.assignee else None,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "status": task.status,
        }

    def get_available_tasks_for_user(self, project: str, user_record: dict) -> list[dict]:
        role_codes: list[str] = []

        for item in user_record.get("roles_ext", []) or []:
            role_id = item.get("id")
            if role_id:
                role_codes.append(role_id)

        tasks = self.task_repo.list_available_for_user_roles(project, role_codes)
        return [self.task_to_legacy_dict(t) for t in tasks]

    def assign_task_with_auto_deadline(self, task_id: int, telegram_user_id: int, work_tz):
        task = self.task_repo.get_by_id(task_id)
        if not task:
            return None

        deadline = task.deadline
        if deadline is None:
            deadline = datetime.now(work_tz) + timedelta(days=task.estimated_days or 7)

        return self.task_repo.assign_task(task_id, telegram_user_id, deadline=deadline)

    def assign_task_to_user(self, task_id: int, telegram_user_id: int, work_tz):
        task = self.task_repo.get_by_id(task_id)
        if not task:
            return None
        if task.assignee_id is not None:
            return None

        deadline = task.deadline
        if deadline is None:
            deadline = datetime.now(work_tz) + timedelta(days=task.estimated_days or 7)

        return self.task_repo.assign_task(task_id, telegram_user_id, deadline=deadline)

    def get_task_by_id(self, task_id: int):
        return self.task_repo.get_by_id(task_id)

    def unassign_task(self, task_id: int):
        return self.task_repo.unassign_task(task_id)

    def set_deadline(self, task_id: int, deadline: datetime):
        return self.task_repo.set_deadline(task_id, deadline)

    def mark_done(self, task_id: int):
        return self.task_repo.mark_done(task_id)

    def list_all_tasks(self):
        return self.task_repo.list_all()