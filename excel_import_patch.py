from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from analytics import ensure_analytics_schema, track_event
from database import db, table_exists, get_table_columns, get_table_column_names, begin_transaction
from auth import CurrentIdentity, get_current_identity, require_manager, require_same_org

PATCH_VERSION = "4.0.0-d4-contract"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ROWS = 500
MAX_COLUMNS = 80

CANONICAL_FIELDS: dict[str, dict[str, Any]] = {
    # Order identity
    "source_order_key": {"label": "订单号/PO号/销售订单号", "required": True, "aliases": ["订单号", "订单编号", "po", "po no", "po_no", "po number", "pono", "order no", "order_no", "orderno", "销售订单号", "销售订单"]},
    "source_line_key": {"label": "行号/明细号", "aliases": ["行号", "明细号", "line no", "line_no", "line number", "item line"]},
    # Core order facts
    "customer_name": {"label": "客户名称", "required": True, "aliases": ["客户", "客户名称", "customer", "customer name", "customer_name", "buyer"]},
    "delivery_date": {"label": "交期", "required": True, "aliases": ["客户交期", "客户正式交期", "正式交期", "交货日期", "交期", "delivery date", "delivery_date", "customer delivery date", "customer_delivery_date"]},
    "owner": {"label": "负责人", "required": True, "aliases": ["负责人", "跟单员", "owner", "assignee"]},
    # Product and quantity (optional)
    "product_name": {"label": "产品名称", "warning": True, "aliases": ["产品", "产品名称", "品名", "product", "product name", "product_name", "item"]},
    "order_qty": {"label": "订单数量", "warning": True, "aliases": ["数量", "qty", "quantity", "order qty", "order_qty", "订单数量"]},
    "order_status": {"label": "订单状态", "aliases": ["订单状态", "状态", "order status", "status"]},
    # Delivery facts (optional)
    "completed_qty": {"label": "已完成数量", "warning": True, "aliases": ["已完成数量", "完成数量", "completed qty", "completed_qty", "completed quantity"]},
    "planned_completion_date": {"label": "计划完成日期", "warning": True, "aliases": ["计划完成日期", "计划完成", "planned completion date", "planned_completion_date", "planned finish"]},
    "supplier_commitment_date": {"label": "供应商承诺日期", "warning": True, "aliases": ["供应商承诺日期", "工厂承诺", "supplier commitment", "supplier_commitment_date", "factory commitment"]},
    # Context (optional)
    "supplier_name": {"label": "供应商名称", "aliases": ["供应商", "供应商名称", "工厂", "工厂名称", "supplier", "supplier name", "factory", "factory_name"]},
    "notes": {"label": "备注", "aliases": ["备注", "note", "notes", "remark", "remarks", "memo"]},
}

ORDER_COLUMN_ALIASES: dict[str, list[str]] = {
    "order_id": ["order_id", "id"],
    "order_no": ["order_no", "source_order_key", "po_no", "po_number"],
    "customer_name": ["customer_name", "customer"],
    "product_name": ["product_name", "product"],
    "quantity": ["quantity", "order_qty", "qty"],
    "customer_delivery_date": ["customer_delivery_date", "delivery_date", "requested_delivery_date"],
    "owner": ["owner", "assignee"],
    "supplier_name": ["supplier_name", "factory_name", "factory"],
    "latest_supplier_commitment": ["latest_supplier_commitment", "supplier_commitment_date"],
    "completed_qty": ["completed_qty"],
    "planned_completion_date": ["planned_completion_date"],
    "order_status": ["order_status", "status"],
    "notes": ["notes", "remark", "memo"],
    "source_system": ["source_system"],
    "source_order_key": ["source_order_key"],
    "source_line_key": ["source_line_key"],
    "status": ["status"],
    "risk_level": ["risk_level"],
    "created_at": ["created_at"],
    "updated_at": ["updated_at"],
}

SQL_TYPE_BY_FIELD = {
    "order_no": "TEXT",
    "source_order_key": "TEXT",
    "source_line_key": "TEXT",
    "source_system": "TEXT",
    "customer_name": "TEXT",
    "product_name": "TEXT",
    "quantity": "REAL",
    "order_qty": "REAL",
    "customer_delivery_date": "TEXT",
    "delivery_date": "TEXT",
    "owner": "TEXT",
    "supplier_name": "TEXT",
    "latest_supplier_commitment": "TEXT",
    "supplier_commitment_date": "TEXT",
    "completed_qty": "REAL",
    "planned_completion_date": "TEXT",
    "order_status": "TEXT",
    "notes": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}

ISSUE_CODES = {
    "REQUIRED_FIELD_MISSING": "IMPORT_REQUIRED_FIELD_MISSING",
    "INVALID_DATE": "IMPORT_INVALID_DATE",
    "INVALID_NUMBER": "IMPORT_INVALID_NUMBER",
    "NEGATIVE_NUMBER": "IMPORT_NEGATIVE_NUMBER",
    "MISSING_RISK_CONTEXT": "IMPORT_MISSING_RISK_CONTEXT",
    "COMMIT_FAILED": "IMPORT_COMMIT_FAILED",
    "OWNER_MISSING": "IMPORT_OWNER_MISSING",
    "OWNER_UNRESOLVED": "IMPORT_OWNER_UNRESOLVED",
    "UNRESOLVED_CONFLICT": "UNRESOLVED_IMPORT_CONFLICT",
}

CLASSIFICATION = {
    "NEW": "NEW",
    "DUPLICATE_NOOP": "DUPLICATE_NOOP",
    "CONFLICT_EXISTING": "CONFLICT_EXISTING",
    "CONFLICT_IN_BATCH": "CONFLICT_IN_BATCH",
    "LINE_CONFLICT": "LINE_CONFLICT",
    "LINE_IDENTITY_AMBIGUOUS": "LINE_IDENTITY_AMBIGUOUS",
    "ERROR": "ERROR",
}

ORDER_LEVEL_FIELDS = {"customer_name", "owner", "supplier_name", "order_status", "source_system", "customer_delivery_date", "delivery_date"}

INTRA_BATCH_ORDER_LEVEL_FIELDS = {"customer_name", "owner", "supplier_name", "order_status"}

LINE_LEVEL_FIELDS = {"source_line_key", "product_name", "order_qty", "quantity",
                     "completed_qty", "notes", "planned_completion_date",
                     "supplier_commitment_date", "latest_supplier_commitment"}

LINE_FACTS_FIELDS = {"product_name", "order_qty", "completed_qty", "notes"}


class PreviewRequest(BaseModel):
    filename: str
    content_base64: str
    mapping: dict[str, str] | None = None


class CommitRequest(BaseModel):
    batch_id: str
    import_key: str | None = None
    current_user_id: str = "USER-1"
    row_actions: dict[str, str] = Field(default_factory=dict)
    order_actions: dict[str, str] = Field(default_factory=dict)
    client_end_to_end_duration_ms: int | None = None
    projection_hash: str


class ClientMetricsRequest(BaseModel):
    client_end_to_end_duration_ms: int | None = None


OPERATOR_NAME_TO_ID = {
    "李梅": "USER-1",
    "王晓": "USER-2",
    "陈琳": "USER-3",
    "周主管": "MANAGER-1",
}
VALID_OPERATOR_IDS = set(OPERATOR_NAME_TO_ID.values())


