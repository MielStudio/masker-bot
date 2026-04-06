from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from database.models import Task, User, Role


class TaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, task_id: int) -> Task | None:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignee),
                joinedload(Task.required_role),
            )
            .filter(Task.id == task_id)
            .first()
        )

    def list_all(self) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignee),
                joinedload(Task.required_role),
            )
            .order_by(Task.id.asc())
            .all()
        )

    def list_user_tasks(self, telegram_user_id: int) -> list[Task]:
        return (
            self.db.query(Task)
            .join(User, Task.assignee_id == User.id)
            .options(joinedload(Task.required_role))
            .filter(User.telegram_user_id == telegram_user_id)
            .filter(Task.status != "done")
            .order_by(Task.deadline.asc().nullslast(), Task.id.asc())
            .all()
        )

    def count_user_active_tasks(self, telegram_user_id: int) -> int:
        return (
            self.db.query(Task)
            .join(User, Task.assignee_id == User.id)
            .filter(User.telegram_user_id == telegram_user_id)
            .filter(Task.status.in_(["open", "in_progress", "review", "blocked"]))
            .count()
        )

    def list_available_for_user_roles(self, project: str, role_codes: list[str]) -> list[Task]:
        q = (
            self.db.query(Task)
            .outerjoin(Role, Task.required_role_id == Role.id)
            .options(joinedload(Task.required_role))
            .filter(Task.project == project)
            .filter(Task.assignee_id.is_(None))
            .filter(Task.status == "open")
        )

        if role_codes:
            normalized_codes = [c.lower() for c in role_codes if c]
            q = q.filter(
                or_(
                    Task.type_code.in_(normalized_codes),
                    Role.code.in_(normalized_codes),
                )
            )

        return q.order_by(Task.id.asc()).all()

    def assign_task(self, task_id: int, telegram_user_id: int, deadline=None) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        user = self.db.query(User).filter(User.telegram_user_id == telegram_user_id).first()
        if not user:
            return None

        task.assignee_id = user.id
        task.status = "in_progress"
        if deadline is not None:
            task.deadline = deadline

        self.db.commit()
        self.db.refresh(task)
        return task

    def unassign_task(self, task_id: int) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        task.assignee_id = None
        task.deadline = None
        task.status = "open"

        self.db.commit()
        self.db.refresh(task)
        return task

    def set_deadline(self, task_id: int, deadline) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        task.deadline = deadline
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

    def delete(self, task_id: int) -> bool:
        task = self.get_by_id(task_id)
        if not task:
            return False
        self.db.delete(task)
        self.db.commit()
        return True