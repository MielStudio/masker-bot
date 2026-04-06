import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config import ROLE_CATALOG, ROLE_SYNONYMS
from database.db import SessionLocal
from database.models import (
    Event,
    EventParticipant,
    Role,
    Task,
    User,
    UserProjectPoints,
    UserRole,
)


BASE_DIR = Path(__file__).resolve().parent.parent
USERS_JSON = BASE_DIR / "users.json"
TASKS_JSON = BASE_DIR / "tasks.json"
EVENTS_JSON = BASE_DIR / "events.json"


def load_json_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"[WARN] File not found: {path}")
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"[WARN] Expected list in {path}, got {type(data).__name__}")
            return []
        return data
    except Exception as e:
        print(f"[ERROR] Failed to read {path}: {e}")
        return []


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def guess_role_code(name: str) -> str | None:
    s = (name or "").lower()
    for key, code in ROLE_SYNONYMS.items():
        if key in s:
            return code
    return None


def ensure_roles(db: Session) -> dict[str, Role]:
    existing = db.query(Role).all()
    by_code = {r.code: r for r in existing}

    changed = False
    for code, (title, emoji) in ROLE_CATALOG.items():
        if code not in by_code:
            role = Role(code=code, title=title, emoji=emoji)
            db.add(role)
            by_code[code] = role
            changed = True

    if changed:
        db.commit()
        existing = db.query(Role).all()
        by_code = {r.code: r for r in existing}

    return by_code


def clear_tables(db: Session) -> None:
    # Порядок важен из-за foreign keys
    db.query(EventParticipant).delete()
    db.query(UserRole).delete()
    db.query(UserProjectPoints).delete()
    db.query(Event).delete()
    db.query(Task).delete()
    db.query(Role).delete()
    db.query(User).delete()
    db.commit()


def migrate_users(db: Session, role_map: dict[str, Role], users_data: list[dict[str, Any]]) -> dict[int, User]:
    user_by_tg_id: dict[int, User] = {}

    for raw in users_data:
        tg_id = raw.get("user_id")
        if not tg_id:
            continue

        user = User(
            telegram_user_id=int(tg_id),
            username=raw.get("username"),
            full_name=raw.get("full_name"),
            joined_at=parse_dt(raw.get("joined_at")),
            last_idle_reminder=parse_dt(raw.get("last_idle_reminder")),
            is_active=True,
            total_points=0,
        )
        db.add(user)
        db.flush()  # чтобы user.id уже был доступен

        # roles_ext имеет приоритет
        roles_ext = raw.get("roles_ext")
        if isinstance(roles_ext, list) and roles_ext:
            for item in roles_ext:
                code = item.get("id")
                level = int(item.get("level", 2))
                role = role_map.get(code)
                if role:
                    db.add(UserRole(user_id=user.id, role_id=role.id, level=level))
        else:
            # fallback на старый roles
            for role_name in raw.get("roles", []) or []:
                code = guess_role_code(role_name)
                role = role_map.get(code) if code else None
                if role:
                    exists = db.query(UserRole).filter_by(user_id=user.id, role_id=role.id).first()
                    if not exists:
                        db.add(UserRole(user_id=user.id, role_id=role.id, level=2))

        # points / percent_rate по проектам
        points_obj = raw.get("points", {}) or {}
        percent_obj = raw.get("percent_rate", {}) or {}

        if isinstance(points_obj, (int, float)):
            points_obj = {"Non-project work": int(points_obj)}

        if isinstance(percent_obj, (int, float)):
            percent_obj = {k: float(percent_obj) for k in points_obj.keys()}

        total_points = 0
        for project_name, points_value in points_obj.items():
            try:
                pts = int(points_value)
            except Exception:
                pts = 0

            total_points += pts
            percent_rate = float(percent_obj.get(project_name, 0.0) or 0.0)

            db.add(
                UserProjectPoints(
                    user_id=user.id,
                    project_name=project_name,
                    points=pts,
                    percent_rate=percent_rate,
                )
            )

        user.total_points = total_points
        user_by_tg_id[int(tg_id)] = user

    db.commit()

    # перечитываем после commit
    result = db.query(User).all()
    return {u.telegram_user_id: u for u in result}


