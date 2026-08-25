from __future__ import annotations

from datetime import datetime

from database.models import User
from repositories.user_repository import UserRepository


class UserService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def get_user_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self.user_repo.get_by_telegram_id(telegram_user_id)

    def is_team_member(self, telegram_user_id: int) -> bool:
        return self.user_repo.is_team_member(telegram_user_id)

    def create_user(
        self,
        *,
        telegram_user_id: int,
        full_name: str | None = None,
        username: str | None = None,
        language_code: str | None = None,
        is_active: bool = True,
    ) -> User:
        return self.user_repo.create_user(
            telegram_user_id=telegram_user_id,
            full_name=full_name,
            username=username,
            language_code=language_code,
            is_active=is_active,
        )

    def update_last_idle_reminder(self, telegram_user_id: int, dt_value: datetime) -> bool:
        return self.user_repo.update_last_idle_reminder(telegram_user_id, dt_value)

    def get_permission_codes(self, telegram_user_id: int) -> list[str]:
        return self.user_repo.get_permission_codes(telegram_user_id)

    def has_permission(self, telegram_user_id: int, permission_code: str) -> bool:
        return self.user_repo.has_permission(telegram_user_id, permission_code)
    
    def get_idle_users(self, now, idle_days: int):
        return self.user_repo.list_idle_users(now, idle_days)
    
    def set_active_status(self, telegram_user_id: int, is_active: bool) -> bool:
        return self.user_repo.set_active_status(telegram_user_id, is_active)

    def get_is_active(self, telegram_user_id: int) -> bool | None:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return None
        return user.is_active

    def user_to_legacy_dict(self, user: User) -> dict:
        role_names: list[str] = []
        roles_ext: list[dict] = []

        for user_role in getattr(user, "work_roles", []):
            work_role = getattr(user_role, "work_role", None)
            if work_role:
                role_names.append(work_role.title)
                roles_ext.append(
                    {
                        "id": work_role.code,
                        "level": user_role.level,
                    }
                )

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "joined_at": user.joined_at.isoformat() if user.joined_at else None,
            "last_idle_reminder": user.last_idle_reminder_at.isoformat() if user.last_idle_reminder_at else None,
            "roles": role_names,
            "roles_ext": roles_ext,
            "permissions": self.get_permission_codes(user.telegram_user_id),
            "points": {"Общее": int(user.total_points or 0)},
            "percent_rate": {"Общее": 1.0},
            "reserved_tasks": [],
        }