def _normalize_owner(value: Any, fallback_user_id: str | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text in VALID_OPERATOR_IDS:
        return text
    if text in OPERATOR_NAME_TO_ID:
        return OPERATOR_NAME_TO_ID[text]
    if text and text not in {"待分配", "未分配", "-", "—"}:
        return text
    return None


def _resolve_owner_for_import(value: Any) -> tuple[str | None, str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, ISSUE_CODES["OWNER_MISSING"], "负责人缺失"
    if text in VALID_OPERATOR_IDS:
        return text, None, None
    if text in OPERATOR_NAME_TO_ID:
        return OPERATOR_NAME_TO_ID[text], None, None
    if text and text not in {"待分配", "未分配", "-", "—"}:
        return None, ISSUE_CODES["OWNER_UNRESOLVED"], f"负责人'{text}'无法映射到有效系统用户"
    return None, ISSUE_CODES["OWNER_MISSING"], "负责人缺失"

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_column(conn: Any, table: str, column: str, col_type: str) -> None:
    """Add a column to a table if it does not exist (SQLite and PG compatible)."""
    columns = get_table_column_names(conn, table)
    if column not in columns:
        if getattr(conn, "is_pg", False):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type}')
        else:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')


def _resolve_column(existing: Iterable[str], canonical: str) -> str | None:
    names = set(existing)
    for alias in ORDER_COLUMN_ALIASES.get(canonical, [canonical]):
        if alias in names:
            return alias
    return None


def _ensure_patch_schema(conn: Any) -> None:
    ensure_analytics_schema(conn)
    if getattr(conn, "is_pg", False):
        required = ("orders", "order_import_batches", "order_import_rows", "order_lines")
        missing = [name for name in required if not table_exists(conn, name)]
        if missing:
            raise RuntimeError(f"PostgreSQL import schema missing {missing}; run `alembic upgrade head`.")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS order_import_batches (
            batch_id TEXT PRIMARY KEY,
            source_filename TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            importable_rows INTEGER NOT NULL DEFAULT 0,
            error_rows INTEGER NOT NULL DEFAULT 0,
            mapping_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            started_at TEXT,
            preflight_completed_at TEXT,
            commit_completed_at TEXT,
            preflight_duration_ms INTEGER,
            commit_duration_ms INTEGER,
            end_to_end_duration_ms INTEGER,
            processing_duration_ms INTEGER,
            projection_hash TEXT,
            warning_count INTEGER NOT NULL DEFAULT 0,
            block_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS order_import_rows (
            row_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            classification TEXT NOT NULL,
            issues_json TEXT NOT NULL,
            changes_json TEXT NOT NULL,
            existing_order_id TEXT,
            commit_status TEXT,
            commit_message TEXT,
            FOREIGN KEY(batch_id) REFERENCES order_import_batches(batch_id)
        );
        CREATE TABLE IF NOT EXISTS order_lines (
            line_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            source_system TEXT NOT NULL DEFAULT 'excel_import',
            source_order_key TEXT,
            source_line_key TEXT,
            product_name TEXT,
            order_qty REAL,
            completed_qty REAL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE INDEX IF NOT EXISTS idx_order_import_rows_batch ON order_import_rows(batch_id);
        CREATE INDEX IF NOT EXISTS idx_order_import_rows_classification ON order_import_rows(classification);
        CREATE INDEX IF NOT EXISTS idx_order_lines_order ON order_lines(order_id);
        CREATE INDEX IF NOT EXISTS idx_order_lines_source ON order_lines(source_order_key);
        """
    )
    _ensure_column(conn, "order_import_batches", "started_at", "TEXT")
    _ensure_column(conn, "order_import_batches", "preflight_completed_at", "TEXT")
    _ensure_column(conn, "order_import_batches", "commit_completed_at", "TEXT")
    _ensure_column(conn, "order_import_batches", "preflight_duration_ms", "INTEGER")
    _ensure_column(conn, "order_import_batches", "commit_duration_ms", "INTEGER")
    _ensure_column(conn, "order_import_batches", "end_to_end_duration_ms", "INTEGER")
    _ensure_column(conn, "order_import_batches", "processing_duration_ms", "INTEGER")
    _ensure_column(conn, "order_import_batches", "projection_hash", "TEXT")
    _ensure_column(conn, "order_import_batches", "warning_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "block_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "success_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "success_with_warning_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "commit_failed_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "retry_of_batch_id", "TEXT")
    _ensure_column(conn, "order_import_batches", "retry_attempt", "INTEGER")
    _ensure_column(conn, "order_import_batches", "duplicate_noop_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "conflict_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "corrected_count", "INTEGER")
    _ensure_column(conn, "order_import_batches", "source_file_name", "TEXT")
    _ensure_column(conn, "order_import_batches", "source_file_size", "INTEGER")
    _ensure_column(conn, "order_import_batches", "file_sha256", "TEXT")
    _ensure_column(conn, "order_import_batches", "has_header", "INTEGER")
    _ensure_column(conn, "order_import_batches", "start_row", "INTEGER")
    _ensure_column(conn, "order_import_batches", "organization_id", "TEXT")
    _ensure_column(conn, "order_import_batches", "created_by", "TEXT")
    _ensure_column(conn, "order_import_rows", "source_system", "TEXT")
    _ensure_column(conn, "order_import_rows", "source_order_key", "TEXT")
    _ensure_column(conn, "order_import_rows", "source_line_key", "TEXT")
    _ensure_column(conn, "order_import_rows", "conflict_type", "TEXT")
    _ensure_column(conn, "order_import_rows", "conflict_details_json", "TEXT")
    _ensure_column(conn, "order_import_rows", "order_action", "TEXT")
    _ensure_column(conn, "order_import_rows", "existing_line_id", "TEXT")
    if not table_exists(conn, "order_corrections"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_corrections (
                correction_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                source_order_key TEXT,
                batch_id TEXT,
                actor_user_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                changes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_corrections_order ON order_corrections(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_corrections_batch ON order_corrections(batch_id)")
    if not table_exists(conn, "orders"):
        conn.execute(
            """
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_no TEXT,
                source_order_key TEXT,
                source_line_key TEXT,
                source_system TEXT DEFAULT 'excel_import',
                customer_name TEXT NOT NULL,
                product_name TEXT,
                order_qty REAL,
                quantity REAL,
                order_status TEXT,
                completed_qty REAL,
                planned_completion_date TEXT,
                supplier_commitment_date TEXT,
                supplier_name TEXT,
                notes TEXT,
                delivery_date TEXT,
                customer_delivery_date TEXT,
                owner TEXT,
                status TEXT DEFAULT 'ACTIVE',
                risk_level TEXT DEFAULT 'low',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    existing = get_table_column_names(conn, "orders")
    for canonical, sql_type in SQL_TYPE_BY_FIELD.items():
        if _resolve_column(existing, canonical) is None:
            conn.execute(f'ALTER TABLE orders ADD COLUMN "{canonical}" {sql_type}')
            existing.add(canonical)
    activation_columns = {
        "action_readiness": "TEXT NOT NULL DEFAULT 'BASE_ONLY'",
        "contact_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "issue_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "initialization_waiting_on": "TEXT",
        "initialization_promised_reply_at": "TEXT",
        "initialization_note": "TEXT",
        "initialization_source": "TEXT",
        "initialized_at": "TEXT",
        "last_dynamic_update_at": "TEXT",
    }
    for column, definition in activation_columns.items():
        if column not in existing:
            conn.execute(f'ALTER TABLE orders ADD COLUMN "{column}" {definition}')
            existing.add(column)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_action_readiness ON orders(action_readiness, requested_delivery_date)")
    conn.commit()


def _normal_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-./\\()（）:：]+", "", text)


def _auto_mapping(headers: list[str]) -> dict[str, str]:
    alias_index: dict[str, str] = {}
    for canonical, config in CANONICAL_FIELDS.items():
        for alias in config.get("aliases", []):
            alias_index[_normal_header(alias)] = canonical
        # The official import templates use the human-readable field label as
        # their header. Treat that label as a first-class alias so a template
        # downloaded from FlowOrder can be uploaded back without remapping.
        label = config.get("label")
        if label:
            alias_index[_normal_header(label)] = canonical
        alias_index[_normal_header(canonical)] = canonical
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for header in headers:
        canonical = alias_index.get(_normal_header(header))
        if canonical and canonical not in used:
            mapping[header] = canonical
            used.add(canonical)
    return mapping


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        return 0
    result = 0
    for char in letters.group(0):
        result = result * 26 + (ord(char) - 64)
    return result - 1


def _read_csv(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    decoded = None
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("CSV编码无法识别，请另存为UTF-8 CSV后重试")
    sample = decoded[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(decoded), dialect)
    rows = list(reader)
    return _matrix_to_records(rows)


def _read_xlsx(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        total_uncompressed = sum(info.file_size for info in archive.infolist())
        if total_uncompressed > MAX_XLSX_UNCOMPRESSED:
            raise ValueError("Excel解压后内容过大，请拆分文件")
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("不是有效的.xlsx文件")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", ns)))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        first_sheet = workbook.find(f"{{{ns_main}}}sheets/{{{ns_main}}}sheet")
        if first_sheet is None:
            raise ValueError("Excel中没有工作表")
        rel_id = first_sheet.attrib.get(f"{{{ns_rel}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rels:
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target")
                break
        if not target:
            raise ValueError("无法定位Excel工作表")
        normalized_target = target.lstrip("/")
        sheet_path = normalized_target if normalized_target.startswith("xl/") else "xl/" + normalized_target
        sheet_path = str(Path(sheet_path).as_posix())
        sheet = ET.fromstring(archive.read(sheet_path))
        matrix: list[list[Any]] = []
        for row in sheet.findall(f".//{{{ns_main}}}sheetData/{{{ns_main}}}row"):
            values: dict[int, Any] = {}
            for cell in row.findall(f"{{{ns_main}}}c"):
                ref = cell.attrib.get("r", "A1")
                index = _column_index(ref)
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{ns_main}}}v")
                inline_node = cell.find(f"{{{ns_main}}}is")
                value: Any = ""
                if cell_type == "inlineStr" and inline_node is not None:
                    value = "".join(node.text or "" for node in inline_node.findall(f".//{{{ns_main}}}t"))
                elif value_node is not None:
                    raw = value_node.text or ""
                    if cell_type == "s":
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = raw
                    elif cell_type == "b":
                        value = raw == "1"
                    else:
                        value = raw
                values[index] = value
            if values:
                width = min(max(values) + 1, MAX_COLUMNS)
                matrix.append([values.get(i, "") for i in range(width)])
        return _matrix_to_records(matrix)


def _matrix_to_records(matrix: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    nonempty = [row for row in matrix if any(str(value or "").strip() for value in row)]
    if not nonempty:
        raise ValueError("文件中没有可读取的数据")
    raw_headers = nonempty[0][:MAX_COLUMNS]
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(raw_headers, 1):
        header = str(value or "").strip() or f"未命名列{index}"
        seen[header] = seen.get(header, 0) + 1
        if seen[header] > 1:
            header = f"{header}_{seen[header]}"
        headers.append(header)
    records: list[dict[str, Any]] = []
    for row_index, row in enumerate(nonempty[1 : MAX_ROWS + 1], start=2):
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        record = {header: padded[i] if i < len(padded) else "" for i, header in enumerate(headers)}
        if any(str(value or "").strip() for value in record.values()):
            record["__row_number__"] = row_index
            records.append(record)
    if len(nonempty) - 1 > MAX_ROWS:
        raise ValueError(f"单次最多导入{MAX_ROWS}行，请拆分文件")
    return headers, records


def _parse_file(filename: str, content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _read_csv(content)
    if suffix == ".xlsx":
        return _read_xlsx(content)
    if suffix == ".xls":
        raise ValueError("MVP暂不支持旧版.xls，请另存为.xlsx或.csv")
    raise ValueError("仅支持.xlsx和.csv")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_number(value: Any, field: str) -> tuple[float | None, str | None, str | None]:
    text = _clean_text(value)
    if text is None:
        return None, None, None
    cleaned = text.replace(",", "").replace("件", "").replace("pcs", "").replace("%", "").strip()
    try:
        number = float(cleaned)
    except ValueError:
        return None, f"{CANONICAL_FIELDS[field]['label']}不是有效数字", ISSUE_CODES["INVALID_NUMBER"]
    if field in ("order_qty", "quantity", "completed_qty") and number < 0:
        return None, f"{CANONICAL_FIELDS[field]['label']}不能为负数", ISSUE_CODES["NEGATIVE_NUMBER"]
    return number, None, None


def _parse_date(value: Any, label: str) -> tuple[str | None, str | None, str | None]:
    if value is None or str(value).strip() == "":
        return None, None, None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(value).strip()):
        number = float(value)
        if 20000 <= number <= 80000:
            return (date(1899, 12, 30) + timedelta(days=int(number))).isoformat(), None, None
    text = str(value).strip()
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    normalized = re.sub(r"\s+.*$", "", normalized)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d", "%m-%d-%Y", "%Y-%m"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            if fmt == "%Y-%m":
                return None, f"{label}必须填写到具体日期", ISSUE_CODES["INVALID_DATE"]
            return parsed.date().isoformat(), None, None
        except ValueError:
            continue
    if any(word in text for word in ("月底", "月初", "下周", "明天", "尽快", "左右", "大概")):
        return None, f"{label}含模糊或相对日期，请改为YYYY-MM-DD", ISSUE_CODES["INVALID_DATE"]
    return None, f"{label}格式无法识别，请使用YYYY-MM-DD", ISSUE_CODES["INVALID_DATE"]


def _normalize_record(raw: dict[str, Any], mapping: dict[str, str], current_user_id: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    normalized: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    missing_information: list[str] = []
    source_by_canonical: dict[str, str] = {}
    for source_header, canonical in mapping.items():
        if canonical not in CANONICAL_FIELDS or source_header not in raw:
            continue
        source_by_canonical[canonical] = source_header
        value = raw.get(source_header)
        if canonical in ("order_qty", "quantity", "completed_qty"):
            parsed, error, code = _parse_number(value, canonical)
            normalized[canonical] = parsed
            if error:
                issues.append({"field": canonical, "source_header": source_header, "level": "error", "message": error, "code": code})
        elif canonical in ("delivery_date", "customer_delivery_date", "planned_completion_date", "supplier_commitment_date", "latest_supplier_commitment"):
            parsed, error, code = _parse_date(value, CANONICAL_FIELDS[canonical]["label"])
            normalized[canonical] = parsed
            if error:
                issues.append({"field": canonical, "source_header": source_header, "level": "error", "message": error, "code": code})
        else:
            normalized[canonical] = _clean_text(value)

    for canonical, config in CANONICAL_FIELDS.items():
        if config.get("required") and not normalized.get(canonical):
            issues.append({"field": canonical, "source_header": source_by_canonical.get(canonical), "level": "error", "message": f"缺少必填字段：{config['label']}", "code": ISSUE_CODES["REQUIRED_FIELD_MISSING"]})
        elif config.get("warning") and not normalized.get(canonical):
            issues.append({"field": canonical, "source_header": source_by_canonical.get(canonical), "level": "warning", "message": f"缺少信息：{config['label']}", "code": ISSUE_CODES["MISSING_RISK_CONTEXT"]})
            missing_information.append(canonical)

    owner_val = normalized.get("owner")
    if owner_val:
        resolved_owner, owner_error_code, owner_error_msg = _resolve_owner_for_import(owner_val)
        if owner_error_code:
            issues.append({"field": "owner", "source_header": source_by_canonical.get("owner"),
                          "level": "error", "message": owner_error_msg, "code": owner_error_code})
            normalized["owner"] = None
        else:
            normalized["owner"] = resolved_owner

    if normalized.get("source_order_key"):
        normalized["order_no"] = normalized["source_order_key"]
        normalized["source_system"] = "excel_import"
    if normalized.get("order_qty") is not None:
        normalized["quantity"] = normalized["order_qty"]
    if normalized.get("delivery_date"):
        normalized["customer_delivery_date"] = normalized["delivery_date"]
    if normalized.get("supplier_commitment_date"):
        normalized["latest_supplier_commitment"] = normalized["supplier_commitment_date"]
    if normalized.get("completed_qty") is not None:
        pass
    normalized["missing_information"] = missing_information
    return normalized, issues, missing_information


def _json_equal(left: Any, right: Any) -> bool:
    if left is None and right in (None, ""):
        return True
    if right is None and left in (None, ""):
        return True
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) < 1e-9
        except (TypeError, ValueError):
            pass
    return str(left).strip() == str(right).strip()


def _map_order_columns(conn: Any) -> dict[str, str]:
    existing = get_table_column_names(conn, "orders")
    result: dict[str, str] = {}
    for canonical in ORDER_COLUMN_ALIASES:
        resolved = _resolve_column(existing, canonical)
        if resolved:
            result[canonical] = resolved
    return result


def _row_to_canonical(row: Any, column_map: dict[str, str]) -> dict[str, Any]:
    return {canonical: row[column] for canonical, column in column_map.items() if column in row.keys()}


def _preview_rows(conn: Any, records: list[dict[str, Any]], mapping: dict[str, str], current_user_id: str = "") -> tuple[list[dict[str, Any]], dict[str, int], str]:
    order_columns = _map_order_columns(conn)
    order_no_column = order_columns.get("order_no")
    order_id_column = order_columns.get("order_id")
    rows: list[dict[str, Any]] = []
    summary = {"source_row_count": 0, "identified_order_count": 0, "new": 0, "update": 0,
               "duplicate": 0, "error": 0, "warning": 0, "block": 0, "total": 0,
               "duplicate_noop_count": 0, "conflict_count": 0, "corrected_count": 0}
    order_keys_seen: set[str] = set()
    order_keys_with_error: set[str] = set()
    order_keys_with_warning: set[str] = set()
    all_rows_data: list[dict[str, Any]] = []

    for raw in records:
        row_number = int(raw.get("__row_number__", 0))
        raw_public = {k: v for k, v in raw.items() if not k.startswith("__")}
        normalized, issues, missing_info = _normalize_record(raw_public, mapping, current_user_id)
        order_no = normalized.get("source_order_key") or normalized.get("order_no")

        existing_row = None
        if order_no and order_no_column:
            existing_row = conn.execute(f'SELECT * FROM orders WHERE "{order_no_column}"=? LIMIT 1', (order_no,)).fetchone()
        changes: list[dict[str, Any]] = []
        existing_order_id = None
        if existing_row:
            current = _row_to_canonical(existing_row, order_columns)
            existing_order_id = current.get("order_id") or (existing_row[order_id_column] if order_id_column else None)
            for canonical, new_value in normalized.items():
                if new_value is None or canonical not in order_columns:
                    continue
                old_value = current.get(canonical)
                if not _json_equal(old_value, new_value):
                    changes.append({"field": canonical, "label": CANONICAL_FIELDS.get(canonical, {}).get("label", canonical), "old_value": old_value, "new_value": new_value})

        has_error = any(issue["level"] == "error" for issue in issues)
        has_warning = any(issue["level"] == "warning" for issue in issues)

        if order_no:
            order_keys_seen.add(order_no)
            if has_error:
                order_keys_with_error.add(order_no)
            if has_warning:
                order_keys_with_warning.add(order_no)

        all_rows_data.append({
            "row_id": f"ROW-{uuid.uuid4().hex[:12]}",
            "row_number": row_number,
            "raw": raw_public,
            "normalized": normalized,
            "issues": issues,
            "missing_information": missing_info,
            "changes": changes,
            "existing_order_id": existing_order_id,
            "source_order_key": order_no,
            "source_line_key": normalized.get("source_line_key"),
            "has_error": has_error,
            "has_warning": has_warning,
        })

    # D5-R2: Detect intra-batch order-level conflicts (order-level fields only, not line-level)
    # delivery_date is a line-level field in multi-line orders, so it must NOT trigger CONFLICT_IN_BATCH
    order_level_values: dict[str, dict[str, Any]] = {}
    intra_batch_conflicts: set[str] = set()
    for rd in all_rows_data:
        ok = rd["source_order_key"]
        if not ok:
            continue
        if ok not in order_level_values:
            order_level_values[ok] = {}
        for field in INTRA_BATCH_ORDER_LEVEL_FIELDS:
            val = rd["normalized"].get(field)
            if val is not None and field in order_level_values[ok]:
                if not _json_equal(order_level_values[ok][field], val):
                    intra_batch_conflicts.add(ok)
                    break
            elif val is not None:
                order_level_values[ok][field] = val

    summary["source_row_count"] = len(all_rows_data)
    summary["identified_order_count"] = len(order_keys_seen)
    summary["total"] = len(all_rows_data)

    # D5-R2: Build order_lines index for each existing order_id
    order_lines_index: dict[str, list[dict[str, Any]]] = {}
    for rd in all_rows_data:
        eoid = rd.get("existing_order_id")
        if eoid and eoid not in order_lines_index:
            order_lines_index[eoid] = []
            if table_exists(conn, "order_lines"):
                existing_lines = conn.execute(
                    "SELECT * FROM order_lines WHERE order_id=?", (eoid,)
                ).fetchall()
                for el in existing_lines:
                    order_lines_index[eoid].append(dict(el))

    # D5-R2: Classify with new states - check order_lines for line-level comparison
    for row_data in all_rows_data:
        order_no = row_data["source_order_key"]
        has_error = row_data["has_error"]
        has_warning = row_data["has_warning"]

        if has_error or (order_no and order_no in order_keys_with_error):
            row_data["classification"] = CLASSIFICATION["ERROR"]
            summary["error"] += 1
        elif order_no and order_no in intra_batch_conflicts:
            row_data["classification"] = CLASSIFICATION["CONFLICT_IN_BATCH"]
            summary["conflict_count"] += 1
        elif row_data.get("existing_order_id"):
            existing_order_id = row_data["existing_order_id"]
            existing_lines = order_lines_index.get(existing_order_id, [])
            src_line_key = row_data.get("source_line_key")
            normalized = row_data["normalized"]

            order_level_changes = [c for c in row_data["changes"] if c["field"] in ORDER_LEVEL_FIELDS]
            line_level_changes = [c for c in row_data["changes"] if c["field"] in LINE_LEVEL_FIELDS]

            # Check order-level conflicts first
            if order_level_changes:
                row_data["classification"] = CLASSIFICATION["CONFLICT_EXISTING"]
                row_data["conflict_type"] = "order_level"
                row_data["conflict_details"] = order_level_changes
                summary["conflict_count"] += 1
            elif not row_data["changes"]:
                # No changes at all - DUPLICATE_NOOP
                row_data["classification"] = CLASSIFICATION["DUPLICATE_NOOP"]
                summary["duplicate_noop_count"] += 1
                summary["duplicate"] += 1
            elif src_line_key and existing_lines:
                # Has source_line_key - try to find matching order_line
                matched_line = None
                for el in existing_lines:
                    el_src_line_key = el.get("source_line_key")
                    if el_src_line_key and str(el_src_line_key) == str(src_line_key):
                        matched_line = el
                        break
                if matched_line:
                    # Compare line facts
                    line_fact_changes = []
                    for fact_field in LINE_FACTS_FIELDS:
                        new_val = normalized.get(fact_field)
                        old_val = matched_line.get(fact_field)
                        if new_val is not None and not _json_equal(old_val, new_val):
                            line_fact_changes.append({
                                "field": fact_field,
                                "label": CANONICAL_FIELDS.get(fact_field, {}).get("label", fact_field),
                                "old_value": old_val,
                                "new_value": new_val,
                            })
                    if line_fact_changes:
                        row_data["classification"] = CLASSIFICATION["LINE_CONFLICT"]
                        row_data["conflict_type"] = "line_level"
                        row_data["conflict_details"] = line_fact_changes
                        row_data["existing_line_id"] = matched_line.get("line_id")
                        summary["conflict_count"] += 1
                    else:
                        # Line facts match exactly
                        row_data["classification"] = CLASSIFICATION["DUPLICATE_NOOP"]
                        summary["duplicate_noop_count"] += 1
                        summary["duplicate"] += 1
                else:
                    # source_line_key not found in existing lines - LINE_IDENTITY_AMBIGUOUS
                    row_data["classification"] = CLASSIFICATION["LINE_IDENTITY_AMBIGUOUS"]
                    row_data["conflict_type"] = "line_identity_ambiguous"
                    row_data["conflict_details"] = line_level_changes
                    summary["conflict_count"] += 1
            elif not src_line_key and existing_lines:
                # No source_line_key - try to find exact line match by facts
                found_match = False
                for el in existing_lines:
                    all_facts_match = True
                    for fact_field in LINE_FACTS_FIELDS:
                        new_val = normalized.get(fact_field)
                        old_val = el.get(fact_field)
                        if new_val is not None and not _json_equal(old_val, new_val):
                            all_facts_match = False
                            break
                    if all_facts_match:
                        found_match = True
                        break
                if found_match:
                    row_data["classification"] = CLASSIFICATION["DUPLICATE_NOOP"]
                    summary["duplicate_noop_count"] += 1
                    summary["duplicate"] += 1
                else:
                    row_data["classification"] = CLASSIFICATION["LINE_IDENTITY_AMBIGUOUS"]
                    row_data["conflict_type"] = "line_identity_ambiguous"
                    row_data["conflict_details"] = [{
                        "field": "line_identity",
                        "label": "明细身份",
                        "old_value": None,
                        "new_value": "未提供行号，且未找到完全一致的已有明细",
                    }]
                    summary["conflict_count"] += 1
            else:
                # No existing order_lines - compare against orders table only
                if line_level_changes:
                    if src_line_key:
                        row_data["classification"] = CLASSIFICATION["LINE_CONFLICT"]
                        row_data["conflict_type"] = "line_level"
                        row_data["conflict_details"] = line_level_changes
                        summary["conflict_count"] += 1
                    else:
                        row_data["classification"] = CLASSIFICATION["LINE_IDENTITY_AMBIGUOUS"]
                        row_data["conflict_type"] = "line_identity_ambiguous"
                        row_data["conflict_details"] = line_level_changes
                        summary["conflict_count"] += 1
                else:
                    row_data["classification"] = CLASSIFICATION["DUPLICATE_NOOP"]
                    summary["duplicate_noop_count"] += 1
                    summary["duplicate"] += 1

            if has_warning:
                summary["warning"] += 1
        else:
            row_data["classification"] = CLASSIFICATION["NEW"]
            summary["new"] += 1
            if has_warning:
                summary["warning"] += 1

    summary["block"] = summary["error"]

    rows = []
    for row_data in all_rows_data:
        rows.append({
            "row_id": row_data["row_id"],
            "row_number": row_data["row_number"],
            "raw": row_data["raw"],
            "normalized": row_data["normalized"],
            "classification": row_data["classification"],
            "issues": row_data["issues"],
            "missing_information": row_data["missing_information"],
            "changes": row_data["changes"],
            "existing_order_id": row_data["existing_order_id"],
            "existing_line_id": row_data.get("existing_line_id"),
            "source_order_key": row_data["source_order_key"],
            "source_line_key": row_data["source_line_key"],
            "conflict_type": row_data.get("conflict_type"),
            "conflict_details": row_data.get("conflict_details"),
        })

    projection_data = json.dumps(
        [{"row_id": r["row_id"], "normalized": r["normalized"], "classification": r["classification"],
          "missing_information": r.get("missing_information", []),
          "conflict_type": r.get("conflict_type"),
          "conflict_details": r.get("conflict_details")} for r in rows],
        sort_keys=True, default=str,
    )
    projection_hash = hashlib.sha256(projection_data.encode()).hexdigest()
    return rows, summary, projection_hash


def _required_db_fill(column: str, canonical: str | None, now: str) -> Any:
    defaults = {
        "status": "ACTIVE",
        "risk_level": "low",
        "owner": "待分配",
        "assignee": "待分配",
        "created_at": now,
        "updated_at": now,
    }
    if column in defaults:
        return defaults[column]
    if canonical in ("status", "risk_level", "current_node", "current_progress", "owner", "created_at", "updated_at"):
        return defaults.get(canonical)
    return ""


def _insert_order(conn: Any, normalized: dict[str, Any], column_map: dict[str, str], batch_id: str, current_user_id: str) -> str:
    now = _now_iso()
    order_id = f"ORD-IMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    values_by_column: dict[str, Any] = {}
    if "order_id" in column_map:
        values_by_column[column_map["order_id"]] = order_id
    for canonical, value in normalized.items():
        column = column_map.get(canonical)
        if column and value is not None:
            if canonical == "owner":
                value = _normalize_owner(value, current_user_id)
            values_by_column[column] = value
    if "created_at" in column_map:
        values_by_column.setdefault(column_map["created_at"], now)
    if "updated_at" in column_map:
        values_by_column[column_map["updated_at"]] = now
    if "status" in column_map:
        values_by_column.setdefault(column_map["status"], "ACTIVE")
    if "risk_level" in column_map:
        values_by_column.setdefault(column_map["risk_level"], "low")
    if "owner" in column_map:
        values_by_column.setdefault(column_map["owner"], "待分配")
    existing_columns = get_table_column_names(conn, "orders")
    if "action_readiness" in existing_columns:
        values_by_column["action_readiness"] = "BASE_ONLY"
    if "contact_status" in existing_columns:
        values_by_column["contact_status"] = "UNKNOWN"
    if "issue_status" in existing_columns:
        values_by_column["issue_status"] = "UNKNOWN"
    if "initialization_source" in existing_columns:
        values_by_column["initialization_source"] = "EXCEL_BASE_IMPORT"

    canonical_by_column = {column: canonical for canonical, column in column_map.items()}
    for info in get_table_columns(conn, "orders"):
        column = info["name"]
        if info["pk"] and str(info["type"]).upper().startswith("INTEGER"):
            continue
        if info["notnull"] and info["dflt_value"] is None and column not in values_by_column:
            values_by_column[column] = _required_db_fill(column, canonical_by_column.get(column), now)

    columns = list(values_by_column)
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    conn.execute(f"INSERT INTO orders ({quoted}) VALUES ({placeholders})", [values_by_column[column] for column in columns])
    return order_id


def _insert_order_line(conn: Any, order_id: str, normalized: dict[str, Any], batch_id: str, row_number: int) -> None:
    now = _now_iso()
    line_id = f"LINE-{uuid.uuid4().hex[:12].upper()}"
    source_line_key = normalized.get("source_line_key")
    if not source_line_key:
        source_line_key = f"auto-line-{batch_id[-8:]}-{row_number}"

    if not table_exists(conn, "order_lines"):
        return

    columns = get_table_column_names(conn, "order_lines")
    values: dict[str, Any] = {
        "line_id": line_id,
        "order_id": order_id,
        "source_system": "excel_import",
        "source_order_key": normalized.get("source_order_key") or normalized.get("order_no"),
        "source_line_key": source_line_key,
        "product_name": normalized.get("product_name"),
        "order_qty": normalized.get("order_qty") or normalized.get("quantity"),
        "completed_qty": normalized.get("completed_qty"),
        "notes": normalized.get("notes"),
        "created_at": now,
        "updated_at": now,
    }

    selected = {k: v for k, v in values.items() if k in columns}
    if len(selected) < len(columns):
        selected.setdefault("created_at", now)
        selected.setdefault("updated_at", now)

    names = list(selected.keys())
    quoted = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(f'INSERT INTO order_lines ({quoted}) VALUES ({placeholders})', [selected[name] for name in names])


def _update_order_line(conn: Any, existing_line_id: str, normalized: dict[str, Any], row_changes: list[dict[str, Any]]) -> None:
    """Update an existing order_line instead of inserting a new one."""
    if not table_exists(conn, "order_lines"):
        raise RuntimeError("order_lines表不存在，无法更新明细")

    columns = get_table_column_names(conn, "order_lines")
    assignments: list[str] = []
    values: list[Any] = []

    line_field_map = {
        "product_name": "product_name",
        "order_qty": "order_qty",
        "quantity": "order_qty",
        "completed_qty": "completed_qty",
        "notes": "notes",
        "source_line_key": "source_line_key",
    }

    for change in row_changes:
        canonical = change["field"]
        line_col = line_field_map.get(canonical)
        if line_col and line_col in columns:
            assignments.append(f'"{line_col}"=?')
            values.append(change.get("new_value"))

    if "updated_at" in columns:
        assignments.append('"updated_at"=?')
        values.append(_now_iso())

    if assignments:
        values.append(existing_line_id)
        conn.execute(
            f'UPDATE order_lines SET {", ".join(assignments)} WHERE "line_id"=?',
            values
        )


def _extract_line_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract line-level changes from the full changes list."""
    return [c for c in changes if c["field"] in LINE_LEVEL_FIELDS]


def _extract_order_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract order-level changes from the full changes list."""
    return [c for c in changes if c["field"] in ORDER_LEVEL_FIELDS]


def _update_order(conn: Any, existing_order_id: str, changes: list[dict[str, Any]], column_map: dict[str, str]) -> None:
    order_id_column = column_map.get("order_id")
    if not order_id_column:
        raise ValueError("orders表缺少可识别的order_id字段")
    assignments: list[str] = []
    values: list[Any] = []
    for change in changes:
        canonical = change["field"]
        column = column_map.get(canonical)
        if not column:
            continue
        current = conn.execute(f'SELECT "{column}" FROM orders WHERE "{order_id_column}"=?', (existing_order_id,)).fetchone()
        current_value = current[0] if current else None
        if not _json_equal(current_value, change.get("old_value")):
            raise RuntimeError(f"字段{canonical}在预览后已被其他操作修改")
        new_value = change.get("new_value")
        if canonical == "owner":
            new_value = _normalize_owner(new_value)
        assignments.append(f'"{column}"=?')
        values.append(new_value)
    if "updated_at" in column_map:
        assignments.append(f'"{column_map["updated_at"]}"=?')
        values.append(_now_iso())
    if assignments:
        values.append(existing_order_id)
        conn.execute(f'UPDATE orders SET {", ".join(assignments)} WHERE "{order_id_column}"=?', values)


def _insert_correction_record(conn: Any, order_id: str, source_order_key: str | None,
                               batch_id: str, actor_user_id: str, target_type: str,
                               target_id: str | None, changes: list[dict[str, Any]]) -> str:
    if not table_exists(conn, "order_corrections"):
        raise RuntimeError("order_corrections表不存在，无法写入纠正记录")
    correction_id = f"CORR-{uuid.uuid4().hex[:12].upper()}"
    columns = get_table_column_names(conn, "order_corrections")
    values: dict[str, Any] = {
        "correction_id": correction_id,
        "order_id": order_id,
        "source_order_key": source_order_key,
        "batch_id": batch_id,
        "actor_user_id": actor_user_id,
        "target_type": target_type,
        "target_id": target_id,
        "changes_json": json.dumps(changes, ensure_ascii=False),
        "created_at": _now_iso(),
    }
    selected = {k: v for k, v in values.items() if k in columns}
    names = list(selected.keys())
    quoted = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(f'INSERT INTO order_corrections ({quoted}) VALUES ({placeholders})', [selected[name] for name in names])
    return correction_id


def _insert_import_task(conn: Any, order_id: str, order_no: str, is_update: bool) -> bool:
    if not table_exists(conn, "tasks"):
        return False
    columns = get_table_column_names(conn, "tasks")
    if not {"task_id", "order_id"}.issubset(columns):
        return False
    title = "确认批量导入的订单变更" if is_update else "核对新导入订单资料完整性"
    if "title" in columns:
        existing = conn.execute("SELECT 1 FROM tasks WHERE order_id=? AND title=? LIMIT 1", (order_id, title)).fetchone()
        if existing:
            return False
    now = _now_iso()
    candidate_values = {
        "task_id": f"TASK-IMP-{uuid.uuid4().hex[:10].upper()}",
        "order_id": order_id,
        "title": title,
        "action_state": "NEEDS_CONFIRMATION",
        "target_role": "跟单员",
        "waiting_on": None,
        "promised_reply_at": None,
        "next_action_at": now,
        "risk_level": "medium" if is_update else "low",
        "reason": "批量导入后需要确认关键字段与交接完整性",
        "evidence": f"订单{order_no}来自Excel/CSV导入",
        "owner": "待分配",
        "status": "OPEN",
        "created_at": now,
        "updated_at": now,
    }
    selected = {key: value for key, value in candidate_values.items() if key in columns}
    names = list(selected)
    quoted_names = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO tasks ({quoted_names}) VALUES ({placeholders})",
        [selected[name] for name in names],
    )
    return True


def _append_event(conn: Any, order_id: str, event_type: str, payload: dict[str, Any]) -> None:
    if not table_exists(conn, "event_logs"):
        return
    columns = get_table_column_names(conn, "event_logs")
    now = _now_iso()
    candidates = {
        "event_id": f"EVT-IMP-{uuid.uuid4().hex[:10].upper()}",
        "entity_type": "order",
        "entity_id": order_id,
        "event_type": event_type,
        "event_name": event_type,
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "detail_json": json.dumps(payload, ensure_ascii=False),
        "operator_id": "excel_import",
        "created_at": now,
    }
    selected = {key: value for key, value in candidates.items() if key in columns}
    if not selected:
        return
    names = list(selected)
    quoted_names = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO event_logs ({quoted_names}) VALUES ({placeholders})",
        [selected[name] for name in names],
    )


def _begin_savepoint(conn: Any, name: str) -> None:
    if getattr(conn, "is_pg", False):
        conn.execute(f"SAVEPOINT {name}")
    else:
        conn.execute(f"SAVEPOINT {name}")


def _rollback_savepoint(conn: Any, name: str) -> None:
    if getattr(conn, "is_pg", False):
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
    else:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")


def _release_savepoint(conn: Any, name: str) -> None:
    if getattr(conn, "is_pg", False):
        conn.execute(f"RELEASE SAVEPOINT {name}")
    else:
        conn.execute(f"RELEASE SAVEPOINT {name}")


def _auth_required() -> bool:
    return bool(os.environ.get("IMPORT_ADMIN_KEY") or os.environ.get("APP_API_KEY"))


def _check_import_key(provided: str | None) -> None:
    expected = os.environ.get("IMPORT_ADMIN_KEY") or os.environ.get("APP_API_KEY")
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="导入密钥错误")


def _xlsx_template() -> bytes:
    headers = [config["label"] for config in CANONICAL_FIELDS.values()]
    sample = [
        "PO-IMPORT-001", "001", "示例客户", "2026-08-20", "USER-1",
        "示例产品", "1000", "ACTIVE", "450", "2026-08-15",
        "2026-08-14", "示例工厂", "示例备注",
    ]

    def cell(ref: str, value: str) -> str:
        safe = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>'

    def col_name(index: int) -> str:
        result = ""
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    row1 = "".join(cell(f"{col_name(i)}1", value) for i, value in enumerate(headers))
    row2 = "".join(cell(f"{col_name(i)}2", value) for i, value in enumerate(sample))
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">{row1}</row><row r="2">{row2}</row></sheetData></worksheet>'''
    files = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="订单导入模板" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        "xl/styles.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf xfId="0"/></cellXfs></styleSheet>''',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _csv_template() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([config["label"] for config in CANONICAL_FIELDS.values()])
    writer.writerow([
        "PO-IMPORT-001", "001", "示例客户", "2026-08-20", "USER-1",
        "示例产品", "1000", "ACTIVE", "450", "2026-08-15",
        "2026-08-14", "示例工厂", "示例备注",
    ])
    return buffer.getvalue().encode("utf-8-sig")


IMPORT_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>订单批量导入</title><link rel="stylesheet" href="/api/import/assets/style.css"></head><body>
<header><a class="back" href="/">← 返回工作台</a><div><h1>订单批量导入</h1><p>原始订单 → 字段映射 → 校验预览 → 建立订单底座 → 补充进展</p></div><span class="version">V3.2</span></header>
<main>
<section class="panel"><div class="step-title"><b>1</b><div><h2>上传订单文件</h2><p>支持 .xlsx 与 .csv，单次最多500行、5MB。旧版 .xls 请先另存为 .xlsx。</p></div></div><div class="upload-row"><label class="file-box"><input id="file" type="file" accept=".xlsx,.csv"><span id="file-name">选择文件或拖入文件</span></label><button id="preview-btn" class="primary">读取并预览</button></div><div class="template-links"><a href="/api/import/template.xlsx">下载Excel模板</a><a href="/api/import/template.csv">下载CSV模板</a></div></section>
<section id="mapping-panel" class="panel hidden"><div class="step-title"><b>2</b><div><h2>确认字段映射</h2><p>系统自动匹配表头；未匹配或匹配错误的列可手动调整。</p></div></div><div id="mapping-grid" class="mapping-grid"></div><button id="repreview-btn" class="secondary">按当前映射重新校验</button></section>
<section id="result-panel" class="panel hidden"><div class="step-title"><b>3</b><div><h2>校验结果</h2><p>错误行不会写入；更新已有订单时只展示发生变化的字段。</p></div></div><div id="summary" class="summary"></div><div class="toolbar"><label>筛选 <select id="status-filter"><option value="ALL">全部</option><option value="NEW">新增</option><option value="UPDATE">更新</option><option value="DUPLICATE">重复</option><option value="DUPLICATE_NOOP">跳过(无变化)</option><option value="CONFLICT_EXISTING">冲突-已存在</option><option value="CONFLICT_IN_BATCH">冲突-批内</option><option value="LINE_CONFLICT">行冲突</option><option value="LINE_IDENTITY_AMBIGUOUS">行身份模糊</option><option value="ERROR">错误</option></select></label><span id="schema-warning"></span></div><div class="table-wrap"><table><thead><tr><th>行</th><th>状态</th><th>订单号</th><th>客户</th><th>产品/SKU</th><th>数量</th><th>交期</th><th>问题/变化</th><th>操作</th></tr></thead><tbody id="rows"></tbody></table></div><div class="commit-box"><label id="key-wrap" class="hidden">导入密钥<input id="import-key" type="password" autocomplete="off" placeholder="Render中的IMPORT_ADMIN_KEY"></label><button id="commit-btn" class="primary">确认导入有效数据</button><p>导入只建立或更新订单基础档案，不会根据原始订单虚构风险或自动生成行动。重复和错误行自动跳过。</p></div></section>
<section id="commit-result" class="panel hidden"></section>
</main><div id="toast"></div><script src="/api/import/assets/app.js"></script></body></html>'''

IMPORT_CSS = r''':root{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#17201d;background:#f4f4f3}*{box-sizing:border-box}body{margin:0}header{height:88px;background:#fff;border-bottom:1px solid #dde2dc;display:flex;align-items:center;gap:22px;padding:0 5vw;position:sticky;top:0;z-index:5}header h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:#68736e;font-size:13px}.back{color:#092923;text-decoration:none;font-weight:600}.version{margin-left:auto;background:#e8eee9;color:#092923;padding:6px 10px;border-radius:999px;font-size:12px}main{max-width:1180px;margin:28px auto;padding:0 20px 80px}.panel{background:#fff;border:1px solid #dde2dc;border-radius:16px;padding:24px;margin-bottom:18px;box-shadow:0 8px 24px rgba(29,55,105,.05)}.hidden{display:none!important}.step-title{display:flex;gap:14px;align-items:flex-start}.step-title>b{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#092923;color:#fff}.step-title h2{margin:1px 0 5px;font-size:18px}.step-title p{margin:0;color:#68736e;font-size:13px}.upload-row{display:flex;gap:12px;margin-top:20px}.file-box{flex:1;border:1px dashed #b8c0b9;background:#fafaf8;border-radius:12px;height:54px;display:flex;align-items:center;padding:0 16px;color:#3f4d44;cursor:pointer}.file-box input{display:none}button{border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}.primary{background:#092923;color:#fff}.primary:disabled{opacity:.5;cursor:not-allowed}.secondary{background:#e8eee9;color:#3f4d44}.template-links{display:flex;gap:16px;margin-top:12px}.template-links a{font-size:13px;color:#092923}.mapping-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}.mapping-item{border:1px solid #dde2dc;border-radius:10px;padding:11px}.mapping-item label{font-size:12px;color:#68736e;display:block;margin-bottom:6px}.mapping-item select{width:100%;border:1px solid #cfd6cf;border-radius:8px;padding:9px;background:#fff}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:20px 0}.summary-card{border:1px solid #dde2dc;border-radius:12px;padding:14px}.summary-card span{display:block;color:#68736e;font-size:12px}.summary-card strong{display:block;font-size:24px;margin-top:5px}.toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;color:#68736e;font-size:13px}.toolbar select{padding:7px 10px;border:1px solid #cfd6cf;border-radius:8px}.table-wrap{overflow:auto;border:1px solid #dde2dc;border-radius:12px}table{width:100%;border-collapse:collapse;min-width:980px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e9ece8;font-size:13px;vertical-align:top}th{background:#fafaf8;color:#3f4d44}.badge{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700}.NEW{background:#e9f3ed;color:#216844}.UPDATE{background:#f5ecdf;color:#8b5b2f}.DUPLICATE{background:#ecefea;color:#68736e}.ERROR{background:#f7e8e6;color:#9c3d36}.DUPLICATE_NOOP{background:#ecefea;color:#68736e}.CONFLICT_EXISTING{background:#f5ecdf;color:#8b5b2f}.CONFLICT_IN_BATCH{background:#f7e8e6;color:#9c3d36}.LINE_CONFLICT{background:#f5ecdf;color:#8b5b2f}.LINE_IDENTITY_AMBIGUOUS{background:#f5ecdf;color:#8b5b2f}.detail{max-width:340px;color:#68736e}.detail div{margin-bottom:4px}.commit-box{display:flex;align-items:end;gap:14px;margin-top:18px;flex-wrap:wrap}.commit-box label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#68736e}.commit-box input{width:280px;padding:10px;border:1px solid #cfd6cf;border-radius:8px}.commit-box p{width:100%;margin:0;color:#7a849a;font-size:12px}#commit-result h2{margin-top:0}.result-note{margin:18px 0;padding:16px;border:1px solid #d4e0d7;border-radius:12px;background:#f0f6f2;color:#24463e;line-height:1.7}.result-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}.result-actions a{display:inline-flex;align-items:center;justify-content:center;border-radius:10px;padding:12px 18px;font-weight:700;text-decoration:none}.result-actions .primary-link{background:#092923;color:#fff}.result-actions .secondary-link{background:#e8eee9;color:#3f4d44}.result-timing{display:flex;gap:12px;margin:18px 0;padding:14px 16px;border:1px solid #dde2dc;border-radius:12px;background:#fafaf8}.timing-row{display:flex;flex-direction:column;gap:4px}.timing-row span{color:#68736e;font-size:12px}.timing-row strong{font-size:18px;color:#092923}.result-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.result-grid div{background:#fafaf8;border-radius:12px;padding:16px}.result-grid strong{display:block;font-size:24px}#toast{position:fixed;right:24px;bottom:24px;background:#17201d;color:#fff;padding:12px 16px;border-radius:10px;opacity:0;transform:translateY(10px);transition:.2s;pointer-events:none}#toast.show{opacity:1;transform:none}@media(max-width:800px){header{height:auto;padding:16px 18px;flex-wrap:wrap}.version{margin-left:0}.mapping-grid{grid-template-columns:1fr}.summary{grid-template-columns:repeat(2,1fr)}.upload-row{flex-direction:column}.result-grid{grid-template-columns:repeat(2,1fr)}}'''

IMPORT_JS = r'''let state={file:null,contentBase64:null,headers:[],mapping:{},rows:[],batchId:null,authRequired:false,clientStartMs:0,previewReturnMs:0,projectionHash:"",orderActions:{}};
const tokenMap={"USER-1":"tok-user-1","USER-2":"tok-user-2","USER-3":"tok-user-3","MANAGER-1":"tok-manager-1","OPERATOR-A1":"tok-operator-a1","OPERATOR-A2":"tok-operator-a2","MANAGER-A":"tok-manager-a","OPERATOR-B1":"tok-operator-b1","OPERATOR-B2":"tok-operator-b2","MANAGER-B":"tok-manager-b"};
const authHeaders=()=>({"Content-Type":"application/json","X-Auth-Token":tokenMap[localStorage.getItem("currentUserId")||"USER-1"]||"tok-user-1"});
const $=s=>document.querySelector(s);const toast=m=>{const el=$("#toast");el.textContent=m;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2500)};
async function trackEvent(name,props){try{await fetch("/api/import/track",{method:"POST",headers:authHeaders(),body:JSON.stringify({event_name:name,properties:props})})}catch(e){}}
async function updateClientMetrics(batchId,e2eMs){try{await fetch("/api/import/batches/"+batchId+"/client-metrics",{method:"POST",headers:authHeaders(),body:JSON.stringify({client_end_to_end_duration_ms:e2eMs})})}catch(e){}}
function bytesToBase64(buffer){let binary="";const bytes=new Uint8Array(buffer);const chunk=0x8000;for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,Math.min(i+chunk,bytes.length)));return btoa(binary)}
async function loadFile(file){if(!file)throw new Error("请选择文件");if(file.size>5*1024*1024)throw new Error("文件不能超过5MB");state.file=file;state.contentBase64=bytesToBase64(await file.arrayBuffer());}
function fieldOptions(selected=""){const fields=window.__fields||{};return `<option value="">忽略此列</option>`+Object.entries(fields).map(([k,v])=>`<option value="${k}" ${k===selected?'selected':''}>${v.label}${v.required?' *':''}${v.warning?' ⚠':''}</option>`).join("")}
function renderMapping(){const grid=$("#mapping-grid");grid.innerHTML=state.headers.map(h=>`<div class="mapping-item"><label>${escapeHtml(h)}</label><select data-header="${escapeAttr(h)}">${fieldOptions(state.mapping[h]||"")}</select></div>`).join("");grid.querySelectorAll("select").forEach(s=>s.addEventListener("change",()=>state.mapping[s.dataset.header]=s.value));$("#mapping-panel").classList.remove("hidden")}
function renderSummary(summary){const labels={source_row_count:"Excel行数",identified_order_count:"订单数",new:"可新增",update:"可更新",duplicate:"完全重复",duplicate_noop_count:"跳过(无变化)",conflict_count:"冲突",corrected_count:"纠正",error:"错误",warning:"警告"};$("#summary").innerHTML=Object.entries(labels).map(([k,l])=>{const v=summary[k]||0;const cls=k==="error"?"error-card":k==="warning"?"warning-card":"";return`<div class="summary-card ${cls}"><span>${l}</span><strong>${v}</strong></div>`}).join("")}
function rowDetail(r){const issues=(r.issues||[]).map(i=>`<div class="${i.level}">${i.level==='warning'?'⚠':'⛔'} [${i.code||''}] ${escapeHtml(i.message)}</div>`);const changes=(r.changes||[]).map(c=>`<div>${escapeHtml(c.label)}：${escapeHtml(String(c.old_value??"空"))} → ${escapeHtml(String(c.new_value??"空"))}</div>`);const missing=(r.missing_information||[]).map(f=>`<div style="color:#8b5b2f">📋 缺少：${escapeHtml(f)}</div>`);return [...missing,...issues,...changes].join("")||"—"}
function renderRows(){const filter=$("#status-filter").value;const rows=state.rows.filter(r=>filter==="ALL"||r.classification===filter);const grouped={};rows.forEach(r=>{const k=r.normalized.source_order_key||r.normalized.order_no||`ROW-${r.row_number}`;(grouped[k]=grouped[k]||[]).push(r)});const orderKeysWithConflict=new Set();Object.entries(grouped).forEach(([k,rs])=>{if(rs.some(r=>r.classification==="CONFLICT_EXISTING"||r.classification==="LINE_CONFLICT"))orderKeysWithConflict.add(k)});$("#rows").innerHTML=rows.map(r=>{const ok=r.normalized.source_order_key||r.normalized.order_no||"";let detail=rowDetail(r);if((r.classification==="CONFLICT_EXISTING"||r.classification==="LINE_CONFLICT")&&r.conflict_details&&r.conflict_details.length){detail=`<div style="background:#fff8e1;padding:8px;border-radius:6px;margin-bottom:6px">${r.conflict_details.map(c=>`<div><strong>${escapeHtml(c.label||c.field)}:</strong> ${escapeHtml(String(c.old_value??"空"))} → <span style="color:#8b5b2f">${escapeHtml(String(c.new_value??"空"))}</span></div>`).join("")}</div>`+detail}let actionCell="";if(orderKeysWithConflict.has(ok)){const act=state.orderActions[ok]||"";const btnCorrection=`<button class="badge" style="background:${act==='apply_correction'?'#216844':'#f5ecdf'};color:${act==='apply_correction'?'#fff':'#8b5b2f'};cursor:pointer;margin-right:4px" data-order-key="${escapeAttr(ok)}" data-action="apply_correction">作为纠正提交</button>`;const btnSkip=`<button class="badge" style="background:${act==='skip'?'#9c3d36':'#ecefea'};color:${act==='skip'?'#fff':'#68736e'};cursor:pointer" data-order-key="${escapeAttr(ok)}" data-action="skip">跳过</button>`;actionCell=`<td>${btnCorrection}${btnSkip}</td>`}else{actionCell=`<td></td>`}return`<tr><td>${r.row_number}</td><td><span class="badge ${r.classification}">${r.classification}</span></td><td>${escapeHtml(ok)}</td><td>${escapeHtml(r.normalized.customer_name||"")}</td><td>${escapeHtml(r.normalized.product_name||"")}</td><td>${escapeHtml(String(r.normalized.order_qty??r.normalized.quantity??""))}</td><td>${escapeHtml(r.normalized.delivery_date||r.normalized.customer_delivery_date||"")}</td><td>${escapeHtml(r.normalized.owner||"")}</td><td class="detail">${detail}</td>${actionCell}</tr>`}).join("")}
function formatDuration(ms){if(ms==null||ms===undefined)return"—";const s=ms/1000;return s.toFixed(1)+" 秒"}
async function preview(useCurrentMapping=false){try{$("#preview-btn").disabled=true;if(!state.file)await loadFile($("#file").files[0]);if(!state.clientStartMs)state.clientStartMs=performance.now();const payload={filename:state.file.name,content_base64:state.contentBase64,mapping:useCurrentMapping?state.mapping:null};const res=await fetch("/api/import/preview",{method:"POST",headers:authHeaders(),body:JSON.stringify(payload)});const data=await res.json();if(!res.ok)throw new Error(data.detail||"预览失败");state.previewReturnMs=performance.now();window.__fields=data.fields;state.headers=data.headers;state.mapping=data.mapping;state.rows=data.rows;state.batchId=data.batch_id;state.authRequired=data.auth_required;state.projectionHash=data.projection_hash||"";await trackEvent("import_preview_viewed",{import_batch_id:data.batch_id,viewed_at:new Date().toISOString()});renderMapping();renderSummary(data.summary);renderRows();$("#result-panel").classList.remove("hidden");$("#key-wrap").classList.toggle("hidden",!data.auth_required);$("#schema-warning").textContent=data.schema_notes?.join("；")||"";if(data.block_count>0){toast(`发现 ${data.block_count} 个错误需要修正`)}else if(data.warning_count>0){toast(`发现 ${data.warning_count} 个警告`)}else{toast("校验通过")}}catch(e){toast(e.message)}finally{$("#preview-btn").disabled=false}}
async function commit(){if(!state.batchId)return;try{$("#commit-btn").disabled=true;const res=await fetch("/api/import/commit",{method:"POST",headers:authHeaders(),body:JSON.stringify({batch_id:state.batchId,import_key:$("#import-key").value||null,row_actions:{},order_actions:state.orderActions,projection_hash:state.projectionHash})});const data=await res.json();const commitReturnMs=performance.now();if(!res.ok)throw new Error(data.detail||"导入失败");const e2eMs=Math.round(commitReturnMs-state.clientStartMs);const serverProcMs=data.processing_duration_ms||0;await updateClientMetrics(state.batchId,e2eMs);await trackEvent("import_completed",{import_batch_id:state.batchId,identified_order_count:data.identified_order_count||0,success_count:data.success_count||0,success_with_warning_count:data.success_with_warning_count||0,blocked_count:data.blocked_count||0,commit_failed_count:data.commit_failed_count||0,excel_import_end_to_end_duration_ms:e2eMs,excel_import_processing_duration_ms:serverProcMs,source_type:"excel_import",client_timestamp:new Date().toISOString()});const displayE2E=e2eMs||data.end_to_end_duration_ms||0;const hasWarn=data.success_with_warning_count>0;$("#commit-result").classList.remove("hidden");$("#commit-result").innerHTML=`<h2>Import Report</h2><div class="result-grid"><div><span>成功导入</span><strong>${data.success_count}</strong></div><div><span>成功但有警告</span><strong>${data.success_with_warning_count}</strong></div><div><span>被阻止</span><strong>${data.blocked_count}</strong></div><div><span>提交失败</span><strong>${data.commit_failed_count}</strong></div></div><div class="result-timing"><div class="timing-row"><span>本次导入总耗时</span><strong>${formatDuration(displayE2E)}</strong></div><div class="timing-row"><span>系统处理耗时</span><strong>${formatDuration(serverProcMs)}</strong></div></div><div class="result-note">${hasWarn?'<div style="background:#fff8e1;padding:12px;border-radius:8px;margin-bottom:12px"><strong>⚠ 部分订单缺少信息已标记警告</strong>，可在Import Report中查看详情</div>':''}<strong>订单已按负责人进入对应工作空间。</strong><br>原始订单仍需补充当前进度或最近沟通后，才能进入行动排序。</div><div class="result-actions"><a class="primary-link" href="/#activation">开始初始化活跃订单</a><a class="secondary-link" href="/#orders">查看订单中心</a><button class="secondary-link" id="export-report-btn" style="background:#e8eee9;color:#3f4d44;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer;border:0">导出Import Report</button>${data.commit_failed_count>0?`<button class="secondary-link" id="retry-failed-btn" style="background:#f5ecdf;color:#8b5b2f;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer;border:0">仅重试失败订单 (${data.commit_failed_count})</button>`:''}</div>`;toast("导入成功");window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});const exportBtn=document.getElementById("export-report-btn");if(exportBtn)exportBtn.addEventListener("click",()=>{const batchId=state.batchId;trackEvent("import_report_exported",{import_batch_id:batchId,export_type:"csv",exported_at:new Date().toISOString()});window.open("/api/import/batches/"+batchId+"/export","_blank")});const retryBtn=document.getElementById("retry-failed-btn");if(retryBtn)retryBtn.addEventListener("click",async()=>{try{retryBtn.disabled=true;retryBtn.textContent="重试中...";const res=await fetch("/api/import/batches/"+state.batchId+"/retry-failed",{method:"POST",headers:authHeaders()});const retryData=await res.json();if(!res.ok)throw new Error(retryData.detail||"重试失败");state.rows=retryData.rows;state.batchId=retryData.batch_id;state.projectionHash=retryData.projection_hash||"";state.orderActions={};renderSummary(retryData.summary);renderRows();$("#commit-result").classList.add("hidden");$("#result-panel").scrollIntoView({behavior:"smooth"});toast(`已创建重试批次 ${retryData.batch_id}`)}catch(e){toast(e.message)}finally{retryBtn.disabled=false}})}catch(e){toast(e.message)}finally{$("#commit-btn").disabled=false}}
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]))}function escapeAttr(v){return escapeHtml(v)}
$("#file").addEventListener("change",async e=>{try{await loadFile(e.target.files[0]);$("#file-name").textContent=state.file.name}catch(err){toast(err.message)}});$("#preview-btn").addEventListener("click",()=>{state.clientStartMs=performance.now();preview(false)});$("#repreview-btn").addEventListener("click",()=>{state.clientStartMs=performance.now();preview(true)});$("#status-filter").addEventListener("change",renderRows);$("#rows").addEventListener("click",e=>{const btn=e.target.closest("button[data-order-key]");if(!btn)return;const ok=btn.dataset.orderKey;const act=btn.dataset.action;state.orderActions[ok]=state.orderActions[ok]===act?"":act;renderRows()});$("#commit-btn").addEventListener("click",commit);'''

ENTRY_JS = r'''(()=>{const add=()=>{if(document.querySelector('[data-excel-import-entry]'))return;const a=document.createElement('a');a.href='/import-orders';a.dataset.excelImportEntry='1';a.textContent='批量导入';a.title='Excel/CSV批量导入订单';a.style.cssText='display:flex;align-items:center;gap:8px;padding:10px 12px;border-radius:10px;text-decoration:none;color:inherit;font-weight:600;';const icon=document.createElement('span');icon.textContent='⇧';icon.style.cssText='width:24px;height:24px;border-radius:7px;background:#e8eee9;color:#092923;display:grid;place-items:center;font-weight:800';a.prepend(icon);const targets=['aside nav','.sidebar nav','.sidebar-menu','.nav-list','[data-nav]'];let target=null;for(const s of targets){target=document.querySelector(s);if(target)break}if(target){target.appendChild(a)}else{a.style.cssText+='position:fixed;right:18px;bottom:18px;background:#fff;border:1px solid #dde2dc;box-shadow:0 8px 24px rgba(0,0,0,.12);z-index:9999;color:#17201d';document.body.appendChild(a)}};document.readyState==='loading'?document.addEventListener('DOMContentLoaded',add):add();setTimeout(add,1200)})();'''


class ImportEntryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path not in ("/", "/index.html"):
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        marker = "/api/import/assets/entry.js"
        if marker not in text:
            script = f'<script src="{marker}"></script>'
            text = text.replace("</body>", script + "</body>") if "</body>" in text else text + script
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(content=text, status_code=response.status_code, headers=headers, media_type="text/html")


def register_excel_import_patch(app: FastAPI) -> None:
    if getattr(app.state, "excel_import_patch_registered", False):
        return
    app.state.excel_import_patch_registered = True

    @app.get("/api/import/capabilities")
    def import_capabilities():
        with db() as conn:
            _ensure_patch_schema(conn)
            columns = sorted(get_table_column_names(conn, "orders"))
        return {
            "patch_version": PATCH_VERSION,
            "supported_formats": [".xlsx", ".csv"],
            "max_rows": MAX_ROWS,
            "max_file_bytes": MAX_FILE_BYTES,
            "auth_required": False,
            "orders_columns": columns,
        }

    @app.get("/import-orders", response_class=HTMLResponse)
    def import_page():
        return HTMLResponse(IMPORT_HTML)

    @app.get("/api/import/assets/style.css")
    def import_style():
        return Response(IMPORT_CSS, media_type="text/css")

    @app.get("/api/import/assets/app.js")
    def import_script():
        return Response(IMPORT_JS, media_type="application/javascript")

    @app.get("/api/import/assets/entry.js")
    def import_entry_script():
        return Response(ENTRY_JS, media_type="application/javascript")

    @app.get("/api/import/template.xlsx")
    def template_xlsx():
        return Response(
            _xlsx_template(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="order_import_template.xlsx"'},
        )

    @app.get("/api/import/template.csv")
    def template_csv():
        return Response(
            _csv_template(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="order_import_template.csv"'},
        )

    @app.post("/api/import/preview")
    def preview_import(payload: PreviewRequest, identity: CurrentIdentity = Depends(get_current_identity)):
        import time as _time
        preview_start = _time.perf_counter()
        started_at = _now_iso()
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="文件内容不是有效Base64") from exc
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="文件不能超过5MB")
        try:
            headers, records = _parse_file(payload.filename, content)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        mapping = payload.mapping or _auto_mapping(headers)
        mapping = {source: canonical for source, canonical in mapping.items() if source in headers and canonical in CANONICAL_FIELDS}
        with db() as conn:
            _ensure_patch_schema(conn)
            rows, summary, projection_hash = _preview_rows(conn, records, mapping, identity.user_id)
            batch_id = f"IMP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            preflight_completed_at = _now_iso()
            preflight_duration_ms = int((_time.perf_counter() - preview_start) * 1000)
            warning_count = summary.get("warning", 0)
            block_count = summary.get("error", 0)
            conn.execute(
                "INSERT INTO order_import_batches(batch_id,source_filename,source_sha256,status,total_rows,importable_rows,error_rows,mapping_json,summary_json,created_at,started_at,preflight_completed_at,preflight_duration_ms,projection_hash,warning_count,block_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    Path(payload.filename).name,
                    hashlib.sha256(content).hexdigest(),
                    "PREVIEWED",
                    summary["source_row_count"],
                    summary["new"] + summary["update"],
                    summary["error"],
                    json.dumps(mapping, ensure_ascii=False),
                    json.dumps({**summary, "_auth": {"organization_id": identity.organization_id, "created_by": identity.user_id}}, ensure_ascii=False),
                    started_at,
                    started_at,
                    preflight_completed_at,
                    preflight_duration_ms,
                    projection_hash,
                    warning_count,
                    block_count,
                ),
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO order_import_rows(row_id,batch_id,row_number,raw_json,normalized_json,classification,issues_json,changes_json,existing_order_id,existing_line_id,source_order_key,source_line_key,conflict_type,conflict_details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["row_id"],
                        batch_id,
                        row["row_number"],
                        json.dumps(row["raw"], ensure_ascii=False),
                        json.dumps(row["normalized"], ensure_ascii=False),
                        row["classification"],
                        json.dumps(row["issues"], ensure_ascii=False),
                        json.dumps(row["changes"], ensure_ascii=False),
                        row["existing_order_id"],
                        row.get("existing_line_id"),
                        row.get("source_order_key"),
                        row.get("source_line_key"),
                        row.get("conflict_type"),
                        json.dumps(row.get("conflict_details"), ensure_ascii=False) if row.get("conflict_details") else None,
                    ),
                )
            track_event(
                conn, "excel_import_started", organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role="manager" if identity.user_id == "MANAGER-1" else "operator",
                source="website",
                properties={
                    "import_batch_id": batch_id,
                    "source_type": "excel_import",
                    "started_at": started_at,
                },
            )
            track_event(
                conn, "preflight_completed", organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role="manager" if identity.user_id == "MANAGER-1" else "operator",
                source="website",
                properties={
                    "import_batch_id": batch_id,
                    "identified_order_count": summary.get("identified_order_count", 0),
                    "pass_count": summary.get("new", 0) + summary.get("update", 0),
                    "warning_count": warning_count,
                    "block_count": block_count,
                    "processing_duration_ms": preflight_duration_ms,
                },
            )
            conn.commit()
            order_columns = _map_order_columns(conn)
        schema_notes = []
        if "order_id" not in order_columns:
            schema_notes.append("orders表未识别到order_id，新增订单会使用现有主键规则尝试写入")
        return {
            "patch_version": PATCH_VERSION,
            "batch_id": batch_id,
            "headers": headers,
            "mapping": mapping,
            "fields": CANONICAL_FIELDS,
            "summary": summary,
            "rows": rows,
            "auth_required": _auth_required(),
            "schema_notes": schema_notes,
            "preflight_duration_ms": preflight_duration_ms,
            "projection_hash": projection_hash,
            "warning_count": warning_count,
            "block_count": block_count,
        }

    @app.post("/api/import/commit")
    def commit_import(payload: CommitRequest, identity: CurrentIdentity = Depends(get_current_identity)):
        import time as _time
        commit_start = _time.perf_counter()
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (payload.batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在或已过期")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if auth_meta.get("organization_id"):
                require_same_org(identity, auth_meta["organization_id"])
            else:
                raise HTTPException(status_code=403, detail="旧导入批次缺少组织绑定，请重新预览后提交")
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="仅导入创建人或同组织主管可提交该批次")

            if not payload.projection_hash:
                raise HTTPException(status_code=400, detail="缺少Projection Hash，请重新预览后提交")
            if not batch["projection_hash"] or payload.projection_hash != batch["projection_hash"]:
                raise HTTPException(status_code=400, detail="Projection数据已变更或Hash不匹配，请重新预览")

            if batch["status"] == "COMMITTED":
                previous = json.loads(batch["summary_json"])
                processing_duration_ms = batch["preflight_duration_ms"] or 0
                end_to_end_duration_ms = batch["end_to_end_duration_ms"] or 0
                return {
                    "status": "DUPLICATE_SKIPPED",
                    "batch_id": payload.batch_id,
                    **previous,
                    "processing_duration_ms": processing_duration_ms,
                    "end_to_end_duration_ms": end_to_end_duration_ms,
                }

            track_event(
                conn, "import_commit_clicked", organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role="manager" if identity.user_id == "MANAGER-1" else "operator",
                source="website",
                properties={
                    "import_batch_id": payload.batch_id,
                    "clicked_at": _now_iso(),
                },
            )
            conn.commit()

            rows = conn.execute("SELECT * FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (payload.batch_id,)).fetchall()
            column_map = _map_order_columns(conn)
            order_no_column = column_map.get("order_no")
            order_id_column = column_map.get("order_id")

            grouped_rows: dict[str, list[Any]] = {}
            for row in rows:
                norm = json.loads(row["normalized_json"])
                key = norm.get("source_order_key") or norm.get("order_no") or f"ROW-{row['row_number']}"
                grouped_rows.setdefault(key, []).append(row)

            # D5: Check for unresolved conflicts before commit
            for order_key, order_rows in grouped_rows.items():
                for row in order_rows:
                    cls = row["classification"]
                    if cls in ("CONFLICT_EXISTING", "LINE_CONFLICT"):
                        order_action = payload.order_actions.get(order_key, "")
                        row_action = payload.row_actions.get(str(row["row_number"]), "")
                        if order_action not in ("apply_correction", "skip") and row_action not in ("apply_correction", "skip"):
                            raise HTTPException(
                                status_code=400,
                                detail=f"UNRESOLVED_IMPORT_CONFLICT: 订单 {order_key} 存在冲突，请明确选择 apply_correction 或 skip"
                            )

            success_count = 0
            success_with_warning_count = 0
            blocked_count = 0
            commit_failed_count = 0
            warning_row_count = 0
            imported_order_ids: list[str] = []
            failures: list[dict[str, Any]] = []
            order_results: list[dict[str, Any]] = []
            duplicate_noop_count = 0
            conflict_count = 0
            corrected_count = 0

            for order_key, order_rows in grouped_rows.items():
                savepoint_name = f"sp_{order_key.replace('-', '_').replace(' ', '_')[:30]}"

                order_has_error = any(row["classification"] == "ERROR" for row in order_rows)
                order_has_blocking_conflict = any(
                    row["classification"] in ("CONFLICT_IN_BATCH", "LINE_IDENTITY_AMBIGUOUS")
                    for row in order_rows
                )
                order_has_resolved_conflict = False
                order_conflict_rows = [
                    row for row in order_rows
                    if row["classification"] in ("CONFLICT_EXISTING", "LINE_CONFLICT")
                ]
                order_action = payload.order_actions.get(order_key, "")
                for cr in order_conflict_rows:
                    row_action = payload.row_actions.get(str(cr["row_number"]), "")
                    effective_action = row_action or order_action
                    if effective_action == "apply_correction":
                        order_has_resolved_conflict = True

                if order_has_error or order_has_blocking_conflict:
                    blocked_count += 1
                    for row in order_rows:
                        if row["classification"] == "ERROR":
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Order contains BLOCKED row", row["row_id"]))
                        elif row["classification"] == "CONFLICT_IN_BATCH":
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Intra-batch conflict", row["row_id"]))
                        elif row["classification"] == "LINE_IDENTITY_AMBIGUOUS":
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Line identity ambiguous", row["row_id"]))
                        else:
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Order blocked due to other row", row["row_id"]))
                    order_results.append({
                        "source_order_key": order_key,
                        "result_status": "BLOCKED",
                        "order_id": None,
                        "row_count": len(order_rows),
                    })
                    continue

                if order_conflict_rows and not order_has_resolved_conflict:
                    blocked_count += 1
                    for row in order_rows:
                        if row["classification"] in ("CONFLICT_EXISTING", "LINE_CONFLICT"):
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Unresolved conflict", row["row_id"]))
                        elif payload.row_actions.get(str(row["row_number"]), "import") == "skip":
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Skipped by user", row["row_id"]))
                        elif row["classification"] == "DUPLICATE_NOOP":
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Order conflicted, duplicate skipped", row["row_id"]))
                        else:
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Order blocked due to conflict", row["row_id"]))
                    order_results.append({
                        "source_order_key": order_key,
                        "result_status": "BLOCKED",
                        "order_id": None,
                        "row_count": len(order_rows),
                    })
                    continue

                try:
                    _begin_savepoint(conn, savepoint_name)
                    order_success = False
                    order_has_warning = False
                    order_failed = False
                    order_was_corrected = False
                    order_id_for_group = None
                    order_ids_added_in_this_group: list[str] = []

                    for row in order_rows:
                        row_number = str(row["row_number"])
                        action = payload.row_actions.get(row_number, "import")
                        order_action = payload.order_actions.get(order_key, "")
                        classification = row["classification"]
                        issues = json.loads(row["issues_json"] or "[]")
                        has_warning = any(i["level"] == "warning" for i in issues)

                        effective_action = action
                        if effective_action == "import" and classification in ("CONFLICT_EXISTING", "LINE_CONFLICT"):
                            effective_action = order_action or "import"

                        if effective_action == "skip":
                            blocked_count += 1
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Skipped by user", row["row_id"]))
                            continue

                        if classification == "DUPLICATE_NOOP":
                            duplicate_noop_count += 1
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("DUPLICATE_NOOP", "Duplicate - no operation needed", row["row_id"]))
                            continue

                        if classification in ("CONFLICT_IN_BATCH", "LINE_IDENTITY_AMBIGUOUS"):
                            blocked_count += 1
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Conflict - blocked", row["row_id"]))
                            continue

                        normalized = json.loads(row["normalized_json"])
                        changes = json.loads(row["changes_json"])
                        try:
                            if classification == "NEW":
                                if order_id_for_group:
                                    order_id = order_id_for_group
                                    imported_order_ids.append(order_id)
                                    order_ids_added_in_this_group.append(order_id)
                                    _insert_order_line(conn, order_id, normalized, payload.batch_id, row["row_number"])
                                    if changes:
                                        try:
                                            _update_order(conn, order_id, changes, column_map)
                                        except Exception:
                                            pass
                                    _append_event(conn, order_id, "ORDER_IMPORTED_MULTI_LINE", {"batch_id": payload.batch_id, "source": "excel_import", "row_number": row_number})
                                else:
                                    order_id = _insert_order(conn, normalized, column_map, payload.batch_id, identity.user_id)
                                    imported_order_ids.append(order_id)
                                    order_ids_added_in_this_group.append(order_id)
                                    order_id_for_group = order_id
                                    _insert_order_line(conn, order_id, normalized, payload.batch_id, row["row_number"])
                                    _append_event(conn, order_id, "ORDER_IMPORTED_BASE_ONLY", {"batch_id": payload.batch_id, "source": "excel_import", "normalized": normalized, "action_readiness": "BASE_ONLY"})
                            elif classification in ("CONFLICT_EXISTING", "LINE_CONFLICT"):
                                if effective_action == "apply_correction":
                                    order_id = row["existing_order_id"]
                                    existing_line_id = row["existing_line_id"] if "existing_line_id" in row.keys() else None
                                    if not order_id and order_no_column and order_id_column:
                                        current = conn.execute(f'SELECT "{order_id_column}" FROM orders WHERE "{order_no_column}"=?', (normalized.get("order_no"),)).fetchone()
                                        order_id = current[0] if current else None
                                    if not order_id:
                                        raise ValueError("找不到已有订单的系统ID")

                                    order_changes = _extract_order_changes(changes)
                                    line_changes = _extract_line_changes(changes)

                                    if classification == "LINE_CONFLICT":
                                        cd_json = row["conflict_details_json"] if "conflict_details_json" in row.keys() else None
                                        if cd_json:
                                            conflict_details = json.loads(cd_json)
                                            conflict_line_changes = _extract_line_changes(conflict_details)
                                            if conflict_line_changes:
                                                line_changes = conflict_line_changes

                                    # FIX 04: Order-level correction - update orders only
                                    if order_changes:
                                        _update_order(conn, order_id, order_changes, column_map)
                                        _insert_correction_record(
                                            conn, order_id, normalized.get("source_order_key"),
                                            payload.batch_id, identity.user_id, "order",
                                            order_id, order_changes
                                        )

                                    # FIX 04: Line-level correction - update order_lines, not INSERT
                                    if line_changes and existing_line_id:
                                        _update_order_line(conn, existing_line_id, normalized, line_changes)
                                        _insert_correction_record(
                                            conn, order_id, normalized.get("source_order_key"),
                                            payload.batch_id, identity.user_id, "order_line",
                                            existing_line_id, line_changes
                                        )
                                    elif line_changes and not existing_line_id:
                                        # Fallback: line changes but no existing_line_id (no source_line_key match)
                                        # Update orders table for order-level, leave line as-is
                                        pass

                                    corrected_count += 1
                                    order_was_corrected = True
                                    imported_order_ids.append(order_id)
                                    order_ids_added_in_this_group.append(order_id)
                                    order_id_for_group = order_id

                                    # FIX 09: event_logs must NOT contain full changes data
                                    _append_event(conn, order_id, "ORDER_IMPORT_CORRECTED", {
                                        "batch_id": payload.batch_id,
                                        "order_id": order_id,
                                        "correction_count": 1,
                                        "action": "apply_correction",
                                    })
                                else:
                                    blocked_count += 1
                                    conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("BLOCKED", "Conflict unresolved", row["row_id"]))
                                    continue
                            else:
                                continue

                            if has_warning:
                                order_has_warning = True
                                warning_row_count += 1
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=?,existing_order_id=? WHERE row_id=?", ("COMMITTED", "", order_id_for_group or order_id, row["row_id"]))
                        except Exception as exc:
                            order_failed = True
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("COMMIT_FAILED", str(exc), row["row_id"]))
                            failures.append({"row_number": row["row_number"], "message": str(exc), "order_key": order_key})

                    if order_failed:
                        _rollback_savepoint(conn, savepoint_name)
                        commit_failed_count += 1
                        for oid in order_ids_added_in_this_group:
                            if oid in imported_order_ids:
                                imported_order_ids.remove(oid)
                        for row in order_rows:
                            conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=?,existing_order_id=NULL WHERE row_id=?", ("COMMIT_FAILED", "订单组提交失败", row["row_id"]))
                        order_results.append({
                            "source_order_key": order_key,
                            "result_status": "COMMIT_FAILED",
                            "order_id": None,
                            "row_count": len(order_rows),
                        })
                    elif order_id_for_group is not None:
                        _release_savepoint(conn, savepoint_name)
                        order_success = True
                        if order_was_corrected:
                            corrected_count += 0
                            status = "CORRECTED"
                        elif order_has_warning:
                            success_with_warning_count += 1
                            status = "SUCCESS_WITH_WARNING"
                        else:
                            success_count += 1
                            status = "SUCCESS"
                        order_results.append({
                            "source_order_key": order_key,
                            "result_status": status,
                            "order_id": order_id_for_group,
                            "row_count": len(order_rows),
                        })
                    else:
                        _release_savepoint(conn, savepoint_name)

                except Exception as exc:
                    _rollback_savepoint(conn, savepoint_name)
                    commit_failed_count += 1
                    failures.append({"order_key": order_key, "message": str(exc)})
                    for row in order_rows:
                        conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=?,existing_order_id=NULL WHERE row_id=?", ("COMMIT_FAILED", str(exc), row["row_id"]))
                    order_results.append({
                        "source_order_key": order_key,
                        "result_status": "COMMIT_FAILED",
                        "order_id": None,
                        "row_count": len(order_rows),
                    })

            commit_duration_ms = int((_time.perf_counter() - commit_start) * 1000)
            completed_at = _now_iso()
            preflight_duration_ms = batch["preflight_duration_ms"] or 0
            processing_duration_ms = preflight_duration_ms + commit_duration_ms

            end_to_end_duration_ms = None

            result_summary = {
                "source_row_count": len(rows),
                "identified_order_count": len(grouped_rows),
                "inserted": success_count + success_with_warning_count,
                "updated": 0,
                "skipped": blocked_count,
                "failed": commit_failed_count,
                "success_count": success_count,
                "success_with_warning_count": success_with_warning_count,
                "blocked_count": blocked_count,
                "commit_failed_count": commit_failed_count,
                "warning_count": warning_row_count,
                "duplicate_noop_count": duplicate_noop_count,
                "conflict_count": conflict_count,
                "corrected_count": corrected_count,
                "imported_order_ids": list(dict.fromkeys(imported_order_ids)),
                "failures": failures,
                "order_results": order_results,
                "tasks_created": 0,
                "ranking_refresh_required": False,
                "ranking_refresh_note": "原始订单只建立事实底座；补充当前进展或最近沟通后，系统才会生成行动并进入排序。",
                "next_step_url": "/#activation",
                "_auth": batch_summary.get("_auth", {}),
            }
            conn.execute(
                "UPDATE order_import_batches SET status='COMMITTED',summary_json=?,committed_at=?,commit_completed_at=?,commit_duration_ms=?,end_to_end_duration_ms=?,processing_duration_ms=?,warning_count=?,success_count=?,success_with_warning_count=?,block_count=?,commit_failed_count=?,duplicate_noop_count=?,conflict_count=?,corrected_count=? WHERE batch_id=?",
                (json.dumps(result_summary, ensure_ascii=False), _now_iso(), completed_at, commit_duration_ms, end_to_end_duration_ms, processing_duration_ms, warning_row_count, success_count, success_with_warning_count, blocked_count, commit_failed_count, duplicate_noop_count, conflict_count, corrected_count, payload.batch_id),
            )
            track_event(
                conn, "import_commit_server_completed", organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role="manager" if identity.user_id == "MANAGER-1" else "operator",
                source="server",
                properties={
                    "import_batch_id": payload.batch_id,
                    "identified_order_count": len(grouped_rows),
                    "success_count": success_count + success_with_warning_count,
                    "success_with_warning_count": success_with_warning_count,
                    "blocked_count": blocked_count,
                    "commit_failed_count": commit_failed_count,
                    "excel_import_processing_duration_ms": processing_duration_ms,
                    "completed_at": completed_at,
                    "source_type": "excel_import",
                },
            )
            conn.commit()

        return {
            "status": "COMMITTED",
            "batch_id": payload.batch_id,
            "identified_order_count": len(grouped_rows),
            "success_count": success_count,
            "success_with_warning_count": success_with_warning_count,
            "blocked_count": blocked_count,
            "commit_failed_count": commit_failed_count,
            "warning_count": warning_row_count,
            "duplicate_noop_count": duplicate_noop_count,
            "conflict_count": conflict_count,
            "corrected_count": corrected_count,
            "imported_order_ids": list(dict.fromkeys(imported_order_ids)),
            "order_results": order_results,
            "processing_duration_ms": processing_duration_ms,
            "end_to_end_duration_ms": end_to_end_duration_ms,
        }

    @app.get("/api/import/batches/{batch_id}")
    def get_batch(batch_id: str, identity: CurrentIdentity = Depends(get_current_identity)):
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if not auth_meta.get("organization_id"):
                raise HTTPException(status_code=403, detail="旧导入批次缺少组织绑定")
            require_same_org(identity, auth_meta["organization_id"])
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="仅导入创建人或同组织主管可查看该批次")
            rows = conn.execute("SELECT row_number,classification,commit_status,commit_message FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (batch_id,)).fetchall()
        batch_dict = dict(batch)
        batch_dict["preflight_duration_ms"] = batch["preflight_duration_ms"]
        batch_dict["commit_duration_ms"] = batch["commit_duration_ms"]
        batch_dict["end_to_end_duration_ms"] = batch["end_to_end_duration_ms"]
        batch_dict["processing_duration_ms"] = batch["processing_duration_ms"]
        return {"batch": batch_dict, "rows": [dict(row) for row in rows]}

    @app.post("/api/import/track")
    def track_import_event(payload: dict, identity: CurrentIdentity = Depends(get_current_identity)):
        event_name = payload.get("event_name", "")
        properties = payload.get("properties", {})
        if not event_name:
            raise HTTPException(status_code=400, detail="event_name is required")
        with db() as conn:
            _ensure_patch_schema(conn)
            track_event(
                conn, event_name,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role="manager" if identity.user_id == "MANAGER-1" else "operator",
                source="client",
                properties=properties,
            )
            conn.commit()
        return {"ok": True}

    @app.get("/api/import/batches/{batch_id}/export")
    def export_batch_report(batch_id: str, identity: CurrentIdentity = Depends(get_current_identity)):
        import csv as _csv
        import io
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if not auth_meta.get("organization_id"):
                raise HTTPException(status_code=403, detail="缺少组织绑定")
            require_same_org(identity, auth_meta["organization_id"])
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="无权限查看该批次")
            rows = conn.execute("SELECT row_number,classification,commit_status,commit_message,raw_json,normalized_json,issues_json,existing_order_id FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (batch_id,)).fetchall()

        buffer = io.StringIO()
        writer = _csv.writer(buffer)
        writer.writerow([
            "import_batch_id", "source_order_key", "source_row_no", "result_status",
            "warning_or_error_code", "message", "order_id", "created_at",
            "客户名称", "产品名称", "订单数量", "交期", "负责人"
        ])
        for row in rows:
            raw = json.loads(row["raw_json"] or "{}")
            norm = json.loads(row["normalized_json"] or "{}")
            issues = json.loads(row["issues_json"] or "[]")
            error_msgs = [i["message"] for i in issues if i["level"] == "error"]
            warning_msgs = [i["message"] for i in issues if i["level"] == "warning"]
            issue_text = "; ".join(error_msgs + warning_msgs)

            error_codes = [i["code"] for i in issues if i["level"] == "error" and i.get("code")]
            warning_codes = [i["code"] for i in issues if i["level"] == "warning" and i.get("code")]
            codes_text = "; ".join(error_codes + warning_codes)

            classification = row["classification"]
            commit_status = row["commit_status"] or ""
            if commit_status == "COMMITTED":
                if any(i["level"] == "warning" for i in issues):
                    result_status = "SUCCESS_WITH_WARNING"
                else:
                    result_status = "SUCCESS"
            elif classification == "ERROR":
                result_status = "BLOCKED"
            elif commit_status == "COMMIT_FAILED":
                result_status = "COMMIT_FAILED"
            else:
                result_status = classification

            order_id = row["existing_order_id"] or norm.get("order_id", "")

            writer.writerow([
                batch_id,
                norm.get("source_order_key", norm.get("order_no", "")),
                row["row_number"],
                result_status,
                codes_text,
                issue_text,
                order_id,
                _now_iso(),
                norm.get("customer_name", raw.get("客户", "")),
                norm.get("product_name", raw.get("产品", "")),
                norm.get("order_qty", norm.get("quantity", "")),
                norm.get("delivery_date", norm.get("customer_delivery_date", "")),
                norm.get("owner", raw.get("负责人", "")),
            ])
        content = buffer.getvalue().encode("utf-8-sig")
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="import_report_{batch_id}.csv"'},
        )

    @app.post("/api/import/batches/{batch_id}/client-metrics")
    def post_client_metrics(batch_id: str, body: ClientMetricsRequest, identity: CurrentIdentity = Depends(get_current_identity)):
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if not auth_meta.get("organization_id"):
                raise HTTPException(status_code=403, detail="缺少组织绑定")
            require_same_org(identity, auth_meta["organization_id"])
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="无权限修改该批次")

            updates: list[str] = []
            values: list[Any] = []
            if body.client_end_to_end_duration_ms is not None:
                updates.append("end_to_end_duration_ms=?")
                values.append(body.client_end_to_end_duration_ms)

            if updates:
                values.append(batch_id)
                conn.execute(f"UPDATE order_import_batches SET {', '.join(updates)} WHERE batch_id=?", values)
                conn.commit()

            return {"status": "ok", "batch_id": batch_id}

    @app.get("/api/import/batches/{batch_id}/report")
    def get_batch_report(batch_id: str, identity: CurrentIdentity = Depends(get_current_identity)):
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if not auth_meta.get("organization_id"):
                raise HTTPException(status_code=403, detail="缺少组织绑定")
            require_same_org(identity, auth_meta["organization_id"])
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="无权限查看该批次")
            rows = conn.execute("SELECT row_number,classification,commit_status,commit_message,raw_json,normalized_json,issues_json,existing_order_id FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (batch_id,)).fetchall()

        report_rows = []
        for row in rows:
            raw = json.loads(row["raw_json"] or "{}")
            norm = json.loads(row["normalized_json"] or "{}")
            issues = json.loads(row["issues_json"] or "[]")
            error_msgs = [i["message"] for i in issues if i["level"] == "error"]
            warning_msgs = [i["message"] for i in issues if i["level"] == "warning"]
            error_codes = [i["code"] for i in issues if i["level"] == "error" and i.get("code")]
            warning_codes = [i["code"] for i in issues if i["level"] == "warning" and i.get("code")]
            classification = row["classification"]
            commit_status = row["commit_status"] or ""
            if commit_status == "COMMITTED":
                if any(i["level"] == "warning" for i in issues):
                    result_status = "SUCCESS_WITH_WARNING"
                else:
                    result_status = "SUCCESS"
            elif classification == "ERROR":
                result_status = "BLOCKED"
            elif commit_status == "COMMIT_FAILED":
                result_status = "COMMIT_FAILED"
            else:
                result_status = classification
            report_rows.append({
                "source_order_key": norm.get("source_order_key") or norm.get("order_no", ""),
                "source_row_no": row["row_number"],
                "result_status": result_status,
                "message": "; ".join(error_msgs + warning_msgs),
                "warning_or_error_code": "; ".join(error_codes + warning_codes),
                "order_id": row["existing_order_id"],
                "missing_information": norm.get("missing_information", []),
            })

        summary = {
            "source_row_count": batch_summary.get("source_row_count", len(rows)),
            "identified_order_count": batch_summary.get("identified_order_count", 0),
            "success_count": batch_summary.get("success_count", 0),
            "success_with_warning_count": batch_summary.get("success_with_warning_count", 0),
            "blocked_count": batch_summary.get("blocked_count", 0),
            "commit_failed_count": batch_summary.get("commit_failed_count", 0),
            "warning_count": batch_summary.get("warning_count", 0),
        }

        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "summary": summary,
            "total_rows": batch["total_rows"],
            "imported_order_ids": batch_summary.get("imported_order_ids", []),
            "order_results": batch_summary.get("order_results", []),
            "rows": report_rows,
        }

    @app.get("/api/import/batches/{batch_id}/report.csv")
    def get_batch_report_csv(batch_id: str, identity: CurrentIdentity = Depends(get_current_identity)):
        return export_batch_report(batch_id=batch_id, identity=identity)

    @app.post("/api/import/batches/{batch_id}/retry-failed")
    def retry_failed_orders(
        batch_id: str,
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        with db() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            batch_summary = json.loads(batch["summary_json"] or "{}")
            auth_meta = batch_summary.get("_auth") or {}
            if not auth_meta.get("organization_id"):
                raise HTTPException(status_code=403, detail="旧导入批次缺少组织绑定")
            require_same_org(identity, auth_meta["organization_id"])
            if not identity.is_manager() and auth_meta.get("created_by") != identity.user_id:
                raise HTTPException(status_code=403, detail="仅导入创建人或同组织主管可重试")

            # FIX 01: Find failed rows - use normalized_json to get source_order_key
            failed_rows = conn.execute(
                "SELECT * FROM order_import_rows WHERE batch_id=? AND commit_status='COMMIT_FAILED' ORDER BY row_number",
                (batch_id,),
            ).fetchall()

            if not failed_rows:
                raise HTTPException(status_code=400, detail="该批次没有可重试的失败订单")

            # FIX 01: Group failed rows by order and rebuild records with proper row_number
            mapping = json.loads(batch["mapping_json"] or "{}")
            retry_attempt = (batch["retry_attempt"] or 0) + 1

            # Build records from failed rows' raw_json, preserving __row_number__
            records = []
            for row in failed_rows:
                raw_json = json.loads(row["raw_json"] or "{}")
                row_number = row["row_number"]
                raw_json["__row_number__"] = row_number
                records.append(raw_json)

            # FIX 01: Call _preview_rows with correct signature (conn, records, mapping, user_id)
            rows, summary, projection_hash = _preview_rows(
                conn, records, mapping, identity.user_id
            )

            # FIX 01: Create child batch with retry metadata
            new_batch_id = f"IMP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            now = _now_iso()

            conn.execute(
                """INSERT INTO order_import_batches
                   (batch_id, source_filename, source_sha256, status, total_rows, importable_rows, error_rows,
                    mapping_json, summary_json, created_at, started_at, preflight_completed_at,
                    preflight_duration_ms, projection_hash, warning_count, block_count,
                    retry_of_batch_id, retry_attempt, organization_id, created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_batch_id,
                    f"Retry of {batch_id}",
                    "",
                    "PREVIEWED",
                    summary["source_row_count"],
                    summary["new"] + summary.get("update", 0),
                    summary.get("error", 0),
                    json.dumps(mapping, ensure_ascii=False),
                    json.dumps({**summary, "_auth": auth_meta, "retry_of_batch_id": batch_id, "retry_attempt": retry_attempt}, ensure_ascii=False),
                    now,
                    now,
                    now,
                    0,
                    projection_hash,
                    summary.get("warning", 0),
                    summary.get("error", 0),
                    batch_id,
                    retry_attempt,
                    str(identity.organization_id),
                    identity.user_id,
                ),
            )

            # FIX 01: Insert preview rows for the child batch
            for row in rows:
                conn.execute(
                    """INSERT INTO order_import_rows
                       (row_id, batch_id, row_number, raw_json, normalized_json,
                        classification, issues_json, changes_json, existing_order_id,
                        source_order_key, source_line_key, conflict_type, conflict_details_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["row_id"],
                        new_batch_id,
                        row["row_number"],
                        json.dumps(row["raw"], ensure_ascii=False),
                        json.dumps(row["normalized"], ensure_ascii=False),
                        row["classification"],
                        json.dumps(row["issues"], ensure_ascii=False),
                        json.dumps(row["changes"], ensure_ascii=False),
                        row.get("existing_order_id"),
                        row.get("source_order_key"),
                        row.get("source_line_key"),
                        row.get("conflict_type"),
                        json.dumps(row.get("conflict_details"), ensure_ascii=False) if row.get("conflict_details") else None,
                    ),
                )

            conn.commit()

        return {
            "batch_id": new_batch_id,
            "retry_of_batch_id": batch_id,
            "retry_attempt": retry_attempt,
            "status": "PREVIEW",
            "projection_hash": projection_hash,
            "summary": summary,
            "rows": rows,
            "source_row_count": summary.get("source_row_count", 0),
            "identified_order_count": summary.get("identified_order_count", 0),
        }
