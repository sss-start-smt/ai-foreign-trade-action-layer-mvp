"""FlowOrder D15: durable failure handling and RESULT_UNCERTAIN governance.

D15 sits *after* D10 durable acceptance. It does not change the frozen D10
meaning of BusinessAction.ACCEPTED / Outbox.PENDING. Instead, it defines how a
future external dispatcher must behave when delivery succeeds, fails safely,
can be retried, or becomes ambiguous after a request may already have reached
an external system.

Core safety rule:
    unknown external result != failed != success

If a request may have produced an external side effect but its result cannot be
confirmed, the state is RESULT_UNCERTAIN and automatic retry is forbidden until
reconciliation proves whether the effect happened.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from d8_action_case import _conn_exec, _row_to_dict
from database import table_exists

CN_TZ = timezone(timedelta(hours=8))
D15_POLICY_VERSION = "D15_DURABLE_EXECUTION_V1"
DEFAULT_RETRY_BUDGET = 3

STATE_PENDING = "PENDING"
STATE_IN_FLIGHT = "IN_FLIGHT"  # internal crash-safety state
STATE_SUCCESS = "SUCCESS"
STATE_FAILED_SAFE = "FAILED_SAFE"
STATE_RETRYABLE = "RETRYABLE"
STATE_RESULT_UNCERTAIN = "RESULT_UNCERTAIN"
STATE_HUMAN_REQUIRED = "HUMAN_REQUIRED"

AUTO_DISPATCHABLE_STATES = {STATE_PENDING, STATE_RETRYABLE}
BLOCKED_STATES = {STATE_RESULT_UNCERTAIN, STATE_HUMAN_REQUIRED, STATE_FAILED_SAFE}
FINAL_OR_STOP_STATES = {STATE_SUCCESS, STATE_FAILED_SAFE, STATE_RESULT_UNCERTAIN, STATE_HUMAN_REQUIRED}

EFFECT_TRUE = "TRUE"
EFFECT_FALSE = "FALSE"
EFFECT_UNKNOWN = "UNKNOWN"

UI_MESSAGES: dict[str, dict[str, Any]] = {
    STATE_PENDING: {
        "code": "ACTION_PENDING",
        "title": "等待执行",
        "message": "业务动作已被系统可靠记录，尚未确认外部执行结果。",
        "primary_action": None,
        "secondary_action": None,
        "auto_retry_allowed": False,
    },
    STATE_IN_FLIGHT: {
        "code": "ACTION_IN_FLIGHT",
        "title": "正在处理",
        "message": "外部操作正在处理中，请不要重复提交。",
        "primary_action": None,
        "secondary_action": None,
        "auto_retry_allowed": False,
    },
    STATE_SUCCESS: {
        "code": "ACTION_CONFIRMED_SUCCESS",
        "title": "已确认操作成功",
        "message": "系统已确认该外部操作成功执行。",
        "primary_action": "VIEW_RESULT",
        "secondary_action": None,
        "auto_retry_allowed": False,
    },
    STATE_FAILED_SAFE: {
        "code": "ACTION_FAILED_SAFE",
        "title": "本次操作未执行",
        "message": "系统已确认该操作没有产生外部业务变更。",
        "primary_action": "RETRY_EXPLICITLY",
        "secondary_action": "BACK_TO_DETAIL",
        "auto_retry_allowed": False,
    },
    STATE_RETRYABLE: {
        "code": "ACTION_RETRYABLE",
        "title": "外部服务暂时不可用",
        "message": "系统已确认本次失败没有产生外部副作用，可以在有限重试预算内安全重试。",
        "primary_action": "RETRY_LATER",
        "secondary_action": "HAND_OFF_TO_HUMAN",
        "auto_retry_allowed": True,
    },
    STATE_RESULT_UNCERTAIN: {
        "code": "ACTION_RESULT_UNCERTAIN",
        "title": "操作结果暂无法确认",
        "message": "系统无法确认刚才的外部操作是否已经执行。为避免重复操作，自动重试已暂停。",
        "primary_action": "RECONCILE_RESULT",
        "secondary_action": "HAND_OFF_TO_HUMAN",
        "auto_retry_allowed": False,
    },
    STATE_HUMAN_REQUIRED: {
        "code": "ACTION_HUMAN_REQUIRED",
        "title": "需要人工处理",
        "message": "自动流程已暂停。请完成人工核对、授权或异常处理后再继续。",
        "primary_action": "OPEN_HUMAN_REVIEW",
        "secondary_action": "BACK_TO_DETAIL",
        "auto_retry_allowed": False,
    },
}


class D15Error(RuntimeError):
    pass


class D15NotFound(D15Error):
    pass


class D15StateError(D15Error):
    pass


class D15Forbidden(D15Error):
    pass


class _SafeDispatchSignal(D15Error):
    """Base for adapter signals that carry only allow-listed failure metadata."""

    state: str = STATE_FAILED_SAFE

    def __init__(self, *, error_kind: str) -> None:
        # Do not accept/store provider exception text here. `error_kind` must be
        # a short classification code suitable for public/business trace.
        self.error_kind = _safe_code(error_kind, fallback="EXTERNAL_FAILURE")
        super().__init__(self.error_kind)


class D15RetryableNoEffect(_SafeDispatchSignal):
    state = STATE_RETRYABLE


class D15FailedSafe(_SafeDispatchSignal):
    state = STATE_FAILED_SAFE


class D15ResultUncertain(_SafeDispatchSignal):
    state = STATE_RESULT_UNCERTAIN


class D15HumanRequired(_SafeDispatchSignal):
    state = STATE_HUMAN_REQUIRED


@dataclass(frozen=True)
class DispatchReceipt:
    """Confirmed success receipt from an external adapter.

    The receipt deliberately contains only allow-listed metadata. Adapters must
    not return full raw HTTP bodies, credentials, or Authorization headers.
    """

    external_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExternalAdapter(Protocol):
    def dispatch(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        request_id: str,
    ) -> DispatchReceipt: ...


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _safe_code(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return fallback
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    cleaned = "".join(ch for ch in text if ch in allowed)[:80]
    return cleaned or fallback


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_metadata(value: Any) -> dict[str, Any]:
    """Keep trace metadata intentionally small and secret-resistant.

    Unknown keys are ignored. This prevents adapters from accidentally storing
    raw provider responses or exception strings in the business trace.
    """
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "external_reference",
        "status_code",
        "provider",
        "adapter",
        "reconciliation_ref",
        "no_effect_confirmed",
        "result_source",
    }
    safe: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            text = str(item) if isinstance(item, str) else item
            if isinstance(text, str):
                text = text[:240]
            safe[key] = text
    return safe


def _effect_status(value: bool | None) -> str:
    if value is True:
        return EFFECT_TRUE
    if value is False:
        return EFFECT_FALSE
    return EFFECT_UNKNOWN


def _bool_int(value: bool) -> int:
    return 1 if value else 0


def _ensure_tables(conn: Any) -> None:
    required = (
        "d10_business_actions",
        "d10_outbox_events",
        "d15_outbox_execution_state",
        "d15_execution_trace_events",
    )
    missing = [table for table in required if not table_exists(conn, table)]
    if missing:
        raise RuntimeError(
            "D15 schema is not migrated. Run alembic upgrade head or initialize from schema.sql. "
            f"Missing tables: {missing}"
        )


def _load_outbox(conn: Any, event_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = _conn_exec(
        conn,
        "SELECT * FROM d10_outbox_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if not row:
        raise D15NotFound(f"Outbox event {event_id} not found")
    outbox = _row_to_dict(row)
    action_row = _conn_exec(
        conn,
        "SELECT * FROM d10_business_actions WHERE business_action_id=?",
        (outbox["business_action_id"],),
    ).fetchone()
    if not action_row:
        raise D15StateError("Outbox event exists without its BusinessAction")
    return outbox, _row_to_dict(action_row)


def _state_row(conn: Any, event_id: str) -> dict[str, Any] | None:
    row = _conn_exec(
        conn,
        "SELECT * FROM d15_outbox_execution_state WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _next_trace_seq(conn: Any, event_id: str) -> int:
    row = _conn_exec(
        conn,
        "SELECT COALESCE(MAX(sequence_no),0)+1 AS seq FROM d15_execution_trace_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return int(_row_to_dict(row).get("seq") or 1)


def _record_trace(
    conn: Any,
    *,
    event_id: str,
    organization_id: str,
    event_type: str,
    state: str,
    request_id: str,
    idempotency_key: str,
    attempt: int,
    dispatch_started: bool,
    result_known: bool,
    external_effect_status: str,
    error_kind: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor: str | None = None,
) -> None:
    _conn_exec(
        conn,
        """INSERT INTO d15_execution_trace_events
           (trace_id,event_id,organization_id,sequence_no,event_type,state,error_kind,
            request_id,idempotency_key,attempt,dispatch_started,result_known,
            external_effect_status,response_meta_json,actor,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("D15TR"),
            event_id,
            organization_id,
            _next_trace_seq(conn, event_id),
            _safe_code(event_type, fallback="D15_EVENT"),
            state,
            _safe_code(error_kind, fallback="") or None,
            request_id,
            idempotency_key,
            int(attempt),
            _bool_int(dispatch_started),
            _bool_int(result_known),
            external_effect_status,
            _canonical_json(_safe_metadata(metadata)),
            actor,
            _now_iso(),
        ),
    )