def migrate_tasks(db: Session, role_map: dict[str, Role], user_by_tg_id: dict[int, User], tasks_data: list[dict[str, Any]]) -> dict[int, Task]:
    task_by_legacy_id: dict[int, Task] = {}

    for raw in tasks_data:
        legacy_id = raw.get("id")
        if legacy_id is None:
            continue

        type_code = raw.get("type")
        required_role = role_map.get(type_code) if isinstance(type_code, str) else None

        assignee_id = None
        reserved_by = raw.get("reserved_by")
        if reserved_by is not None:
            user = user_by_tg_id.get(int(reserved_by))
            if user:
                assignee_id = user.id

        task = Task(
            id=int(legacy_id),
            title=raw.get("title") or f"Task #{legacy_id}",
            description=raw.get("description"),
            type_code=type_code,
            required_role_id=required_role.id if required_role else None,
            project=raw.get("project"),
            points=int(raw.get("points", 0) or 0),
            estimated_days=int(raw.get("estimated_days", 7) or 7),
            assignee_id=assignee_id,
            deadline=parse_dt(raw.get("deadline")),
            status="in_progress" if assignee_id else "open",
        )
        db.add(task)
        task_by_legacy_id[int(legacy_id)] = task

    db.commit()

    result = db.query(Task).all()
    return {t.id: t for t in result}


def migrate_events(db: Session, user_by_tg_id: dict[int, User], task_by_legacy_id: dict[int, Task], events_data: list[dict[str, Any]]) -> None:
    for raw in events_data:
        legacy_id = raw.get("id")
        dt = parse_dt(raw.get("datetime"))

        if legacy_id is None or dt is None:
            continue

        legacy_task_id = raw.get("task_id")
        linked_task = task_by_legacy_id.get(int(legacy_task_id)) if legacy_task_id is not None else None

        event = Event(
            id=int(legacy_id),
            type=raw.get("type") or "other",
            title=raw.get("title") or f"Event #{legacy_id}",
            description=raw.get("description"),
            datetime_value=dt,
            notify_users=bool(raw.get("notify_users", True)),
            personal=bool(raw.get("personal", False)),
            task_id=linked_task.id if linked_task else None,
            notified_24h=bool(raw.get("notified_24h", False)),
            notified_2h=bool(raw.get("notified_2h", False)),
            is_archived=False,
        )
        db.add(event)
        db.flush()

        users = raw.get("users", []) or []
        for tg_user_id in users:
            user = user_by_tg_id.get(int(tg_user_id))
            if user:
                db.add(EventParticipant(event_id=event.id, user_id=user.id))

    db.commit()


def print_summary(db: Session) -> None:
    print("Migration complete:")
    print(f"  Users: {db.query(User).count()}")
    print(f"  Roles: {db.query(Role).count()}")
    print(f"  UserRoles: {db.query(UserRole).count()}")
    print(f"  UserProjectPoints: {db.query(UserProjectPoints).count()}")
    print(f"  Tasks: {db.query(Task).count()}")
    print(f"  Events: {db.query(Event).count()}")
    print(f"  EventParticipants: {db.query(EventParticipant).count()}")


def main() -> None:
    users_data = load_json_file(USERS_JSON)
    tasks_data = load_json_file(TASKS_JSON)
    events_data = load_json_file(EVENTS_JSON)

    db = SessionLocal()
    try:
        print("[INFO] Clearing old database data...")
        clear_tables(db)

        print("[INFO] Creating roles...")
        role_map = ensure_roles(db)

        print("[INFO] Migrating users...")
        user_by_tg_id = migrate_users(db, role_map, users_data)

        print("[INFO] Migrating tasks...")
        task_by_legacy_id = migrate_tasks(db, role_map, user_by_tg_id, tasks_data)

        print("[INFO] Migrating events...")
        migrate_events(db, user_by_tg_id, task_by_legacy_id, events_data)

        print_summary(db)

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()