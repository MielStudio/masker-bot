from __future__ import annotations

from repositories.event_repository import EventRepository


class EventService:
    def __init__(self, event_repo: EventRepository):
        self.event_repo = event_repo

    def event_to_legacy_dict(self, event) -> dict:
        return {
            "id": event.id,
            "type": event.subtype or "event",
            "title": event.title,
            "description": event.description,
            "datetime": event.datetime_at.isoformat() if event.datetime_at else None,
            "notify_users": event.notify_users,
            "personal": (event.scope == "personal"),
            "users": [p.user.telegram_user_id for p in event.participants if p.user is not None],
            "task_id": event.related_task_id,
            "notified_24h": event.notified_24h,
            "is_archived": event.is_archived,
        }

    def get_upcoming_for_user(self, telegram_user_id: int, now, limit: int = 5) -> list[dict]:
        events = self.event_repo.list_upcoming_for_user(telegram_user_id, now, limit=limit)
        return [self.event_to_legacy_dict(e) for e in events]

    def get_event_by_id(self, event_id: int) -> dict | None:
        event = self.event_repo.get_by_id(event_id)
        if not event:
            return None
        return self.event_to_legacy_dict(event)