def _initial_state(conn: Any, event_id: str, *, retry_budget: int = DEFAULT_RETRY_BUDGET) -> dict[str, Any]:
    _ensure_tables(conn)
    existing = _state_row(conn, event_id)
    if existing:
        return existing
    outbox, action = _load_outbox(conn, event_id)
    now = _now_iso()
    budget = max(1, int(retry_budget or DEFAULT_RETRY_BUDGET))
    _conn_exec(
        conn,
        """INSERT INTO d15_outbox_execution_state
           (event_id,organization_id,business_action_id,request_id,idempotency_key,state,
            retry_budget,attempt_count,dispatch_started,result_known,external_effect_status,
            error_kind,user_message_code,reconciliation_status,next_attempt_at,last_attempt_at,
            resolved_at,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            outbox["organization_id"],
            outbox["business_action_id"],
            action["request_id"],
            action["idempotency_key"],
            STATE_PENDING,
            budget,
            int(outbox.get("attempt_count") or 0),
            0,
            0,
            EFFECT_UNKNOWN,
            None,
            UI_MESSAGES[STATE_PENDING]["code"],
            None,
            None,
            None,
            None,
            now,
            now,
        ),
    )
    _record_trace(
        conn,
        event_id=event_id,
        organization_id=outbox["organization_id"],
        event_type="EXECUTION_STATE_CREATED",
        state=STATE_PENDING,
        request_id=action["request_id"],
        idempotency_key=action["idempotency_key"],
        attempt=int(outbox.get("attempt_count") or 0),
        dispatch_started=False,
        result_known=False,
        external_effect_status=EFFECT_UNKNOWN,
        metadata={"adapter": "UNBOUND"},
        actor="SYSTEM",
    )
    conn.commit()
    return _state_row(conn, event_id) or {}


def ensure_execution_state(conn: Any, event_id: str, *, retry_budget: int = DEFAULT_RETRY_BUDGET) -> dict[str, Any]:
    """Create/read the D15 state overlay for a D10 Outbox event."""
    return _initial_state(conn, event_id, retry_budget=retry_budget)


def _set_state(
    conn: Any,
    *,
    state_row: dict[str, Any],
    state: str,
    dispatch_started: bool,
    result_known: bool,
    external_effect_executed: bool | None,
    error_kind: str | None,
    next_attempt_at: str | None = None,
    reconciliation_status: str | None = None,
    resolved: bool = False,
    metadata: dict[str, Any] | None = None,
    actor: str = "SYSTEM",
    event_type: str = "EXECUTION_STATE_CHANGED",
) -> dict[str, Any]:
    event_id = state_row["event_id"]
    now = _now_iso()
    effect_status = _effect_status(external_effect_executed)
    message = UI_MESSAGES[state]
    _conn_exec(
        conn,
        """UPDATE d15_outbox_execution_state
           SET state=?,dispatch_started=?,result_known=?,external_effect_status=?,error_kind=?,
               user_message_code=?,reconciliation_status=?,next_attempt_at=?,
               resolved_at=?,updated_at=?
           WHERE event_id=?""",
        (
            state,
            _bool_int(dispatch_started),
            _bool_int(result_known),
            effect_status,
            _safe_code(error_kind, fallback="") or None,
            message["code"],
            reconciliation_status,
            next_attempt_at,
            now if resolved else None,
            now,
            event_id,
        ),
    )
    # D10 remains the durable-intent owner. D15 only advances transport state.
    outbox_status = {
        STATE_PENDING: "PENDING",
        STATE_IN_FLIGHT: "IN_FLIGHT",
        STATE_RETRYABLE: "RETRYABLE",
        STATE_FAILED_SAFE: "FAILED_SAFE",
        STATE_RESULT_UNCERTAIN: "RESULT_UNCERTAIN",
        STATE_HUMAN_REQUIRED: "HUMAN_REQUIRED",
        STATE_SUCCESS: "PUBLISHED",
    }[state]
    _conn_exec(
        conn,
        """UPDATE d10_outbox_events
           SET status=?,next_attempt_at=?,published_at=?,last_error=?,updated_at=?
           WHERE event_id=?""",
        (
            outbox_status,
            next_attempt_at,
            now if state == STATE_SUCCESS else None,
            _safe_code(error_kind, fallback="") or None,
            now,
            event_id,
        ),
    )
    fresh = _state_row(conn, event_id) or state_row
    _record_trace(
        conn,
        event_id=event_id,
        organization_id=fresh["organization_id"],
        event_type=event_type,
        state=state,
        request_id=fresh["request_id"],
        idempotency_key=fresh["idempotency_key"],
        attempt=int(fresh.get("attempt_count") or 0),
        dispatch_started=dispatch_started,
        result_known=result_known,
        external_effect_status=effect_status,
        error_kind=error_kind,
        metadata=metadata,
        actor=actor,
    )
    conn.commit()
    return _state_row(conn, event_id) or fresh


def recover_inflight_as_uncertain(conn: Any, event_id: str, *, actor: str = "SYSTEM_RECOVERY") -> dict[str, Any]:
    """Crash recovery: an orphaned IN_FLIGHT attempt is ambiguous, never retried."""
    state = _initial_state(conn, event_id)
    if state["state"] != STATE_IN_FLIGHT:
        return state
    return _set_state(
        conn,
        state_row=state,
        state=STATE_RESULT_UNCERTAIN,
        dispatch_started=True,
        result_known=False,
        external_effect_executed=None,
        error_kind="ORPHANED_IN_FLIGHT",
        resolved=False,
        metadata={"result_source": "crash_recovery"},
        actor=actor,
        event_type="IN_FLIGHT_RECOVERED_AS_UNCERTAIN",
    )


def process_outbox_event(
    conn: Any,
    *,
    event_id: str,
    adapter: ExternalAdapter,
    retry_budget: int = DEFAULT_RETRY_BUDGET,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Dispatch one D10 Outbox event under D15 durable-execution rules.

    The function is deliberately single-event and adapter-injected. There is no
    live ERP/email writer in the D14.2 baseline; D15 establishes the safe worker
    contract without pretending an external write adapter already exists.
    """
    _ensure_tables(conn)
    state = _initial_state(conn, event_id, retry_budget=retry_budget)

    # A process crash after the durable IN_FLIGHT commit leaves an ambiguous
    # attempt. Never dispatch it again automatically.
    if state["state"] == STATE_IN_FLIGHT:
        return recover_inflight_as_uncertain(conn, event_id)

    if state["state"] not in AUTO_DISPATCHABLE_STATES:
        return serialize_execution_state(state)

    current_dt = (now_fn or (lambda: datetime.now(CN_TZ)))()
    if state["state"] == STATE_RETRYABLE and state.get("next_attempt_at"):
        try:
            due = datetime.fromisoformat(str(state["next_attempt_at"]).replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=CN_TZ)
            if current_dt < due.astimezone(CN_TZ):
                return serialize_execution_state(state)
        except ValueError:
            # Bad retry timestamps fail closed into human handling instead of
            # creating an unbounded hot retry loop.
            state = _set_state(
                conn, state_row=state, state=STATE_HUMAN_REQUIRED,
                dispatch_started=False, result_known=True, external_effect_executed=False,
                error_kind="INVALID_RETRY_SCHEDULE", resolved=False, actor="SYSTEM",
                event_type="INVALID_RETRY_SCHEDULE",
            )
            return serialize_execution_state(state)

    attempt_count = int(state.get("attempt_count") or 0)
    budget = int(state.get("retry_budget") or retry_budget or DEFAULT_RETRY_BUDGET)
    if attempt_count >= budget:
        state = _set_state(
            conn,
            state_row=state,
            state=STATE_HUMAN_REQUIRED,
            dispatch_started=False,
            result_known=True,
            external_effect_executed=False,
            error_kind="RETRY_BUDGET_EXHAUSTED",
            resolved=False,
            actor="SYSTEM",
            event_type="RETRY_BUDGET_EXHAUSTED",
        )
        return serialize_execution_state(state)

    outbox, _action = _load_outbox(conn, event_id)
    payload = json.loads(outbox.get("payload_json") or "{}")
    new_attempt = attempt_count + 1
    now = current_dt
    now_iso = now.isoformat(timespec="seconds")

    # Durable intent-before-I/O. If the process dies after this commit, the next
    # worker observes IN_FLIGHT and converts it to RESULT_UNCERTAIN.
    _conn_exec(
        conn,
        """UPDATE d15_outbox_execution_state
           SET state=?,attempt_count=?,dispatch_started=1,result_known=0,
               external_effect_status=?,error_kind=NULL,user_message_code=?,
               last_attempt_at=?,next_attempt_at=NULL,updated_at=? WHERE event_id=?""",
        (
            STATE_IN_FLIGHT,
            new_attempt,
            EFFECT_UNKNOWN,
            UI_MESSAGES[STATE_IN_FLIGHT]["code"],
            now_iso,
            now_iso,
            event_id,
        ),
    )
    _conn_exec(
        conn,
        "UPDATE d10_outbox_events SET status='IN_FLIGHT',attempt_count=?,next_attempt_at=NULL,last_error=NULL,updated_at=? WHERE event_id=?",
        (new_attempt, now_iso, event_id),
    )
    fresh = _state_row(conn, event_id) or state
    _record_trace(
        conn,
        event_id=event_id,
        organization_id=fresh["organization_id"],
        event_type="DISPATCH_STARTED",
        state=STATE_IN_FLIGHT,
        request_id=fresh["request_id"],
        idempotency_key=fresh["idempotency_key"],
        attempt=new_attempt,
        dispatch_started=True,
        result_known=False,
        external_effect_status=EFFECT_UNKNOWN,
        actor="SYSTEM",
    )
    conn.commit()

    try:
        receipt = adapter.dispatch(
            payload,
            idempotency_key=fresh["idempotency_key"],
            request_id=fresh["request_id"],
        )
        if not isinstance(receipt, DispatchReceipt):
            raise D15ResultUncertain(error_kind="ADAPTER_INVALID_RECEIPT")
        meta = _safe_metadata({**dict(receipt.metadata or {}), "external_reference": receipt.external_reference})
        state = _set_state(
            conn,
            state_row=_state_row(conn, event_id) or fresh,
            state=STATE_SUCCESS,
            dispatch_started=True,
            result_known=True,
            external_effect_executed=True,
            error_kind=None,
            resolved=True,
            metadata=meta,
            actor="SYSTEM",
            event_type="DISPATCH_CONFIRMED_SUCCESS",
        )
        return serialize_execution_state(state)
    except D15RetryableNoEffect as exc:
        current = _state_row(conn, event_id) or fresh
        attempts = int(current.get("attempt_count") or new_attempt)
        budget = int(current.get("retry_budget") or retry_budget or DEFAULT_RETRY_BUDGET)
        if attempts >= budget:
            state_name = STATE_HUMAN_REQUIRED
            next_attempt = None
            event_type = "RETRY_BUDGET_EXHAUSTED"
        else:
            state_name = STATE_RETRYABLE
            delay_seconds = min(300, 5 * (2 ** max(0, attempts - 1)))
            next_attempt = (now + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")
            event_type = "DISPATCH_RETRYABLE_NO_EFFECT"
        state = _set_state(
            conn,
            state_row=current,
            state=state_name,
            dispatch_started=False,
            result_known=True,
            external_effect_executed=False,
            error_kind=exc.error_kind,
            next_attempt_at=next_attempt,
            resolved=False,
            metadata={"no_effect_confirmed": True},
            actor="SYSTEM",
            event_type=event_type,
        )
        return serialize_execution_state(state)
    except D15FailedSafe as exc:
        state = _set_state(
            conn,
            state_row=_state_row(conn, event_id) or fresh,
            state=STATE_FAILED_SAFE,
            dispatch_started=False,
            result_known=True,
            external_effect_executed=False,
            error_kind=exc.error_kind,
            resolved=False,
            metadata={"no_effect_confirmed": True},
            actor="SYSTEM",
            event_type="DISPATCH_FAILED_SAFE",
        )
        return serialize_execution_state(state)
    except D15HumanRequired as exc:
        state = _set_state(
            conn,
            state_row=_state_row(conn, event_id) or fresh,
            state=STATE_HUMAN_REQUIRED,
            dispatch_started=False,
            result_known=True,
            external_effect_executed=False,
            error_kind=exc.error_kind,
            resolved=False,
            actor="SYSTEM",
            event_type="DISPATCH_HUMAN_REQUIRED",
        )
        return serialize_execution_state(state)
    except D15ResultUncertain as exc:
        state = _set_state(
            conn,
            state_row=_state_row(conn, event_id) or fresh,
            state=STATE_RESULT_UNCERTAIN,
            dispatch_started=True,
            result_known=False,
            external_effect_executed=None,
            error_kind=exc.error_kind,
            resolved=False,
            actor="SYSTEM",
            event_type="DISPATCH_RESULT_UNCERTAIN",
        )
        return serialize_execution_state(state)
    except Exception:
        # Unknown adapter exceptions are treated as ambiguous once the durable
        # dispatch boundary was entered. Never persist str(exc); it may contain
        # credentials or raw provider payloads.
        state = _set_state(
            conn,
            state_row=_state_row(conn, event_id) or fresh,
            state=STATE_RESULT_UNCERTAIN,
            dispatch_started=True,
            result_known=False,
            external_effect_executed=None,
            error_kind="ADAPTER_EXCEPTION_UNCERTAIN",
            resolved=False,
            actor="SYSTEM",
            event_type="DISPATCH_RESULT_UNCERTAIN",
        )
        return serialize_execution_state(state)


def reconcile_outbox_event(
    conn: Any,
    *,
    event_id: str,
    result: str,
    actor: str,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Resolve RESULT_UNCERTAIN using explicit external/human reconciliation."""
    state = _initial_state(conn, event_id)
    if state["state"] not in {STATE_RESULT_UNCERTAIN, STATE_HUMAN_REQUIRED}:
        raise D15StateError("Reconciliation is only valid for RESULT_UNCERTAIN/HUMAN_REQUIRED events")
    decision = str(result or "").strip().upper()
    metadata = {"reconciliation_ref": str(evidence_ref or "")[:240], "result_source": "human_reconciliation"}
    if decision == "SUCCESS":
        state = _set_state(
            conn,
            state_row=state,
            state=STATE_SUCCESS,
            dispatch_started=True,
            result_known=True,
            external_effect_executed=True,
            error_kind=None,
            reconciliation_status="CONFIRMED_SUCCESS",
            resolved=True,
            metadata=metadata,
            actor=actor,
            event_type="RECONCILIATION_CONFIRMED_SUCCESS",
        )
    elif decision == "NOT_EXECUTED":
        state = _set_state(
            conn,
            state_row=state,
            state=STATE_FAILED_SAFE,
            dispatch_started=True,
            result_known=True,
            external_effect_executed=False,
            error_kind="RECONCILED_NOT_EXECUTED",
            reconciliation_status="CONFIRMED_NOT_EXECUTED",
            resolved=False,
            metadata=metadata,
            actor=actor,
            event_type="RECONCILIATION_CONFIRMED_NOT_EXECUTED",
        )
    elif decision == "UNKNOWN":
        state = _set_state(
            conn,
            state_row=state,
            state=STATE_HUMAN_REQUIRED,
            dispatch_started=True,
            result_known=False,
            external_effect_executed=None,
            error_kind="RECONCILIATION_STILL_UNKNOWN",
            reconciliation_status="UNRESOLVED",
            resolved=False,
            metadata=metadata,
            actor=actor,
            event_type="RECONCILIATION_UNRESOLVED",
        )
    else:
        raise D15StateError("result must be SUCCESS, NOT_EXECUTED, or UNKNOWN")
    return serialize_execution_state(state)


def requeue_after_confirmed_no_effect(conn: Any, *, event_id: str, actor: str) -> dict[str, Any]:
    """Explicit human requeue after reconciliation proved no side effect occurred."""
    state = _initial_state(conn, event_id)
    if state["state"] != STATE_FAILED_SAFE or state.get("reconciliation_status") != "CONFIRMED_NOT_EXECUTED":
        raise D15StateError("Only a reconciled CONFIRMED_NOT_EXECUTED event can be requeued")
    if int(state.get("attempt_count") or 0) >= int(state.get("retry_budget") or DEFAULT_RETRY_BUDGET):
        state = _set_state(
            conn,
            state_row=state,
            state=STATE_HUMAN_REQUIRED,
            dispatch_started=False,
            result_known=True,
            external_effect_executed=False,
            error_kind="RETRY_BUDGET_EXHAUSTED",
            resolved=False,
            actor=actor,
            event_type="MANUAL_REQUEUE_BLOCKED_BUDGET",
        )
        return serialize_execution_state(state)
    state = _set_state(
        conn,
        state_row=state,
        state=STATE_PENDING,
        dispatch_started=False,
        result_known=True,
        external_effect_executed=False,
        error_kind=None,
        next_attempt_at=None,
        reconciliation_status="CONFIRMED_NOT_EXECUTED",
        resolved=False,
        metadata={"result_source": "explicit_human_requeue"},
        actor=actor,
        event_type="MANUAL_REQUEUE_AFTER_RECONCILIATION",
    )
    return serialize_execution_state(state)


def get_execution_status(conn: Any, event_id: str, *, create: bool = True) -> dict[str, Any] | None:
    _ensure_tables(conn)
    state = _state_row(conn, event_id)
    if not state and create:
        state = _initial_state(conn, event_id)
    if not state:
        return None
    return serialize_execution_state(state)


def list_execution_trace(conn: Any, event_id: str) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    rows = _conn_exec(
        conn,
        "SELECT * FROM d15_execution_trace_events WHERE event_id=? ORDER BY sequence_no ASC",
        (event_id,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        try:
            item["response_meta"] = json.loads(item.get("response_meta_json") or "{}")
        except Exception:
            item["response_meta"] = {}
        items.append(item)
    return items


def serialize_execution_state(state: dict[str, Any]) -> dict[str, Any]:
    name = str(state.get("state") or STATE_PENDING)
    ui = dict(UI_MESSAGES.get(name, UI_MESSAGES[STATE_HUMAN_REQUIRED]))
    return {
        "policy_version": D15_POLICY_VERSION,
        "event_id": state.get("event_id"),
        "organization_id": state.get("organization_id"),
        "business_action_id": state.get("business_action_id"),
        "request_id": state.get("request_id"),
        "idempotency_key": state.get("idempotency_key"),
        "state": name,
        "retry_budget": int(state.get("retry_budget") or DEFAULT_RETRY_BUDGET),
        "attempt_count": int(state.get("attempt_count") or 0),
        "dispatch_started": bool(state.get("dispatch_started")),
        "result_known": bool(state.get("result_known")),
        "external_effect_status": state.get("external_effect_status") or EFFECT_UNKNOWN,
        "error_kind": state.get("error_kind"),
        "reconciliation_status": state.get("reconciliation_status"),
        "next_attempt_at": state.get("next_attempt_at"),
        "last_attempt_at": state.get("last_attempt_at"),
        "resolved_at": state.get("resolved_at"),
        "ui": ui,
        "auto_retry_allowed": bool(ui.get("auto_retry_allowed")) and name == STATE_RETRYABLE,
    }


def failure_contract() -> dict[str, Any]:
    return {
        "policy_version": D15_POLICY_VERSION,
        "states": {
            state: dict(UI_MESSAGES[state])
            for state in (
                STATE_SUCCESS,
                STATE_FAILED_SAFE,
                STATE_RETRYABLE,
                STATE_RESULT_UNCERTAIN,
                STATE_HUMAN_REQUIRED,
            )
        },
        "invariants": [
            "RESULT_UNCERTAIN is never SUCCESS.",
            "A request that may have produced an external side effect is never automatically retried while its result is unknown.",
            "Only explicit no-effect evidence permits automatic retry, and retry is budgeted.",
            "D10 BusinessAction.ACCEPTED remains durable intent, not proof of external execution.",
            "D12 Human Review / authorization boundaries remain authoritative during failure recovery.",
            "Raw adapter/provider exception text is never stored in D15 business trace.",
        ],
        "scope": {
            "mcp_required": False,
            "redis_required": False,
            "external_write_adapter_present": False,
            "note": "D15 defines the worker contract; no live ERP/email write adapter is claimed in the D14.2 baseline.",
        },
    }
