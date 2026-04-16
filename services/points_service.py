from __future__ import annotations

from repositories.user_repository import UserRepository
from repositories.log_repository import LogRepository


class PointsService:
    def __init__(self, user_repo: UserRepository, log_repo: LogRepository | None = None):
        self.user_repo = user_repo
        self.log_repo = log_repo

    def _build_project_summary(self, user) -> dict:
        projects: dict[str, dict] = {}

        for entry in getattr(user, "ledger_entries", []) or []:
            project_title = "Без проекта"
            if getattr(entry, "project", None) and getattr(entry.project, "title", None):
                project_title = entry.project.title

            if project_title not in projects:
                projects[project_title] = {
                    "points": 0,
                    "percent_rate": 0.0,
                }

            projects[project_title]["points"] += int(entry.amount or 0)

        total_points = sum(item["points"] for item in projects.values())

        if total_points > 0:
            for item in projects.values():
                item["percent_rate"] = item["points"] / total_points
        else:
            for item in projects.values():
                item["percent_rate"] = 0.0

        return projects

    def get_user_points_summary(self, telegram_user_id: int) -> dict | None:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return None

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": self._build_project_summary(user),
        }

    def get_user_points_summary_by_username(self, username: str) -> dict | None:
        user = self.user_repo.get_by_username(username)
        if not user:
            return None

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": self._build_project_summary(user),
        }

    def add_points(
        self,
        telegram_user_id: int,
        points_to_add: int,
        project_id: int,
        project_name: str | None = None,
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
        return