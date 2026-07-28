from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

PATCH_VERSION = "3.1.0-excel-import"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ROWS = 500
MAX_COLUMNS = 80

CANONICAL_FIELDS: dict[str, dict[str, Any]] = {
    "order_no": {"label": "订单号", "required": True, "aliases": ["订单号", "订单编号", "po", "po no", "po_no", "po number", "pono", "order no", "order_no", "orderno"]},
    "customer_name": {"label": "客户名称", "required": True, "aliases": ["客户", "客户名称", "customer", "customer name", "customer_name", "buyer"]},
    "sku": {"label": "SKU/货号", "required_any": "product", "aliases": ["sku", "SKU/货号", "货号", "款号", "产品编号", "item no", "item_no", "itemno", "product code", "product_code"]},
    "product_name": {"label": "产品名称", "required_any": "product", "aliases": ["产品", "产品名称", "品名", "product", "product name", "product_name", "item"]},
    "quantity": {"label": "数量", "required": True, "aliases": ["数量", "qty", "quantity", "order qty", "order_qty"]},
    "unit": {"label": "单位", "required": True, "aliases": ["单位", "unit", "uom"]},
    "customer_delivery_date": {"label": "客户正式交期", "required": True, "aliases": ["客户交期", "客户正式交期", "正式交期", "交货日期", "交期", "delivery date", "delivery_date", "customer delivery date", "customer_delivery_date"]},
    "current_node": {"label": "当前节点", "aliases": ["当前节点", "当前环节", "current node", "current_node", "current stage", "current_stage", "stage"]},
    "factory_name": {"label": "工厂名称", "aliases": ["工厂", "工厂名称", "供应商", "supplier", "supplier name", "factory", "factory_name"]},
    "latest_supplier_commitment": {"label": "最新工厂承诺", "aliases": ["工厂承诺", "最新工厂承诺", "供应商承诺", "supplier commitment", "supplier_commitment", "latest_supplier_commitment", "factory commitment"]},
    "current_progress": {"label": "当前进度", "aliases": ["当前进度", "进度", "progress", "current_progress"]},
    "owner": {"label": "负责人", "aliases": ["负责人", "跟单员", "owner", "assignee"]},
    "packaging_method": {"label": "包装方式", "aliases": ["包装方式", "包装", "packaging", "packaging_method"]},
    "specification": {"label": "规格", "aliases": ["规格", "specification", "spec"]},
    "material": {"label": "材质", "aliases": ["材质", "material"]},
    "color": {"label": "颜色", "aliases": ["颜色", "color"]},
    "logo_process": {"label": "Logo工艺", "aliases": ["logo工艺", "logo process", "logo_process", "工艺", "process"]},
}

ORDER_COLUMN_ALIASES: dict[str, list[str]] = {
    "order_id": ["order_id", "id"],
    "order_no": ["order_no", "po_no", "po_number"],
    "customer_name": ["customer_name", "customer"],
    "sku": ["sku", "product_sku", "item_no"],
    "product_name": ["product_name", "product"],
    "quantity": ["quantity", "qty"],
    "unit": ["unit", "uom"],
    "customer_delivery_date": ["customer_delivery_date", "requested_delivery_date", "delivery_date"],
    "current_node": ["current_node", "current_stage", "stage"],
    "factory_name": ["factory_name", "factory", "supplier_name"],
    "latest_supplier_commitment": ["latest_supplier_commitment", "supplier_commitment_date", "factory_commitment_date"],
    "current_progress": ["current_progress", "progress"],
    "owner": ["owner", "assignee"],
    "packaging_method": ["packaging_method", "packaging"],
    "specification": ["specification", "spec"],
    "material": ["material"],
    "color": ["color"],
    "logo_process": ["logo_process", "process"],
    "status": ["status"],
    "risk_level": ["risk_level"],
    "created_at": ["created_at"],
    "updated_at": ["updated_at"],
}

