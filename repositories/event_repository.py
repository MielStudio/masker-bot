from datetime import datetime
from sqlalchemy.orm import Session, joinedload

from database.models import Event, EventParticipant, User, Task


class EventRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, event_id: int) -> Event | None:
        return (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.id == event_id)
            .first()
        )

    def list_upcoming_for_user(
        self,
        telegram_user_id: int,
        now: datetime,
        limit: int = 5,
    ) -> list[Event]:

        user = (
            self.db.query(User)
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
        )

        if not user:
            return []

        events = (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.datetime_at >= now)
            .filter(Event.is_archived.is_(False))
            .order_by(Event.datetime_at.asc())
            .all()
        )

        result = []

        for e in events:
            if e.scope != "personal":
                result.append(e)
            else:
                user_ids = {p.user_id for p in e.participants}
                if user.id in user_ids:
                    result.append(e)

        return result[:limit]

    def create_deadline_event(
        self,
        task_id: int,
        telegram_user_id: int,
        title: str,
        description: str,
        dt_value: datetime,
    ) -> Event | None:

        user = (
            self.db.query(User)
            .filter(User.telegram_user_id == telegram_user_id)
            .first()
        )

        task = self.db.query(Task).filter(Task.id == task_id).first()

        if not user or not task:
            return None

        event = Event(
            title=title,
            description=description,
            datetime_at=dt_value,
            related_task_id=task.id,
            notify_users=True,
            scope="personal",
            is_archived=False,
        )

        self.db.add(event)
        self.db.flush()

        self.db.add(
            EventParticipant(
                event_id=event.id,
                user_id=user.id,
            )
        )

        self.db.commit()
        self.db.refresh(event)
        return event

    def get_by_task_id(self, task_id: int) -> Event | None:
        return (
            self.db.query(Event)
            .filter(Event.related_task_id == task_id)
            .filter(Event.is_archived.is_(False))
            .first()
        )

    def remove_by_task_id(self, task_id: int) -> int:
        events = (
            self.db.query(Event)
            .filter(Event.related_task_id == task_id)
            .all()
        )

        count = len(events)

        for e in events:
            self.db.delete(e)

        self.db.commit()
        return count
    
    def list_for_notifications(self, now: datetime) -> list[Event]:
        return (
            self.db.query(Event)
            .options(
                joinedload(Event.participants).joinedload(EventParticipant.user)
            )
            .filter(Event.notify_users.is_(True))
            .filter(Event.is_archived.is_(False))
            .all()
        )

    def mark_notified_24h(self, event_id: int) -> bool:
        event = self.get_by_id(event_id)
        if not event:
            return False
        event.notified_24h = True
        self.db.commit()
        return True

    def mark_notified_2h(self, event_id: int) -> bool:
        event = self.get_by_id(event_id)
        if not event:
            return False
        event.notified_2h = True
        self.db.commit()
        return True

    def archive_event(self, event_id: int) -> bool:
        event = self.get_by_id(event_id)
        if not event:
            return False
        event.is_archived = True
        self.db.commit()
        return True

    def upsert_deadline_event(
        self,
        task_id: int,
        telegram_user_ids: list[int],
        title: str,
        description: str,
        dt_value: datetime,
    ) -> Event | None:
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return None

        event = (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.related_task_id == task_id)
            .filter(Event.is_archived.is_(False))
            .first()
        )

        if not event:
            event = Event(
                title=title,
                description=description,
                datetime_at=dt_value,
                related_task_id=task.id,
                notify_users=True,
                scope="personal",
                is_archived=False,
                notified_24h=False,
                notified_2h=False,
            )
            self.db.add(event)
            self.db.flush()
        else:
            event.title = title
            event.description = description
            event.datetime_at = dt_value
            event.notify_users = True
            event.notified_24h = False
            event.notified_2h = False

            for p in list(event.participants):
                self.db.delete(p)
            self.db.flush()

        users = (
            self.db.query(User)
            .filter(User.telegram_user_id.in_(telegram_user_ids))
            .all()
        )

        for user in users:
            self.db.add(
                EventParticipant(
                    event_id=event.id,
                    user_id=user.id,
                )
            )

        self.db.commit()
        self.db.refresh(event)
        return event