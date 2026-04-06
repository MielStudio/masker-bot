from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session, joinedload

from database.models import User, UserProjectPoints, UserRole


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, db_user_id: int) -> User | None:
        return (
            self.db.query(User)
            .options(
                joinedload(User.roles).joinedload(UserRole.role),
                joinedload(User.point_entries),
            )
            .filter(User.id == db_user_id)
            .first()
        )

    def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return (
            self.db.query(User)
            .options(
                joinedload(User.roles).joinedload(UserRole.role),
                joinedload(User.point_entries),
            )
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
        )

    def get_by_username(self, username: str) -> User | None:
        username = username.lstrip("@").strip().lower()
        return (
            self.db.query(User)
            .options(
                joinedload(User.roles).joinedload(UserRole.role),
                joinedload(User.point_entries),
            )
            .filter(User.username.is_not(None))
            .filter(User.username.ilike(username))
            .first()
        )

    def list_all(self) -> list[User]:
        return (
            self.db.query(User)
            .options(
                joinedload(User.roles).joinedload(UserRole.role),
                joinedload(User.point_entries),
            )
            .order_by(User.username.asc().nullslast(), User.full_name.asc().nullslast())
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
        user.last_idle_reminder = dt_value
        self.db.commit()
        return True

    def get_project_points_map(self, user: User) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for entry in user.point_entries:
            result[entry.project_name] = {
                "points": entry.points,
                "percent_rate": entry.percent_rate,
            }
        return result

    def replace_project_points(
        self,
        user: User,
        entries: Iterable[dict],
    ) -> None:
        self.db.query(UserProjectPoints).filter(UserProjectPoints.user_id == user.id).delete()
        for item in entries:
            self.db.add(
                UserProjectPoints(
                    user_id=user.id,
                    project_name=item["project_name"],
                    points=int(item.get("points", 0) or 0),
                    percent_rate=float(item.get("percent_rate", 0.0) or 0.0),
                )
            )
        self.db.commit()