from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import _LegacySQLiteWrapper
from d7_risk_engine import assess_risks_from_facts, assign_action_bucket, run_d7_pipeline
from d8_action_case import derive_action_intents

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)
BASE_DIR = Path(__file__).resolve().parents[1]


def _order(**overrides):
    base = {
        "order_id": "ORD-Q1",
        "order_no": "SO-Q1",
        "organization_id": "ORG-A",
        "owner": "USER-A",
        "requested_delivery_date": "2026-08-22",
        "current_progress": 0.92,
        "current_node": "包装/整改",
        "status": "ACTIVE",
        "updated_at": "2026-08-19T09:20:00+08:00",
    }
    base.update(overrides)
    return base


def _quality(**overrides):
    base = {
        "quality_event_id": "QE-1",
        "order_id": "ORD-Q1",
        "event_type": "PACKAGING_LABEL_ERROR",
        "status": "REWORKING",
        "description": "外箱标签印刷错误，需要重印",
        "is_delivery_blocking": 1,
        "rework_required": 1,
        "expected_resolution_at": None,
        "event_time": "2026-08-19T09:20:00+08:00",
        "resolved_at": None,
        "created_at": "2026-08-19T09:20:00+08:00",
        "updated_at": "2026-08-19T09:20:00+08:00",
    }
    base.update(overrides)
    return base


def test_structured_quality_blocking_creates_high_signal_and_do_now():
    order = _order()
    signals = assess_risks_from_facts(order, current=NOW, quality_events=[_quality()])
    q = next(s for s in signals if s["risk_type"] == "QUALITY_BLOCKING")
    assert q["severity"] == "HIGH"
    assert q["source_type"] == "QUALITY_EVENT"
    assert q["source_id"] == "QE-1"
    assert "质量问题预计解决时间" in q["missing_information"]

    bucket = assign_action_bucket(
        signals, order, current=NOW, user_id="USER-A", user_role="operator"
    )
    assert bucket["action_bucket"] == "DO_NOW"
    assert any("质量问题正在阻塞交付" in r for r in bucket["bucket_reasons"])


def test_nonblocking_or_resolved_quality_event_does_not_create_quality_risk():
    far_order = _order(requested_delivery_date="2026-09-20")
    for event in [
        _quality(is_delivery_blocking=0),
        _quality(status="RESOLVED", resolved_at="2026-08-19T09:40:00+08:00"),
    ]:
        signals = assess_risks_from_facts(far_order, current=NOW, quality_events=[event])
        assert not any(s["risk_type"] == "QUALITY_BLOCKING" for s in signals)


def test_resolution_after_customer_due_is_critical_and_escalates():
    order = _order(requested_delivery_date="2026-08-22")
    event = _quality(expected_resolution_at="2026-08-23T12:00:00+08:00")
    signals = assess_risks_from_facts(order, current=NOW, quality_events=[event])
    q = next(s for s in signals if s["risk_type"] == "QUALITY_BLOCKING")
    assert q["severity"] == "CRITICAL"
    bucket = assign_action_bucket(
        signals, order, current=NOW, user_id="USER-A", user_role="operator"
    )
    assert bucket["action_bucket"] == "ESCALATE"


def test_quality_root_cause_suppresses_generic_delivery_recovery_in_d8():
    item = {
        "order_id": "ORD-Q1",
        "action_bucket": "DO_NOW",
        "recommended_action": "立即确认返工时间",
        "severity": "HIGH",
        "evidence": ["距交期3天", "标签错误返工"],
        "risk_signals": [
            {
                "risk_type": "DELIVERY_RISK",
                "severity": "MEDIUM",
                "evidence": ["距离客户正式交期3天"],
            },
            {
                "risk_type": "QUALITY_BLOCKING",
                "severity": "HIGH",
                "evidence": ["质量阻塞：PACKAGING_LABEL_ERROR - 外箱标签印刷错误，需要重印"],
            },
        ],
    }
    intents = derive_action_intents(item, organization_id="ORG-A")
    assert [i["action_intent_key"] for i in intents] == ["v1:QUALITY_RECOVERY"]
    assert intents[0]["intent_type"] == "QUALITY_RECOVERY"
    assert any("距离客户正式交期3天" in e for e in intents[0]["evidence"])


def test_real_schema_and_pipeline_consume_quality_events_without_free_text_parsing(tmp_path):
    db_path = tmp_path / "quality-contract.db"
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn = _LegacySQLiteWrapper(raw)
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(quality_events)").fetchall()}
        assert {"is_delivery_blocking", "rework_required", "expected_resolution_at"} <= columns

        conn.execute(
            """INSERT INTO orders
               (order_id,order_no,customer_name,requested_delivery_date,current_progress,current_node,
                status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("ORD-QPIPE", "SO-QPIPE", "客户D", "2026-08-22", 0.92, "包装/整改",
             "ACTIVE", "USER-A", "ORG-A", NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            """INSERT INTO source_messages
               (message_id,order_id,organization_id,source_channel,sender_role,message_type,raw_content,source_time,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("MSG-Q1", "ORD-QPIPE", "ORG-A", "wechat", "supplier", "text",
             "标签可能有点问题，正在看", NOW.isoformat(), NOW.isoformat()),
        )
        # The structured quality event — not the text above — is what authorizes QUALITY_BLOCKING.
        conn.execute(
            """INSERT INTO quality_events
               (quality_event_id,order_id,event_type,status,description,is_delivery_blocking,rework_required,
                expected_resolution_at,event_time,source,source_message_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("QE-QPIPE", "ORD-QPIPE", "PACKAGING_LABEL_ERROR", "REWORKING",
             "外箱标签印刷错误，需要重印", 1, 1, None, NOW.isoformat(),
             "MANUAL_CONFIRMED", "MSG-Q1", NOW.isoformat(), NOW.isoformat()),
        )
        conn.commit()

        result = run_d7_pipeline(
            conn,
            {"user_id": "USER-A", "organization_id": "ORG-A", "role": "operator"},
            top_n=7,
            current_time=NOW.isoformat(),
            include_erp_snapshot=False,
        )
        assert result["count"] == 1
        item = result["items"][0]
        assert item["order_id"] == "ORD-QPIPE"
        assert item["action_bucket"] == "DO_NOW"
        assert "QUALITY_BLOCKING" in {s["risk_type"] for s in item["risk_signals"]}
        assert "确认返工/重检完成时间与可行替代方案" in item["recommended_action"]
    finally:
        raw.close()
