from __future__ import annotations

from repositories.user_repository import UserRepository


class PointsService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def get_user_points_summary(self, telegram_user_id: int) -> dict | None:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return None

        my_projects = {}
        for entry in user.point_entries:
            my_projects[entry.project_name] = {
                "points": entry.points,
                "percent_rate": entry.percent_rate,
            }

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": my_projects,
        }

    def get_user_points_summary_by_username(self, username: str) -> dict | None:
        user = self.user_repo.get_by_username(username)
        if not user:
            return None

        my_projects = {}
        for entry in user.point_entries:
            my_projects[entry.project_name] = {
                "points": entry.points,
                "percent_rate": entry.percent_rate,
            }

        return {
            "user_id": user.telegram_user_id,
            "username": user.username,
            "full_name": user.full_name,
            "projects": my_projects,
        }

    def add_points(self, telegram_user_id: int, points_to_add: int, project_name: str) -> bool:
        user = self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            return False

        existing = None
        for entry in user.point_entries:
            if entry.project_name == project_name:
                existing = entry
                break

        if existing:
            existing.points += points_to_add
        else:
            # через relationship, чтобы не тянуть модель в этот сервис
            user.point_entries.append(
                type(user.point_entries[0])(
                    user_id=user.id,
                    project_name=project_name,
                    points=points_to_add,
                    percent_rate=0.0,
                )
            ) if user.point_entries else None

        # если у пользователя вообще не было point_entries
        if not user.point_entries:
            from database.models import UserProjectPoints
            user.point_entries.append(
                UserProjectPoints(
                    user_id=user.id,
                    project_name=project_name,
                    points=points_to_add,
                    percent_rate=0.0,
                )
            )

        self.user_repo.db.commit()
        self.recalculate_percent_rates()
        return True

    def recalculate_percent_rates(self) -> None:
        users = self.user_repo.list_all()

        totals: dict[str, int] = {}
        for user in users:
            for entry in user.point_entries:
                totals[entry.project_name] = totals.get(entry.project_name, 0) + int(entry.points or 0)

        for user in users:
            for entry in user.point_entries:
                total = totals.get(entry.project_name, 0)
                entry.percent_rate = (entry.points / total) if total > 0 else 0.0

        self.user_repo.db.commit()