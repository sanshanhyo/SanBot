from __future__ import annotations

from backend.audit_log import AuditLog, AuditLogConfig


def test_audit_log_records_and_filters_events(tmp_path):
    audit_log = AuditLog(AuditLogConfig(data_dir=tmp_path, retention_days=30))
    audit_log.initialize()

    first = audit_log.record_event(
        group_id="10001",
        user_id="20001",
        command="ok",
        target="JM123456",
        status="received",
        duration_ms=12,
    )
    audit_log.record_event(
        group_id="10002",
        user_id="20002",
        command="blocked_group",
        target=None,
        status="blocked",
        error_code="GROUP_NOT_ALLOWED",
        duration_ms=0,
    )

    assert first["id"] == 1
    assert first["target"] == "JM123456"

    events = audit_log.list_events(group_id="10001", limit=20)

    assert len(events) == 1
    assert events[0]["group_id"] == "10001"
    assert events[0]["command"] == "ok"
    assert events[0]["duration_ms"] == 12
