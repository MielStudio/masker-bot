from sqlalchemy.orm import Session, joinedload

from database.models import Task, User, TaskAssignee, Project, WorkRole, TaskCategory, TaskChecklist
from datetime import datetime
from config import TASK_STATUSES, TASK_STATUS_TRANSITIONS


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
    
    def create_task(
        self,
        *,
        task_id: int,
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
    ) -> Task | None:
        task = Task(
            id=task_id,
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
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task
    
    def get_next_task_id(self) -> int:
        last_task = (
            self.db.query(Task)
            .order_by(Task.id.desc())
            .first()
        )
        if not last_task:
            return 1
        return last_task.id + 1
    
    def list_projects(self) -> list[Project]:
        return (
            self.db.query(Project)
            .filter(Project.is_active.is_(True))
            .order_by(Project.title.asc())
            .all()
        )

    def list_work_roles(self) -> list[WorkRole]:
        return (
            self.db.query(WorkRole)
            .order_by(WorkRole.title.asc())
            .all()
        )

    def list_task_categories(self) -> list[TaskCategory]:
        return (
            self.db.query(TaskCategory)
            .order_by(TaskCategory.title.asc())
            .all()
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

    def get_project_by_id(self, project_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id)
            .first()
        )
    
    def get_work_role_by_id(self, work_role_id: int) -> WorkRole | None:
        return (
            self.db.query(WorkRole)
            .filter(WorkRole.id == work_role_id)
            .first()
        )
    
    def get_task_category_by_id(self, category_id: int) -> TaskCategory | None:
        return (
            self.db.query(TaskCategory)
            .filter(TaskCategory.id == category_id)
            .first()
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

        if deadline:
            task.deadline_at = deadline

        # прямой переход available -> in_progress
        if task.status != "in_progress":
            task.status = "in_progress"

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

        task.deadline_at = None

        if task.status != "available":
            task.status = "available"

        self.db.commit()
        self.db.refresh(task)
        return task

    def mark_done(self, task_id: int) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        if task.status != "done":
            task.status = "done"

        self.db.commit()
        self.db.refresh(task)
        return task
    
    def set_status(self, task_id: int, new_status: str) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        task.status = new_status
        self.db.commit()
        self.db.refresh(task)
        return task
    
    def transition_status(self, task_id: int, new_status: str) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        current_status = task.status

        if new_status not in TASK_STATUSES:
            return None

        allowed = TASK_STATUS_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            return None

        task.status = new_status
        self.db.commit()
        self.db.refresh(task)
        return task
    
    def set_deadline(self, task_id: int, deadline_at: datetime | None) -> Task | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        task.deadline_at = deadline_at
        self.db.commit()
        self.db.refresh(task)
        return task
    
    def list_overdue_candidates(self, now: datetime) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
            )
            .filter(Task.deadline_at.is_not(None))
            .filter(Task.deadline_at < now)
            .filter(Task.status.in_(["available", "in_progress", "review"]))
            .all()
        )
    
    def list_checklists(self, task_id: int) -> list[TaskChecklist]:
        return (
            self.db.query(TaskChecklist)
            .filter(TaskChecklist.task_id == task_id)
            .order_by(TaskChecklist.sort_order.asc().nullslast(), TaskChecklist.id.asc())
            .all()
        )

    def get_checklist_item(self, checklist_id: int) -> TaskChecklist | None:
        return (
            self.db.query(TaskChecklist)
            .filter(TaskChecklist.id == checklist_id)
            .first()
        )

    def get_next_checklist_sort_order(self, task_id: int) -> int:
        last_item = (
            self.db.query(TaskChecklist)
            .filter(TaskChecklist.task_id == task_id)
            .order_by(TaskChecklist.sort_order.desc().nullslast(), TaskChecklist.id.desc())
            .first()
        )
        if not last_item or last_item.sort_order is None:
            return 1
        return last_item.sort_order + 1

    def add_checklist_item(self, task_id: int, title: str) -> TaskChecklist | None:
        task = self.get_by_id(task_id)
        if not task:
            return None

        item = TaskChecklist(
            task_id=task_id,
            title=title,
            is_done=False,
            sort_order=self.get_next_checklist_sort_order(task_id),
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def toggle_checklist_item(self, checklist_id: int) -> TaskChecklist | None:
        item = self.get_checklist_item(checklist_id)
        if not item:
            return None

        item.is_done = not item.is_done
        self.db.commit()
        self.db.refresh(item)
        return item

    def delete_checklist_item(self, checklist_id: int) -> bool:
        item = self.get_checklist_item(checklist_id)
        if not item:
            return False

        self.db.delete(item)
        self.db.commit()
        return True
    
    def has_open_checklist_items(self, task_id: int) -> bool:
        return (
            self.db.query(TaskChecklist)
            .filter(TaskChecklist.task_id == task_id)
            .filter(TaskChecklist.is_done.is_(False))
            .count()
            > 0
        )

    def count_checklist_items(self, task_id: int) -> int:
        return (
            self.db.query(TaskChecklist)
            .filter(TaskChecklist.task_id == task_id)
            .count()
        )
    
    def unassign_task_from_user(self, task_id: int, telegram_user_id: int) -> Task | None:
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

        link = (
            self.db.query(TaskAssignee)
            .filter(TaskAssignee.task_id == task_id)
            .filter(TaskAssignee.user_id == user.id)
            .filter(TaskAssignee.is_active.is_(True))
            .first()
        )
        if not link:
            return None

        link.is_active = False

        # Flush so the change is visible to the next query within this session
        self.db.flush()

        remaining_active_links = (
            self.db.query(TaskAssignee)
            .filter(TaskAssignee.task_id == task_id)
            .filter(TaskAssignee.is_active.is_(True))
            .all()
        )

        if not remaining_active_links:
            task.status = "available"
            task.deadline_at = None

        self.db.commit()
        self.db.refresh(task)
        return task
    
    def list_overdue_tasks(self) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
            )
            .filter(Task.status == "overdue")
            .order_by(Task.deadline_at.asc())
            .all()
        )
    
    def list_tasks_by_status(self, statuses: list[str]) -> list[Task]:
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
            )
            .filter(Task.status.in_(statuses))
            .order_by(Task.id.asc())
            .all()
        )

    def list_assigned_tasks(self) -> list[Task]:
        """Tasks that have at least one active assignee (any status except done/backlog)."""
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
            )
            .join(TaskAssignee, Task.id == TaskAssignee.task_id)
            .filter(TaskAssignee.is_active.is_(True))
            .filter(Task.status.not_in(["done", "backlog"]))
            .order_by(Task.id.asc())
            .distinct()
            .all()
        )

    def list_all_non_done_tasks(self) -> list[Task]:
        """All tasks except done/backlog — used for /block_task selection."""
        return (
            self.db.query(Task)
            .options(
                joinedload(Task.assignees).joinedload(TaskAssignee.user),
                joinedload(Task.project),
                joinedload(Task.required_work_role),
                joinedload(Task.category),
            )
            .filter(Task.status.not_in(["done", "backlog"]))
            .order_by(Task.id.asc())
            .all()
        )