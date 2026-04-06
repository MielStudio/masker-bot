from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Session, joinedload

from database.models import Event, EventParticipant, User, Task


class EventRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, event_id: int) -> Event | None:
        return (
            self.db.query(Event)
            .options(
                joinedload(Event.participants).joinedload(EventParticipant.user),
                joinedload(Event.task),
            )
            .filter(Event.id == event_id)
            .first()
        )

    def list_all(self) -> list[Event]:
        return (
            self.db.query(Event)
            .options(
                joinedload(Event.participants).joinedload(EventParticipant.user),
                joinedload(Event.task),
            )
            .order_by(Event.datetime_value.asc(), Event.id.asc())
            .all()
        )

    def list_upcoming_for_user(
        self,
        telegram_user_id: int,
        now: datetime,
        limit: int = 5,
    ) -> list[Event]:
        user = self.db.query(User).filter(User.telegram_user_id == telegram_user_id).first()
        if not user:
            return []

        events = (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.datetime_value >= now)
            .filter(Event.is_archived.is_(False))
            .order_by(Event.datetime_value.asc())
            .all()
        )

        result: list[Event] = []
        for e in events:
            if not e.personal:
                result.append(e)
            else:
                participant_ids = {p.user_id for p in e.participants}
                if user.id in participant_ids:
                    result.append(e)

        return result[:limit]

    def list_future_for_month(
        self,
        telegram_user_id: int,
        year: int,
        month: int,
        now: datetime,
    ) -> list[Event]:
        user = self.db.query(User).filter(User.telegram_user_id == telegram_user_id).first()
        if not user:
            return []

        events = (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.datetime_value >= now)
            .filter(Event.is_archived.is_(False))
            .filter(Event.datetime_value >= datetime(year, month, 1))
            .order_by(Event.datetime_value.asc())
            .all()
        )

        result: list[Event] = []
        for e in events:
            if e.datetime_value.year != year or e.datetime_value.month != month:
                continue
            if not e.personal:
                result.append(e)
            else:
                participant_ids = {p.user_id for p in e.participants}
                if user.id in participant_ids:
                    result.append(e)
        return result

    def create_deadline_event(
        self,
        event_id: int,
        task_id: int,
        telegram_user_id: int,
        title: str,
        description: str,
        dt_value: datetime,
    ) -> Event | None:
        user = self.db.query(User).filter(User.telegram_user_id == telegram_user_id).first()
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not user or not task:
            return None

        event = Event(
            id=event_id,
            type="deadline",
            title=title,
            description=description,
            datetime_value=dt_value,
            notify_users=True,
            personal=True,
            task_id=task.id,
            notified_24h=False,
            notified_2h=False,
            is_archived=False,
        )
        self.db.add(event)
        self.db.flush()

        self.db.add(EventParticipant(event_id=event.id, user_id=user.id))
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_by_task_id(self, task_id: int) -> Event | None:
        return (
            self.db.query(Event)
            .options(joinedload(Event.participants))
            .filter(Event.task_id == task_id)
            .filter(Event.is_archived.is_(False))
            .first()
        )

    def update_deadline_event(self, task_id: int, dt_value: datetime) -> Event | None:
        event = self.get_by_task_id(task_id)
        if not event:
            return None
        event.datetime_value = dt_value
        event.notified_24h = False
        event.notified_2h = False
        self.db.commit()
        self.db.refresh(event)
        return event

    def remove_by_task_id(self, task_id: int) -> int:
        events = self.db.query(Event).filter(Event.task_id == task_id).all()
        count = len(events)
        for e in events:
            self.db.delete(e)
        self.db.commit()
        return count

    def delete(self, event_id: int) -> bool:
        event = self.get_by_id(event_id)
        if not event:
            return False
        self.db.delete(event)
        self.db.commit()
        return True

    def list_for_notifications(self, now: datetime) -> list[Event]:
        return (
            self.db.query(Event)
            .options(joinedload(Event.participants).joinedload(EventParticipant.user))
            .filter(Event.notify_users.is_(True))
            .filter(Event.is_archived.is_(False))
            .filter(Event.datetime_value >= now)
            .order_by(Event.datetime_value.asc())
            .all()
        )

    def list_started_or_expired(self, now: datetime) -> list[Event]:
        return (
            self.db.query(Event)
            .options(joinedload(Event.participants).joinedload(EventParticipant.user))
            .filter(Event.is_archived.is_(False))
            .filter(Event.datetime_value <= now)
            .order_by(Event.datetime_value.asc())
            .all()
        )

    def mark_notified_24h(self, event_id: int) -> None:
        event = self.get_by_id(event_id)
        if not event:
            return
        event.notified_24h = True
        self.db.commit()

    def mark_notified_2h(self, event_id: int) -> None:
        event = self.get_by_id(event_id)
        if not event:
            return
        event.notified_2h = True
        self.db.commit()

    def archive(self, event_id: int) -> None:
        event = self.get_by_id(event_id)
        if not event:
            return
        event.is_archived = True
        self.db.commit()