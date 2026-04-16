class LogService:
    def __init__(self, repo):
        self.repo = repo

    def log_audit(self, actor_user_id, action_type, entity_type, entity_id=None, payload=None):
        return self.repo.add_audit_log(
            actor_user_id=actor_user_id,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )

    def log_error(self, source, message, traceback_text=None):
        return self.repo.add_error_log(
            source=source,
            message=message,
            traceback_text=traceback_text,
        )

    def log_task_history(self, task_id, action_type, actor_user_id=None, old_value=None, new_value=None, note=None):
        return self.repo.add_task_history(
            task_id=task_id,
            action_type=action_type,
            actor_user_id=actor_user_id,
            old_value=old_value,
            new_value=new_value,
            note=note,
        )

    def log_points(
        self,
        user_id,
        project_id,
        amount,
        source_type,
        reason=None,
        task_id=None,
        event_id=None,
        created_by_user_id=None,
        j_value=None,
        c_value=None,
        t_value=None,
        k_value=None,
    ):
        return self.repo.add_points_ledger(
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

    def get_recent_audit_logs(self, limit=20):
        return self.repo.get_recent_audit_logs(limit)

    def get_recent_error_logs(self, limit=20):
        return self.repo.get_recent_error_logs(limit)

    def get_recent_task_history(self, limit=20):
        return self.repo.get_recent_task_history(limit)

    def get_recent_points_ledger(self, limit=20):
        return self.repo.get_recent_points_ledger(limit)
    
    def get_points_ledger_by_user_id(self, user_id: int, limit: int = 20):
        return self.log_repo.get_points_ledger_by_user_id(user_id, limit=limit)

    def get_recent_points_ledger_filtered(self, user_id: int | None = None, limit: int = 20):
        return self.log_repo.get_recent_points_ledger_filtered(user_id=user_id, limit=limit)