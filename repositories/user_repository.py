from datetime import datetime, timedelta
from sqlalchemy import text, or_
from sqlalchemy.orm import Session

from database.models import User, TaskAssignee


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, db_user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == db_user_id).first()

    def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return (
            self.db.query(User)
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
        )

    def get_by_username(self, username: str) -> User | None:
        username = username.lstrip("@").strip().lower()
        return (
            self.db.query(User)
            .filter(User.username.is_not(None))
            .filter(User.username.ilike(username))
            .first()
        )

    def get_by_full_name(self, full_name: str) -> User | None:
        full_name = (full_name or "").strip()
        if not full_name:
            return None
        return (
            self.db.query(User)
            .filter(User.full_name.is_not(None))
            .filter(User.full_name.ilike(full_name))
            .first()
        )

    def list_all(self) -> list[User]:
        return self.db.query(User).all()

    def list_active_team_members(self) -> list[User]:
        return (
            self.db.query(User)
            .filter(User.is_active.is_(True))
            .order_by(User.id.asc())
            .all()
        )

    def is_team_member(self, telegram_user_id: int) -> bool:
        return (
            self.db.query(User.id)
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
            is not None
        )

    def update_last_idle_reminder(self, telegram_user_id: int, dt_value) -> bool:
        user = self.get_by_telegram_id(telegram_user_id)
        if not user:
            return False

        user.last_idle_reminder_at = dt_value
        self.db.commit()
        return True

    def get_permission_codes(self, telegram_user_id: int) -> list[str]:
        user = self.get_by_telegram_id(telegram_user_id)
        if not user:
            return []

        rows = self.db.execute(
            text("""
                SELECT p.code
                FROM user_permissions up
                JOIN permissions p ON p.id = up.permission_id
                WHERE up.user_id = :user_id
                ORDER BY p.code
            """),
            {"user_id": user.id},
        ).fetchall()

        return [row[0] for row in rows]

    def has_permission(self, telegram_user_id: int, permission_code: str) -> bool:
        return permission_code in self.get_permission_codes(telegram_user_id)
    
    def list_idle_users(self, now: datetime, idle_days: int) -> list[User]:
        """Активные участники без активных задач, которым пора напомнить."""
        threshold = now - timedelta(days=idle_days)

        active_assignee_subq = (
            self.db.query(TaskAssignee.user_id)
            .filter(TaskAssignee.is_active.is_(True))
            .subquery()
        )

        return (
            self.db.query(User)
            .filter(User.is_active.is_(True))
            .filter(~User.id.in_(active_assignee_subq))
            .filter(
                or_(
                    User.last_idle_reminder_at.is_(None),
                    User.last_idle_reminder_at <= threshold,
                )
            )
            .all()
        )