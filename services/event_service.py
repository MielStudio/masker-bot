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
            "notified_2h": getattr(event, "notified_2h", False),
            "notified_30m": getattr(event, "notified_30m", False),
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

    def get_events_for_notifications(self, now) -> list[dict]:
        events = self.event_repo.list_for_notifications(now)
        return [self.event_to_legacy_dict(e) for e in events]

    def mark_notified_24h(self, event_id: int) -> bool:
        return self.event_repo.mark_notified_24h(event_id)

    def mark_notified_2h(self, event_id: int) -> bool:
        return self.event_repo.mark_notified_2h(event_id)

    def archive_event(self, event_id: int) -> bool:
        return self.event_repo.archive_event(event_id)
    
    def get_next_team_meeting(self, now):
        event = self.event_repo.get_next_team_meeting(now)
        if not event:
            return None
        return self.event_to_legacy_dict(event)

    def create_team_meeting(self, title: str, description: str, dt_value, created_by_user_id: int | None = None):
        event = self.event_repo.create_team_meeting(
            title=title,
            description=description,
            dt_value=dt_value,
            created_by_user_id=created_by_user_id,
        )
        return self.event_to_legacy_dict(event)

    def update_event_datetime(self, event_id: int, new_dt):
        event = self.event_repo.update_event_datetime(event_id, new_dt)
        if not event:
            return None
        return self.event_to_legacy_dict(event)
    
    def get_last_started_meeting(self, now):
        event = self.event_repo.get_last_started_meeting(now)
        if not event:
            return None
        return self.event_to_legacy_dict(event)
    
    def finish_meeting(self, event_id: int, finished_at):
        event = self.event_repo.finish_meeting(event_id, finished_at)
        if not event:
            return None
        return self.event_to_legacy_dict(event)
    
    def save_attendance(self, event_id: int, present_tg_ids: list[int], marked_by_tg_id: int):
        return self.event_repo.save_attendance(
            event_id=event_id,
            present_tg_ids=present_tg_ids,
            marked_by_tg_id=marked_by_tg_id,
        )
    
    def get_attendance_by_event_id(self, event_id: int):
        return self.event_repo.get_attendance_by_event_id(event_id)
    
    def mark_notified_30m(self, event_id: int) -> bool:
        return self.event_repo.mark_notified_30m(event_id)
    