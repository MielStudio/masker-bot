from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Float,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_idle_reminder: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Пока оставляем флаги и агрегаты ближе к твоей текущей логике
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    total_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # relationships
    roles: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    assigned_tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="assignee",
        foreign_keys="Task.assignee_id",
    )

    event_links: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    point_entries: Mapped[list["UserProjectPoints"]] = relationship(
        "UserProjectPoints",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_user_id={self.telegram_user_id} username={self.username!r}>"



class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user_links: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="required_role",
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} code={self.code!r}>"


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)

    # Это соответствует твоему roles_ext.level
    level: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="roles")
    role: Mapped["Role"] = relationship("Role", back_populates="user_links")

    def __repr__(self) -> str:
        return f"<UserRole user_id={self.user_id} role_id={self.role_id} level={self.level}>"


class UserProjectPoints(Base):
    """
    Замена points/percent_rate по проектам в users.json.
    Пока можно хранить агрегаты по каждому проекту на пользователя.
    """
    __tablename__ = "user_project_points"
    __table_args__ = (
        UniqueConstraint("user_id", "project_name", name="uq_user_project_points"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Аналог percent_rate[project], если захочешь пока хранить как есть
    percent_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="point_entries")

    def __repr__(self) -> str:
        return f"<UserProjectPoints user_id={self.user_id} project={self.project_name!r} points={self.points}>"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # В текущем коде это task["type"], который матчится с ролью
    type_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Нормализованная связь на роль, если будешь использовать дальше
    required_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
    )

    project: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    # Замена reserved_by
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # На будущее, чтобы не удалять задачи физически
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignee: Mapped["User | None"] = relationship("User", back_populates="assigned_tasks")
    required_role: Mapped["Role | None"] = relationship("Role", back_populates="tasks")

    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="task",
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} title={self.title!r} assignee_id={self.assignee_id}>"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # meeting / deadline / other
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    datetime_value: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    notify_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    personal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Аналог task_id из json
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    notified_24h: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notified_2h: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # На будущее лучше архивировать, чем удалять
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped["Task | None"] = relationship("Task", back_populates="events")
    participants: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Event id={self.id} type={self.type!r} title={self.title!r}>"


class EventParticipant(Base):
    __tablename__ = "event_participants"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_participant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    event: Mapped["Event"] = relationship("Event", back_populates="participants")
    user: Mapped["User"] = relationship("User", back_populates="event_links")

    def __repr__(self) -> str:
        return f"<EventParticipant event_id={self.event_id} user_id={self.user_id}>"