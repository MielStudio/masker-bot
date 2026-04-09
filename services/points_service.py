from __future__ import annotations

from repositories.user_repository import UserRepository


class PointsService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

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

    def add_points(self, telegram_user_id: int, points_to_add: int, project_name: str = "Общее") -> bool:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return False

        current = int(user.total_points or 0)
        user.total_points = current + int(points_to_add)
        self.user_repo.db.commit()
        return True

    def recalculate_percent_rates(self) -> None:
        # Для MVP ничего не делаем.
        return