SQL_TYPE_BY_FIELD = {
    "order_no": "TEXT",
    "customer_name": "TEXT",
    "sku": "TEXT",
    "product_name": "TEXT",
    "quantity": "REAL",
    "unit": "TEXT",
    "customer_delivery_date": "TEXT",
    "current_node": "TEXT",
    "factory_name": "TEXT",
    "latest_supplier_commitment": "TEXT",
    "current_progress": "REAL",
    "owner": "TEXT",
    "packaging_method": "TEXT",
    "specification": "TEXT",
    "material": "TEXT",
    "color": "TEXT",
    "logo_process": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


class PreviewRequest(BaseModel):
    filename: str
    content_base64: str
    mapping: dict[str, str] | None = None  # source header -> canonical field


class CommitRequest(BaseModel):
    batch_id: str
    import_key: str | None = None
    row_actions: dict[str, str] = Field(default_factory=dict)  # row number -> import/skip


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _db_path() -> str:
    configured = os.environ.get("DB_PATH")
    if configured:
        return configured
    candidates = ["data/action_layer.db", "action_layer.db", "data/app.db", "app.db"]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    Path("data").mkdir(exist_ok=True)
    return "data/action_layer.db"


def _connect() -> sqlite3.Connection:
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    return list(conn.execute(f'PRAGMA table_info("{table}")'))


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in _columns(conn, table)}


def _resolve_column(existing: Iterable[str], canonical: str) -> str | None:
    names = set(existing)
    for alias in ORDER_COLUMN_ALIASES.get(canonical, [canonical]):
        if alias in names:
            return alias
    return None


def _ensure_patch_schema(conn: sqlite3.Connection) -> None:
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
            committed_at TEXT
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
        CREATE INDEX IF NOT EXISTS idx_order_import_rows_batch ON order_import_rows(batch_id);
        CREATE INDEX IF NOT EXISTS idx_order_import_rows_classification ON order_import_rows(classification);
        """
    )
    if not _table_exists(conn, "orders"):
        conn.execute(
            """
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_no TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                sku TEXT,
                product_name TEXT,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                customer_delivery_date TEXT NOT NULL,
                current_node TEXT,
                factory_name TEXT,
                latest_supplier_commitment TEXT,
                current_progress REAL,
                owner TEXT,
                packaging_method TEXT,
                specification TEXT,
                material TEXT,
                color TEXT,
                logo_process TEXT,
                status TEXT DEFAULT 'ACTIVE',
                risk_level TEXT DEFAULT 'low',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    existing = _column_names(conn, "orders")
    for canonical, sql_type in SQL_TYPE_BY_FIELD.items():
        if _resolve_column(existing, canonical) is None:
            conn.execute(f'ALTER TABLE orders ADD COLUMN "{canonical}" {sql_type}')
            existing.add(canonical)
    conn.commit()


def _normal_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-./\\()（）:：]+", "", text)


def _auto_mapping(headers: list[str]) -> dict[str, str]:
    alias_index: dict[str, str] = {}
    for canonical, config in CANONICAL_FIELDS.items():
        for alias in config.get("aliases", []):
            alias_index[_normal_header(alias)] = canonical
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


def _parse_number(value: Any, field: str) -> tuple[float | None, str | None]:
    text = _clean_text(value)
    if text is None:
        return None, None
    cleaned = text.replace(",", "").replace("件", "").replace("pcs", "").replace("%", "").strip()
    try:
        number = float(cleaned)
    except ValueError:
        return None, f"{CANONICAL_FIELDS[field]['label']}不是有效数字"
    if field == "quantity" and number <= 0:
        return None, "数量必须大于0"
    if field == "current_progress":
        if "%" in text:
            number /= 100
        elif 1 < number <= 100:
            number /= 100
        if not 0 <= number <= 1:
            return None, "当前进度应在0%—100%之间"
    return number, None


def _parse_date(value: Any, label: str) -> tuple[str | None, str | None]:
    if value is None or str(value).strip() == "":
        return None, None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(value).strip()):
        number = float(value)
        if 20000 <= number <= 80000:
            return (date(1899, 12, 30) + timedelta(days=int(number))).isoformat(), None
    text = str(value).strip()
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    normalized = re.sub(r"\s+.*$", "", normalized)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d", "%m-%d-%Y", "%Y-%m"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            if fmt == "%Y-%m":
                return None, f"{label}必须填写到具体日期"
            return parsed.date().isoformat(), None
        except ValueError:
            continue
    if any(word in text for word in ("月底", "月初", "下周", "明天", "尽快", "左右", "大概")):
        return None, f"{label}含模糊或相对日期，请改为YYYY-MM-DD"
    return None, f"{label}格式无法识别，请使用YYYY-MM-DD"


