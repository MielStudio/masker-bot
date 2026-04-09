from sqlalchemy.orm import Session

from database.models import User


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

    def list_all(self) -> list[User]:
        return self.db.query(User).all()

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