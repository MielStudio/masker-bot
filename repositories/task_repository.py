from sqlalchemy.orm import Session, joinedload

from database.models import Task, User, TaskAssignee
from datetime import datetime


class TaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, task_id: int) -> Task | None:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
            )
            .filter(Task.id == task_id)
            .first()
        )

    def list_user_tasks(self, telegram_user_id: int) -> list[Task]:
        return (
            self.db.query(Task)
            .join(TaskAssignee, Task.id == TaskAssignee.task_id)
            .join(User, TaskAssignee.user_id == User.id)
            .filter(User.telegram_user_id == telegram_user_id)
            .filter(TaskAssignee.is_active.is_(True))
            .filter(Task.status != "done")
            .order_by(Task.deadline_at.asc().nullslast(), Task.id.asc())
            .all()
        )

    def count_user_active_tasks(self, telegram_user_id: int) -> int:
        return (
            self.db.query(Task)
            .join(TaskAssignee, Task.id == TaskAssignee.task_id)
            .join(User, TaskAssignee.user_id == User.id)
            .filter(User.telegram_user_id == telegram_user_id)
            .filter(TaskAssignee.is_active.is_(True))
            .filter(Task.status.in_(["open", "in_progress", "review", "blocked"]))
            .count()
        )

    def list_available_tasks(self) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
            )
            .filter(Task.status == "available")
            .order_by(Task.id.asc())
            .all()
        )
    
    def list_available_tasks_paginated(self, offset: int = 0, limit: int = 5) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
            )
            .filter(Task.status == "available")
            .order_by(Task.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    
    def count_available_tasks(self) -> int:
        return (
            self.db.query(Task)
            .filter(Task.status == "available")
            .count()
        )

    def assign_task(self, task_id: int, telegram_user_id: int, deadline=None) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        user = (
            self.db.query(User)
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
        )
        if not user:
            return None

        # ищем уже существующую связь task-user
        existing_link = (
            self.db.query(TaskAssignee)
            .filter(TaskAssignee.task_id == task.id)
            .filter(TaskAssignee.user_id == user.id)
            .first()
        )

        if existing_link:
            # если связь уже есть, просто реактивируем её
            existing_link.is_active = True
            if getattr(existing_link, "assigned_at", None) is None:
                existing_link.assigned_at = datetime.utcnow()
        else:
            # иначе создаём новую
            link = TaskAssignee(
                task_id=task.id,
                user_id=user.id,
                is_active=True,
                assigned_at=datetime.utcnow(),
            )
            self.db.add(link)

        task.status = "in_progress"

        if deadline:
            task.deadline_at = deadline

        self.db.commit()
        self.db.refresh(task)
        return task

    def unassign_task(self, task_id: int) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        links = (
            self.db.query(TaskAssignee)
            .filter(TaskAssignee.task_id == task_id)
            .all()
        )

        for link in links:
            link.is_active = False

        task.status = "available"
        task.deadline_at = None

        self.db.commit()
        self.db.refresh(task)
        return task

    def mark_done(self, task_id: int) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        task.status = "done"
        self.db.commit()
        self.db.refresh(task)
        return task