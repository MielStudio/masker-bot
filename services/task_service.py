from __future__ import annotations

from repositories.task_repository import TaskRepository


class TaskService:
    def __init__(self, task_repo: TaskRepository):
        self.task_repo = task_repo

    def get_user_tasks(self, telegram_user_id: int):
        return self.task_repo.list_user_tasks(telegram_user_id)

    def count_user_active_tasks(self, telegram_user_id: int) -> int:
        return self.task_repo.count_user_active_tasks(telegram_user_id)

    def task_to_legacy_dict(self, task) -> dict:
        """
        Временный адаптер под старый bot.py.
        Возвращает dict, похожий на старую запись из tasks.json.
        """
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