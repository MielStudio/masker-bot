import json
from models.log_models import AuditLog, ErrorLog, TaskHistory, PointsLedger


class LogRepository:
    def __init__(self, db):
        self.db = db

    def add_audit_log(
        self,
        actor_user_id: int | None,
        action_type: str,
        entity_type: str,
        entity_id: int | None = None,
        payload: dict | None = None,
    ):
        row = AuditLog(
            actor_user_id=actor_user_id,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_error_log(self, source: str, message: str, traceback_text: str | None = None):
        row = ErrorLog(
            source=source,
            message=message,
            traceback=traceback_text,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_task_history(
        self,
        task_id: int,
        action_type: str,
        actor_user_id: int | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        note: str | None = None,
    ):
        row = TaskHistory(
            task_id=task_id,
            actor_user_id=actor_user_id,
            action_type=action_type,
            old_value=old_value,
            new_value=new_value,
            note=note,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_points_ledger(
        self,
        user_id: int,
        project_id: int,
        amount: float,
        source_type: str,
        reason: str | None = None,
        task_id: int | None = None,
        event_id: int | None = None,
        created_by_user_id: int | None = None,
        j_value: float | None = None,
        c_value: float | None = None,
        t_value: float | None = None,
        k_value: float | None = None,
    ):
        row = PointsLedger(
            user_id=user_id,
            project_id=project_id,
            amount=amount,
            source_type=source_type,
            reason=reason,
            task_id=task_id,
            event_id=event_id,
            created_by_user_id=created_by_user_id,
            j_value=j_value,
            c_value=c_value,
            t_value=t_value,
            k_value=k_value,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row