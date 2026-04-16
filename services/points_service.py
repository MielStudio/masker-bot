from __future__ import annotations

from repositories.user_repository import UserRepository
from repositories.log_repository import LogRepository

class PointsService:
    def __init__(self, user_repo: UserRepository, log_repo: LogRepository | None = None):
        self.user_repo = user_repo
        self.log_repo = log_repo

    def get_user_points_summary(self, telegram_user_id: int) -> dict | None:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return None

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": {
                "Общее": {
                    "points": int(user.total_points or 0),
                    "percent_rate": 1.0,
                }
            },
        }

    def get_user_points_summary_by_username(self, username: str) -> dict | None:
        user = self.user_repo.get_by_username(username)
        if not user:
            return None

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": {
                "Общее": {
                    "points": int(user.total_points or 0),
                    "percent_rate": 1.0,
                }
            },
        }

    def add_points(
        self,
        telegram_user_id: int,
        points_to_add: int,
        project_id: int = 1,
        project_name: str = "Общее",
        reason: str | None = None,
        task_id: int | None = None,
        source_type: str = "manual",
        created_by_user_id: int | None = None,
        j_value: float | None = None,
        c_value: float | None = None,
        t_value: float | None = None,
        k_value: float | None = None,
    ) -> bool:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return False

        current = int(user.total_points or 0)
        user.total_points = current + int(points_to_add)
        self.user_repo.db.commit()

        if self.log_repo:
            self.log_repo.add_points_ledger(
                user_id=user.id,
                project_id=project_id,
                amount=points_to_add,
                source_type=source_type,
                reason=reason,
                task_id=task_id,
                created_by_user_id=created_by_user_id,
                j_value=j_value,
                c_value=c_value,
                t_value=t_value,
                k_value=k_value,
            )

        return True

    def recalculate_percent_rates(self) -> None:
        # Для MVP ничего не делаем.
        return