def _normalize_record(raw: dict[str, Any], mapping: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    source_by_canonical: dict[str, str] = {}
    for source_header, canonical in mapping.items():
        if canonical not in CANONICAL_FIELDS or source_header not in raw:
            continue
        source_by_canonical[canonical] = source_header
        value = raw.get(source_header)
        if canonical in ("quantity", "current_progress"):
            parsed, error = _parse_number(value, canonical)
            normalized[canonical] = parsed
            if error:
                issues.append({"field": canonical, "source_header": source_header, "level": "error", "message": error})
        elif canonical in ("customer_delivery_date", "latest_supplier_commitment"):
            parsed, error = _parse_date(value, CANONICAL_FIELDS[canonical]["label"])
            normalized[canonical] = parsed
            if error:
                issues.append({"field": canonical, "source_header": source_header, "level": "error", "message": error})
        else:
            normalized[canonical] = _clean_text(value)

    for canonical, config in CANONICAL_FIELDS.items():
        if config.get("required") and not normalized.get(canonical):
            issues.append({"field": canonical, "source_header": source_by_canonical.get(canonical), "level": "error", "message": f"缺少必填字段：{config['label']}"})
    if not normalized.get("sku") and not normalized.get("product_name"):
        issues.append({"field": "product", "source_header": None, "level": "error", "message": "SKU/货号与产品名称至少填写一项"})
    return normalized, issues


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


def _map_order_columns(conn: sqlite3.Connection) -> dict[str, str]:
    existing = _column_names(conn, "orders")
    result: dict[str, str] = {}
    for canonical in ORDER_COLUMN_ALIASES:
        resolved = _resolve_column(existing, canonical)
        if resolved:
            result[canonical] = resolved
    return result


def _row_to_canonical(row: sqlite3.Row, column_map: dict[str, str]) -> dict[str, Any]:
    return {canonical: row[column] for canonical, column in column_map.items() if column in row.keys()}


def _preview_rows(conn: sqlite3.Connection, records: list[dict[str, Any]], mapping: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    order_columns = _map_order_columns(conn)
    order_no_column = order_columns.get("order_no")
    order_id_column = order_columns.get("order_id")
    seen_order_nos: set[str] = set()
    rows: list[dict[str, Any]] = []
    summary = {"total": 0, "new": 0, "update": 0, "duplicate": 0, "error": 0, "review": 0}
    for raw in records:
        row_number = int(raw.get("__row_number__", 0))
        raw_public = {k: v for k, v in raw.items() if not k.startswith("__")}
        normalized, issues = _normalize_record(raw_public, mapping)
        order_no = normalized.get("order_no")
        if order_no and order_no in seen_order_nos:
            issues.append({"field": "order_no", "source_header": None, "level": "error", "message": "同一文件中订单号重复"})
        if order_no:
            seen_order_nos.add(order_no)

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

        if any(issue["level"] == "error" for issue in issues):
            classification = "ERROR"
        elif existing_row and changes:
            classification = "UPDATE"
        elif existing_row:
            classification = "DUPLICATE"
        else:
            classification = "NEW"
        summary[classification.lower()] += 1
        summary["total"] += 1
        rows.append(
            {
                "row_id": f"ROW-{uuid.uuid4().hex[:12]}",
                "row_number": row_number,
                "raw": raw_public,
                "normalized": normalized,
                "classification": classification,
                "issues": issues,
                "changes": changes,
                "existing_order_id": existing_order_id,
            }
        )
    return rows, summary


def _required_db_fill(column: str, canonical: str | None, now: str) -> Any:
    defaults = {
        "status": "ACTIVE",
        "risk_level": "low",
        "current_node": "资料确认",
        "current_stage": "资料确认",
        "stage": "资料确认",
        "current_progress": 0,
        "progress": 0,
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


def _insert_order(conn: sqlite3.Connection, normalized: dict[str, Any], column_map: dict[str, str], batch_id: str) -> str:
    now = _now_iso()
    order_id = f"ORD-IMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    values_by_column: dict[str, Any] = {}
    if "order_id" in column_map:
        values_by_column[column_map["order_id"]] = order_id
    for canonical, value in normalized.items():
        column = column_map.get(canonical)
        if column and value is not None:
            values_by_column[column] = value
    if "created_at" in column_map:
        values_by_column.setdefault(column_map["created_at"], now)
    if "updated_at" in column_map:
        values_by_column[column_map["updated_at"]] = now
    if "status" in column_map:
        values_by_column.setdefault(column_map["status"], "ACTIVE")
    if "risk_level" in column_map:
        values_by_column.setdefault(column_map["risk_level"], "low")
    if "current_node" in column_map:
        values_by_column.setdefault(column_map["current_node"], "资料确认")
    if "current_progress" in column_map:
        values_by_column.setdefault(column_map["current_progress"], 0)
    if "owner" in column_map:
        values_by_column.setdefault(column_map["owner"], "待分配")

    canonical_by_column = {column: canonical for canonical, column in column_map.items()}
    for info in _columns(conn, "orders"):
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


def _update_order(conn: sqlite3.Connection, existing_order_id: str, changes: list[dict[str, Any]], column_map: dict[str, str]) -> None:
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
        assignments.append(f'"{column}"=?')
        values.append(change.get("new_value"))
    if "updated_at" in column_map:
        assignments.append(f'"{column_map["updated_at"]}"=?')
        values.append(_now_iso())
    if assignments:
        values.append(existing_order_id)
        conn.execute(f'UPDATE orders SET {", ".join(assignments)} WHERE "{order_id_column}"=?', values)


def _insert_import_task(conn: sqlite3.Connection, order_id: str, order_no: str, is_update: bool) -> bool:
    if not _table_exists(conn, "tasks"):
        return False
    columns = _column_names(conn, "tasks")
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


def _append_event(conn: sqlite3.Connection, order_id: str, event_type: str, payload: dict[str, Any]) -> None:
    if not _table_exists(conn, "event_logs"):
        return
    columns = _column_names(conn, "event_logs")
    now = _now_iso()
    candidates = {
        "event_id": f"EVT-IMP-{uuid.uuid4().hex[:10].upper()}",
        "order_id": order_id,
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


def _auth_required() -> bool:
    return bool(os.environ.get("IMPORT_ADMIN_KEY") or os.environ.get("APP_API_KEY"))


def _check_import_key(provided: str | None) -> None:
    expected = os.environ.get("IMPORT_ADMIN_KEY") or os.environ.get("APP_API_KEY")
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="导入密钥错误")


def _xlsx_template() -> bytes:
    headers = [config["label"] for config in CANONICAL_FIELDS.values()]
    sample = ["PO-IMPORT-001", "示例客户", "SKU-001", "示例产品", "1000", "pcs", "2026-08-20", "资料确认", "示例工厂", "2026-08-10", "0%", "张三", "彩盒", "M码", "棉", "蓝色", "丝印"]

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
    writer.writerow(["PO-IMPORT-001", "示例客户", "SKU-001", "示例产品", 1000, "pcs", "2026-08-20", "资料确认", "示例工厂", "2026-08-10", "0%", "张三", "彩盒", "M码", "棉", "蓝色", "丝印"])
    return buffer.getvalue().encode("utf-8-sig")


IMPORT_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>订单批量导入</title><link rel="stylesheet" href="/api/import/assets/style.css"></head><body>
<header><a class="back" href="/">← 返回工作台</a><div><h1>订单批量导入</h1><p>Excel/CSV → 字段映射 → 校验预览 → 人工确认 → 写入订单</p></div><span class="version">V3.1</span></header>
<main>
<section class="panel"><div class="step-title"><b>1</b><div><h2>上传订单文件</h2><p>支持 .xlsx 与 .csv，单次最多500行、5MB。旧版 .xls 请先另存为 .xlsx。</p></div></div><div class="upload-row"><label class="file-box"><input id="file" type="file" accept=".xlsx,.csv"><span id="file-name">选择文件或拖入文件</span></label><button id="preview-btn" class="primary">读取并预览</button></div><div class="template-links"><a href="/api/import/template.xlsx">下载Excel模板</a><a href="/api/import/template.csv">下载CSV模板</a></div></section>
<section id="mapping-panel" class="panel hidden"><div class="step-title"><b>2</b><div><h2>确认字段映射</h2><p>系统自动匹配表头；未匹配或匹配错误的列可手动调整。</p></div></div><div id="mapping-grid" class="mapping-grid"></div><button id="repreview-btn" class="secondary">按当前映射重新校验</button></section>
<section id="result-panel" class="panel hidden"><div class="step-title"><b>3</b><div><h2>校验结果</h2><p>错误行不会写入；更新已有订单时只展示发生变化的字段。</p></div></div><div id="summary" class="summary"></div><div class="toolbar"><label>筛选 <select id="status-filter"><option value="ALL">全部</option><option value="NEW">新增</option><option value="UPDATE">更新</option><option value="DUPLICATE">重复</option><option value="ERROR">错误</option></select></label><span id="schema-warning"></span></div><div class="table-wrap"><table><thead><tr><th>行</th><th>状态</th><th>订单号</th><th>客户</th><th>产品/SKU</th><th>数量</th><th>交期</th><th>问题/变化</th></tr></thead><tbody id="rows"></tbody></table></div><div class="commit-box"><label id="key-wrap" class="hidden">导入密钥<input id="import-key" type="password" autocomplete="off" placeholder="Render中的IMPORT_ADMIN_KEY"></label><button id="commit-btn" class="primary">确认导入有效数据</button><p>导入会新增订单或更新已确认的变化；重复和错误行自动跳过。原文件不会被修改。</p></div></section>
<section id="commit-result" class="panel hidden"></section>
</main><div id="toast"></div><script src="/api/import/assets/app.js"></script></body></html>'''

IMPORT_CSS = r''':root{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#17201d;background:#f4f4f3}*{box-sizing:border-box}body{margin:0}header{height:88px;background:#fff;border-bottom:1px solid #dde2dc;display:flex;align-items:center;gap:22px;padding:0 5vw;position:sticky;top:0;z-index:5}header h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:#68736e;font-size:13px}.back{color:#092923;text-decoration:none;font-weight:600}.version{margin-left:auto;background:#e8eee9;color:#092923;padding:6px 10px;border-radius:999px;font-size:12px}main{max-width:1180px;margin:28px auto;padding:0 20px 80px}.panel{background:#fff;border:1px solid #dde2dc;border-radius:16px;padding:24px;margin-bottom:18px;box-shadow:0 8px 24px rgba(29,55,105,.05)}.hidden{display:none!important}.step-title{display:flex;gap:14px;align-items:flex-start}.step-title>b{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#092923;color:#fff}.step-title h2{margin:1px 0 5px;font-size:18px}.step-title p{margin:0;color:#68736e;font-size:13px}.upload-row{display:flex;gap:12px;margin-top:20px}.file-box{flex:1;border:1px dashed #b8c0b9;background:#fafaf8;border-radius:12px;height:54px;display:flex;align-items:center;padding:0 16px;color:#3f4d44;cursor:pointer}.file-box input{display:none}button{border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}.primary{background:#092923;color:#fff}.primary:disabled{opacity:.5;cursor:not-allowed}.secondary{background:#e8eee9;color:#3f4d44}.template-links{display:flex;gap:16px;margin-top:12px}.template-links a{font-size:13px;color:#092923}.mapping-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}.mapping-item{border:1px solid #dde2dc;border-radius:10px;padding:11px}.mapping-item label{font-size:12px;color:#68736e;display:block;margin-bottom:6px}.mapping-item select{width:100%;border:1px solid #cfd6cf;border-radius:8px;padding:9px;background:#fff}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:20px 0}.summary-card{border:1px solid #dde2dc;border-radius:12px;padding:14px}.summary-card span{display:block;color:#68736e;font-size:12px}.summary-card strong{display:block;font-size:24px;margin-top:5px}.toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;color:#68736e;font-size:13px}.toolbar select{padding:7px 10px;border:1px solid #cfd6cf;border-radius:8px}.table-wrap{overflow:auto;border:1px solid #dde2dc;border-radius:12px}table{width:100%;border-collapse:collapse;min-width:980px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e9ece8;font-size:13px;vertical-align:top}th{background:#fafaf8;color:#3f4d44}.badge{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700}.NEW{background:#e9f3ed;color:#216844}.UPDATE{background:#f5ecdf;color:#8b5b2f}.DUPLICATE{background:#ecefea;color:#68736e}.ERROR{background:#f7e8e6;color:#9c3d36}.detail{max-width:340px;color:#68736e}.detail div{margin-bottom:4px}.commit-box{display:flex;align-items:end;gap:14px;margin-top:18px;flex-wrap:wrap}.commit-box label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#68736e}.commit-box input{width:280px;padding:10px;border:1px solid #cfd6cf;border-radius:8px}.commit-box p{width:100%;margin:0;color:#7a849a;font-size:12px}#commit-result h2{margin-top:0}.result-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.result-grid div{background:#fafaf8;border-radius:12px;padding:16px}.result-grid strong{display:block;font-size:24px}#toast{position:fixed;right:24px;bottom:24px;background:#17201d;color:#fff;padding:12px 16px;border-radius:10px;opacity:0;transform:translateY(10px);transition:.2s;pointer-events:none}#toast.show{opacity:1;transform:none}@media(max-width:800px){header{height:auto;padding:16px 18px;flex-wrap:wrap}.version{margin-left:0}.mapping-grid{grid-template-columns:1fr}.summary{grid-template-columns:repeat(2,1fr)}.upload-row{flex-direction:column}.result-grid{grid-template-columns:repeat(2,1fr)}}'''

IMPORT_JS = r'''let state={file:null,contentBase64:null,headers:[],mapping:{},rows:[],batchId:null,authRequired:false};
const $=s=>document.querySelector(s);const toast=m=>{const el=$("#toast");el.textContent=m;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2500)};
function bytesToBase64(buffer){let binary="";const bytes=new Uint8Array(buffer);const chunk=0x8000;for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,Math.min(i+chunk,bytes.length)));return btoa(binary)}
async function loadFile(file){if(!file)throw new Error("请选择文件");if(file.size>5*1024*1024)throw new Error("文件不能超过5MB");state.file=file;state.contentBase64=bytesToBase64(await file.arrayBuffer());}
function fieldOptions(selected=""){const fields=window.__fields||{};return `<option value="">忽略此列</option>`+Object.entries(fields).map(([k,v])=>`<option value="${k}" ${k===selected?'selected':''}>${v.label}${v.required?' *':''}</option>`).join("")}
function renderMapping(){const grid=$("#mapping-grid");grid.innerHTML=state.headers.map(h=>`<div class="mapping-item"><label>${escapeHtml(h)}</label><select data-header="${escapeAttr(h)}">${fieldOptions(state.mapping[h]||"")}</select></div>`).join("");grid.querySelectorAll("select").forEach(s=>s.addEventListener("change",()=>state.mapping[s.dataset.header]=s.value));$("#mapping-panel").classList.remove("hidden")}
function renderSummary(summary){const labels={total:"总行数",new:"可新增",update:"可更新",duplicate:"完全重复",error:"错误"};$("#summary").innerHTML=Object.entries(labels).map(([k,l])=>`<div class="summary-card"><span>${l}</span><strong>${summary[k]||0}</strong></div>`).join("")}
function rowDetail(r){const problems=(r.issues||[]).map(i=>`<div>⚠ ${escapeHtml(i.message)}</div>`);const changes=(r.changes||[]).map(c=>`<div>${escapeHtml(c.label)}：${escapeHtml(String(c.old_value??"空"))} → ${escapeHtml(String(c.new_value??"空"))}</div>`);return [...problems,...changes].join("")||"—"}
function renderRows(){const filter=$("#status-filter").value;const rows=state.rows.filter(r=>filter==="ALL"||r.classification===filter);$("#rows").innerHTML=rows.map(r=>`<tr><td>${r.row_number}</td><td><span class="badge ${r.classification}">${r.classification}</span></td><td>${escapeHtml(r.normalized.order_no||"")}</td><td>${escapeHtml(r.normalized.customer_name||"")}</td><td>${escapeHtml(r.normalized.sku||r.normalized.product_name||"")}</td><td>${escapeHtml(String(r.normalized.quantity??""))} ${escapeHtml(r.normalized.unit||"")}</td><td>${escapeHtml(r.normalized.customer_delivery_date||"")}</td><td class="detail">${rowDetail(r)}</td></tr>`).join("")}
async function preview(useCurrentMapping=false){try{$("#preview-btn").disabled=true;if(!state.file)await loadFile($("#file").files[0]);const payload={filename:state.file.name,content_base64:state.contentBase64,mapping:useCurrentMapping?state.mapping:null};const res=await fetch("/api/import/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const data=await res.json();if(!res.ok)throw new Error(data.detail||"预览失败");window.__fields=data.fields;state.headers=data.headers;state.mapping=data.mapping;state.rows=data.rows;state.batchId=data.batch_id;state.authRequired=data.auth_required;renderMapping();renderSummary(data.summary);renderRows();$("#result-panel").classList.remove("hidden");$("#key-wrap").classList.toggle("hidden",!data.auth_required);$("#schema-warning").textContent=data.schema_notes?.join("；")||"";toast("校验完成");}catch(e){toast(e.message)}finally{$("#preview-btn").disabled=false}}
async function commit(){if(!state.batchId)return;try{$("#commit-btn").disabled=true;const res=await fetch("/api/import/commit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({batch_id:state.batchId,import_key:$("#import-key").value||null,row_actions:{}})});const data=await res.json();if(!res.ok)throw new Error(data.detail||"导入失败");$("#commit-result").classList.remove("hidden");$("#commit-result").innerHTML=`<h2>导入完成</h2><div class="result-grid"><div><span>新增</span><strong>${data.inserted}</strong></div><div><span>更新</span><strong>${data.updated}</strong></div><div><span>跳过</span><strong>${data.skipped}</strong></div><div><span>失败</span><strong>${data.failed}</strong></div></div><p>已创建/更新订单，并尽可能生成“核对导入资料”的待确认任务。请返回订单中心查看结果。</p><a class="back" href="/">返回工作台</a>`;toast("导入成功");window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});}catch(e){toast(e.message)}finally{$("#commit-btn").disabled=false}}
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]))}function escapeAttr(v){return escapeHtml(v)}
$("#file").addEventListener("change",async e=>{try{await loadFile(e.target.files[0]);$("#file-name").textContent=state.file.name}catch(err){toast(err.message)}});$("#preview-btn").addEventListener("click",()=>preview(false));$("#repreview-btn").addEventListener("click",()=>preview(true));$("#status-filter").addEventListener("change",renderRows);$("#commit-btn").addEventListener("click",commit);'''

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
        with _connect() as conn:
            _ensure_patch_schema(conn)
            columns = sorted(_column_names(conn, "orders"))
        return {
            "patch_version": PATCH_VERSION,
            "supported_formats": [".xlsx", ".csv"],
            "max_rows": MAX_ROWS,
            "max_file_bytes": MAX_FILE_BYTES,
            "auth_required": _auth_required(),
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
    def preview_import(payload: PreviewRequest):
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
        with _connect() as conn:
            _ensure_patch_schema(conn)
            rows, summary = _preview_rows(conn, records, mapping)
            batch_id = f"IMP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            now = _now_iso()
            conn.execute(
                "INSERT INTO order_import_batches(batch_id,source_filename,source_sha256,status,total_rows,importable_rows,error_rows,mapping_json,summary_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    Path(payload.filename).name,
                    hashlib.sha256(content).hexdigest(),
                    "PREVIEWED",
                    summary["total"],
                    summary["new"] + summary["update"],
                    summary["error"],
                    json.dumps(mapping, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False),
                    now,
                ),
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO order_import_rows(row_id,batch_id,row_number,raw_json,normalized_json,classification,issues_json,changes_json,existing_order_id) VALUES(?,?,?,?,?,?,?,?,?)",
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
                    ),
                )
            conn.commit()
            order_columns = _map_order_columns(conn)
        schema_notes = []
        if "order_id" not in order_columns:
            schema_notes.append("orders表未识别到order_id，新增订单会使用现有主键规则尝试写入")
        with _connect() as note_conn:
            has_tasks = _table_exists(note_conn, "tasks")
        if not has_tasks:
            schema_notes.append("当前数据库没有tasks表，导入后不会自动创建待确认任务")
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
        }

    @app.post("/api/import/commit")
    def commit_import(payload: CommitRequest):
        _check_import_key(payload.import_key)
        with _connect() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (payload.batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在或已过期")
            if batch["status"] == "COMMITTED":
                previous = json.loads(batch["summary_json"])
                return {"status": "DUPLICATE_SKIPPED", "batch_id": payload.batch_id, **previous}
            rows = conn.execute("SELECT * FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (payload.batch_id,)).fetchall()
            column_map = _map_order_columns(conn)
            order_no_column = column_map.get("order_no")
            order_id_column = column_map.get("order_id")
            inserted = updated = skipped = failed = task_created = 0
            failures: list[dict[str, Any]] = []
            try:
                conn.execute("BEGIN IMMEDIATE")
                for row in rows:
                    row_number = str(row["row_number"])
                    action = payload.row_actions.get(row_number, "import")
                    classification = row["classification"]
                    if action == "skip" or classification in ("ERROR", "DUPLICATE"):
                        skipped += 1
                        conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("SKIPPED", classification, row["row_id"]))
                        continue
                    normalized = json.loads(row["normalized_json"])
                    changes = json.loads(row["changes_json"])
                    try:
                        if classification == "NEW":
                            order_id = _insert_order(conn, normalized, column_map, payload.batch_id)
                            inserted += 1
                            created_task = _insert_import_task(conn, order_id, normalized.get("order_no") or "", False)
                            task_created += int(created_task)
                            _append_event(conn, order_id, "ORDER_IMPORTED", {"batch_id": payload.batch_id, "source": "excel_csv", "normalized": normalized})
                        elif classification == "UPDATE":
                            order_id = row["existing_order_id"]
                            if not order_id and order_no_column and order_id_column:
                                current = conn.execute(f'SELECT "{order_id_column}" FROM orders WHERE "{order_no_column}"=?', (normalized.get("order_no"),)).fetchone()
                                order_id = current[0] if current else None
                            if not order_id:
                                raise ValueError("找不到已有订单的系统ID")
                            _update_order(conn, order_id, changes, column_map)
                            updated += 1
                            created_task = _insert_import_task(conn, order_id, normalized.get("order_no") or "", True)
                            task_created += int(created_task)
                            _append_event(conn, order_id, "ORDER_IMPORT_UPDATED", {"batch_id": payload.batch_id, "changes": changes})
                        else:
                            skipped += 1
                            continue
                        conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("COMMITTED", "", row["row_id"]))
                    except Exception as exc:
                        failed += 1
                        failures.append({"row_number": row["row_number"], "message": str(exc)})
                        conn.execute("UPDATE order_import_rows SET commit_status=?,commit_message=? WHERE row_id=?", ("FAILED", str(exc), row["row_id"]))
                result_summary = {
                    "inserted": inserted,
                    "updated": updated,
                    "skipped": skipped,
                    "failed": failed,
                    "tasks_created": task_created,
                    "failures": failures,
                    "ranking_refresh_required": True,
                    "ranking_refresh_note": "导入后请刷新今日行动；若已配置FT04，现有任务变化会进入下一次重排。",
                }
                conn.execute(
                    "UPDATE order_import_batches SET status='COMMITTED',summary_json=?,committed_at=? WHERE batch_id=?",
                    (json.dumps(result_summary, ensure_ascii=False), _now_iso(), payload.batch_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"status": "COMMITTED", "batch_id": payload.batch_id, **result_summary}

    @app.get("/api/import/batches/{batch_id}")
    def get_batch(batch_id: str):
        with _connect() as conn:
            _ensure_patch_schema(conn)
            batch = conn.execute("SELECT * FROM order_import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="导入批次不存在")
            rows = conn.execute("SELECT row_number,classification,commit_status,commit_message FROM order_import_rows WHERE batch_id=? ORDER BY row_number", (batch_id,)).fetchall()
        return {"batch": dict(batch), "rows": [dict(row) for row in rows]}
