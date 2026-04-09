from sqlalchemy import Column, Integer, Text, REAL, ForeignKey
from db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action_type = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(Text, nullable=True)


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    created_at = Column(Text, nullable=True)


class TaskHistory(Base):
    __tablename__ = "task_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action_type = Column(Text, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(Text, nullable=True)


class PointsLedger(Base):
    __tablename__ = "points_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    source_type = Column(Text, nullable=False)
    amount = Column(REAL, nullable=False)
    reason = Column(Text, nullable=True)
    j_value = Column(REAL, nullable=True)
    c_value = Column(REAL, nullable=True)
    t_value = Column(REAL, nullable=True)
    k_value = Column(REAL, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, nullable=True)