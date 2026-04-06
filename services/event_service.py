from __future__ import annotations

from repositories.event_repository import EventRepository


class EventService:
    def __init__(self, event_repo: EventRepository):
        self.event_repo = event_repo

    def event_to_legacy_dict(self, event) -> dict:
        return {
            "id": event.id,
            "type": event.type,
            "title": event.title,
            "description": event.description,
            "datetime": event.datetime_value.isoformat() if event.datetime_value else None,
            "notify_users": event.notify_users,
            "personal": event.personal,
            "users": [
                p.user.telegram_user_id
                for p in event.participants
                if p.user is not None
            ],
            "task_id": event.task_id,
            "notified_24h": event.notified_24h,
            "notified_2h": event.notified_2h,
            "is_archived": event.is_archived,
        }

    def get_upcoming_for_user(self, telegram_user_id: int, now, limit: int = 5) -> list[dict]:
        events = self.event_repo.list_upcoming_for_user(telegram_user_id, now, limit=limit)
        return [self.event_to_legacy_dict(e) for e in events]

    def get_future_for_month(self, telegram_user_id: int, year: int, month: int, now) -> list[dict]:
        events = self.event_repo.list_future_for_month(telegram_user_id, year, month, now)
        return [self.event_to_legacy_dict(e) for e in events]

    def get_all_events(self) -> list[dict]:
        return [self.event_to_legacy_dict(e) for e in self.event_repo.list_all()]

    def get_event_by_id(self, event_id: int) -> dict | None:
        event = self.event_repo.get_by_id(event_id)
        if not event:
            return None
        return self.event_to_legacy_dict(event)

    def delete_event(self, event_id: int) -> bool:
        return self.event_repo.delete(event_id)

    def get_events_for_notifications(self, now) -> list[dict]:
        events = self.event_repo.list_for_notifications(now)
        return [self.event_to_legacy_dict(e) for e in events]

    def get_started_or_expired(self, now) -> list[dict]:
        events = self.event_repo.list_started_or_expired(now)
        return [self.event_to_legacy_dict(e) for e in events]

    def mark_notified_24h(self, event_id: int) -> None:
        self.event_repo.mark_notified_24h(event_id)

    def mark_notified_2h(self, event_id: int) -> None:
        self.event_repo.mark_notified_2h(event_id)

    def archive(self, event_id: int) -> None:
        self.event_repo.archive(event_id)