from sqlalchemy.orm import Session, joinedload

from database.models import Task, User, TaskAssignee


class TaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, task_id: int) -> Task | None:
        return (
            self.db.query(Task)
            .options(joinedload(Task.assignees))
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
            .filter(Task.status == "available")
            .order_by(Task.id.asc())
            .all()
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

        # создаём связь
        link = TaskAssignee(
            task_id=task.id,
            user_id=user.id,
            is_active=True,
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