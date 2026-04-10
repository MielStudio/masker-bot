from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)

    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_idle_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    language_code: Mapped[str | None] = mapped_column(String, nullable=True)
    max_active_tasks_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    work_roles: Mapped[list["UserWorkRole"]] = relationship(
        "UserWorkRole",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    project_memberships: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    teamlead_projects: Mapped[list["ProjectTeamlead"]] = relationship(
        "ProjectTeamlead",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    task_assignments: Mapped[list["TaskAssignee"]] = relationship(
        "TaskAssignee",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="TaskAssignee.user_id",
    )

    created_tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="created_by",
        foreign_keys="Task.created_by_user_id",
    )

    created_events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="created_by",
        foreign_keys="Event.created_by_user_id",
    )

    event_participants: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    event_attendance_links: Mapped[list["EventAttendance"]] = relationship(
        "EventAttendance",
        back_populates="user",
        foreign_keys="EventAttendance.user_id",
    )

    ledger_entries: Mapped[list["PointsLedger"]] = relationship(
        "PointsLedger",
        back_populates="user",
        foreign_keys="PointsLedger.user_id",
    )

    created_ledger_entries: Mapped[list["PointsLedger"]] = relationship(
        "PointsLedger",
        back_populates="created_by",
        foreign_keys="PointsLedger.created_by_user_id",
    )

    point_snapshots: Mapped[list["ProjectPointSnapshot"]] = relationship(
        "ProjectPointSnapshot",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    task_reviews: Mapped[list["TaskReview"]] = relationship(
        "TaskReview",
        back_populates="reviewer",
        foreign_keys="TaskReview.reviewer_user_id",
    )

    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="actor",
        foreign_keys="AuditLog.actor_user_id",
    )

    onboarding_sessions: Mapped[list["OnboardingSession"]] = relationship(
        "OnboardingSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)

    user_links: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )


class UserPermission(Base):
    __tablename__ = "user_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="permissions")
    permission: Mapped["Permission"] = relationship("Permission", back_populates="user_links")


class WorkRole(Base):
    __tablename__ = "work_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    emoji: Mapped[str | None] = mapped_column(String, nullable=True)

    user_links: Mapped[list["UserWorkRole"]] = relationship(
        "UserWorkRole",
        back_populates="work_role",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="required_work_role",
    )


class UserWorkRole(Base):
    __tablename__ = "user_work_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "work_role_id", name="uq_user_work_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    work_role_id: Mapped[int] = mapped_column(ForeignKey("work_roles.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    user: Mapped["User"] = relationship("User", back_populates="work_roles")
    work_role: Mapped["WorkRole"] = relationship("WorkRole", back_populates="user_links")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_max_active_tasks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    members: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    teamleads: Mapped[list["ProjectTeamlead"]] = relationship(
        "ProjectTeamlead",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="project",
    )

    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="project",
    )

    point_snapshots: Mapped[list["ProjectPointSnapshot"]] = relationship(
        "ProjectPointSnapshot",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    ledger_entries: Mapped[list["PointsLedger"]] = relationship(
        "PointsLedger",
        back_populates="project",
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="project_memberships")


class ProjectTeamlead(Base):
    __tablename__ = "project_teamleads"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_teamlead"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="teamleads")
    user: Mapped["User"] = relationship("User", back_populates="teamlead_projects")


class TaskCategory(Base):
    __tablename__ = "task_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="category")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category_id: Mapped[int | None] = mapped_column(ForeignKey("task_categories.id"), nullable=True)
    required_work_role_id: Mapped[int | None] = mapped_column(ForeignKey("work_roles.id"), nullable=True)

    priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String, nullable=False, default="available")
    max_assignees: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    estimated_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    j_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    c_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    t_value: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")
    category: Mapped["TaskCategory | None"] = relationship("TaskCategory", back_populates="tasks")
    required_work_role: Mapped["WorkRole | None"] = relationship("WorkRole", back_populates="tasks")
    created_by: Mapped["User | None"] = relationship("User", back_populates="created_tasks")

    assignees: Mapped[list["TaskAssignee"]] = relationship(
        "TaskAssignee",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    attachments: Mapped[list["TaskAttachment"]] = relationship(
        "TaskAttachment",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    checklists: Mapped[list["TaskChecklist"]] = relationship(
        "TaskChecklist",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    history_entries: Mapped[list["TaskHistory"]] = relationship(
        "TaskHistory",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    reviews: Mapped[list["TaskReview"]] = relationship(
        "TaskReview",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    events: Mapped[list["Event"]] = relationship("Event", back_populates="related_task")
    ledger_entries: Mapped[list["PointsLedger"]] = relationship("PointsLedger", back_populates="task")


class TaskAssignee(Base):
    __tablename__ = "task_assignees"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_assignee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="assignees")
    user: Mapped["User"] = relationship("User", back_populates="task_assignments", foreign_keys=[user_id])


class TaskAttachment(Base):
    __tablename__ = "task_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="attachments")


class TaskChecklist(Base):
    __tablename__ = "task_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="checklists")


class TaskHistory(Base):
    __tablename__ = "task_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="history_entries")


class TaskReview(Base):
    __tablename__ = "task_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    k_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="reviews")
    reviewer: Mapped["User"] = relationship("User", back_populates="task_reviews")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    related_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)

    scope: Mapped[str] = mapped_column(Text, nullable=False, default="team")
    subtype: Mapped[str] = mapped_column(Text, nullable=False, default="reminder")

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    datetime_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    notify_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_silent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    notified_24h: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notified_3h: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notified_30m: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    meeting_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project | None"] = relationship("Project", back_populates="events")
    related_task: Mapped["Task | None"] = relationship("Task", back_populates="events")
    created_by: Mapped["User | None"] = relationship("User", back_populates="created_events")

    participants: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    attendance_records: Mapped[list["EventAttendance"]] = relationship(
        "EventAttendance",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    ledger_entries: Mapped[list["PointsLedger"]] = relationship("PointsLedger", back_populates="event")


class EventParticipant(Base):
    __tablename__ = "event_participants"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_participant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    event: Mapped["Event"] = relationship("Event", back_populates="participants")
    user: Mapped["User"] = relationship("User", back_populates="event_participants")


class EventAttendance(Base):
    __tablename__ = "event_attendance"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_attendance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    marked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped["Event"] = relationship("Event", back_populates="attendance_records")
    user: Mapped["User"] = relationship("User", back_populates="event_attendance_links", foreign_keys=[user_id])


class PointsLedger(Base):
    __tablename__ = "points_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)

    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    j_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    c_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    k_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="ledger_entries", foreign_keys=[user_id])
    project: Mapped["Project"] = relationship("Project", back_populates="ledger_entries")
    task: Mapped["Task | None"] = relationship("Task", back_populates="ledger_entries")
    event: Mapped["Event | None"] = relationship("Event", back_populates="ledger_entries")
    created_by: Mapped["User | None"] = relationship("User", back_populates="created_ledger_entries", foreign_keys=[created_by_user_id])


class ProjectPointSnapshot(Base):
    __tablename__ = "project_point_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_point_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    total_points: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    percent_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="point_snapshots")
    user: Mapped["User"] = relationship("User", back_populates="point_snapshots")




class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    actor: Mapped["User | None"] = relationship("User", back_populates="audit_logs")


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="started")
    Field4: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_language: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="onboarding_sessions")