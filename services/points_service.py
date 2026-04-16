from __future__ import annotations

from repositories.user_repository import UserRepository
from repositories.log_repository import LogRepository
from database.models import PointsLedger, Project


class PointsService:
    def __init__(self, user_repo: UserRepository, log_repo: LogRepository | None = None):
        self.user_repo = user_repo
        self.log_repo = log_repo

    def _build_project_summary(self, user) -> dict:
        db = self.user_repo.db
        projects: dict[int, dict] = {}

        # 1. Считаем баллы пользователя по каждому project_id
        for entry in getattr(user, "ledger_entries", []) or []:
            project_id = getattr(entry, "project_id", None)
            if not project_id:
                continue

            project_title = "Без проекта"
            if getattr(entry, "project", None) and getattr(entry.project, "title", None):
                project_title = entry.project.title
            else:
                project_row = db.query(Project).filter(Project.id == project_id).first()
                if project_row and project_row.title:
                    project_title = project_row.title

            if project_id not in projects:
                projects[project_id] = {
                    "project_id": project_id,
                    "project_title": project_title,
                    "points": 0,
                    "percent_rate": 0.0,
                }

            projects[project_id]["points"] += int(entry.amount or 0)

        # 2. Для каждого проекта считаем общий пул баллов всех участников проекта
        for project_id, item in projects.items():
            total_project_points = (
                db.query(PointsLedger)
                .filter(PointsLedger.project_id == project_id)
                .with_entities(PointsLedger.amount)
                .all()
            )

            total_project_sum = sum(int(row[0] or 0) for row in total_project_points)

            if total_project_sum > 0:
                item["percent_rate"] = item["points"] / total_project_sum
            else:
                item["percent_rate"] = 0.0

        # 3. Возвращаем словарь по названию проекта, как ждёт bot.py
        result: dict[str, dict] = {}
        for item in projects.values():
            result[item["project_title"]] = {
                "points": item["points"],
                "percent_rate": item["percent_rate"],
            }

        return result

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