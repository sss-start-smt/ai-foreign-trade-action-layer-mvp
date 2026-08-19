from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

CN_TZ = timezone(timedelta(hours=8))
D17_RELEASE_POLICY_VERSION = "D17_RELEASE_RECOVERY_V1"
DEFAULT_EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git"}
DEFAULT_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class D17Error(RuntimeError):
    pass


class D17IntegrityError(D17Error):
    pass


class D17UnsafeOperation(D17Error):
    pass


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _include(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in DEFAULT_EXCLUDED_PARTS for part in rel.parts):
        return False
    if path.suffix.lower() in DEFAULT_EXCLUDED_SUFFIXES:
        return False
    if path.name.endswith(".db") and "tests" in rel.parts:
        return False
    return True


def build_manifest(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise D17Error(f"release root not found: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if not _include(path, root):
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    digest = hashlib.sha256(
        "\n".join(f"{x['sha256']}  {x['path']}" for x in files).encode("utf-8")
    ).hexdigest()
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "generated_at": _now(),
        "root": root.name,
        "file_count": len(files),
        "release_digest": digest,
        "files": files,
    }


def verify_manifest(root: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = Path(root).resolve()
    mismatches: list[dict[str, Any]] = []
    expected = {x["path"]: x for x in manifest.get("files", [])}
    current = build_manifest(root)
    actual = {x["path"]: x for x in current["files"]}
    for rel, item in expected.items():
        if rel not in actual:
            mismatches.append({"path": rel, "kind": "MISSING"})
        elif actual[rel]["sha256"] != item["sha256"]:
            mismatches.append({"path": rel, "kind": "HASH_MISMATCH"})
    for rel in sorted(set(actual) - set(expected)):
        mismatches.append({"path": rel, "kind": "UNEXPECTED"})
    return {
        "ok": not mismatches and current["release_digest"] == manifest.get("release_digest"),
        "expected_file_count": len(expected),
        "actual_file_count": len(actual),
        "expected_release_digest": manifest.get("release_digest"),
        "actual_release_digest": current["release_digest"],
        "mismatches": mismatches,
    }


def _sqlite_integrity(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise D17Error(f"database not found: {path}")
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "unknown")
    finally:
        conn.close()


def backup_sqlite(source_db: str | Path, backup_dir: str | Path, *, label: str = "manual") -> dict[str, Any]:
    source = Path(source_db).resolve()
    backup_dir = Path(backup_dir).resolve()
    if _sqlite_integrity(source).lower() != "ok":
        raise D17IntegrityError("source database failed PRAGMA integrity_check")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%dT%H%M%S")
    target = backup_dir / f"floworder_{label}_{stamp}.sqlite3"
    src_conn = sqlite3.connect(source)
    dst_conn = sqlite3.connect(target)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()
    if _sqlite_integrity(target).lower() != "ok":
        target.unlink(missing_ok=True)
        raise D17IntegrityError("backup database failed PRAGMA integrity_check")
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "source": str(source),
        "backup": str(target),
        "sha256": sha256_file(target),
        "integrity": "ok",
        "created_at": _now(),
    }


def restore_sqlite(backup_db: str | Path, target_db: str | Path, *, allow_overwrite: bool = False) -> dict[str, Any]:
    backup = Path(backup_db).resolve()
    target = Path(target_db).resolve()
    if _sqlite_integrity(backup).lower() != "ok":
        raise D17IntegrityError("backup database failed integrity check")
    if target.exists() and not allow_overwrite:
        raise D17UnsafeOperation("target database exists; explicit allow_overwrite is required")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        dst.commit()
    if _sqlite_integrity(target).lower() != "ok":
        raise D17IntegrityError("restored database failed integrity check")
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "backup": str(backup),
        "restored_to": str(target),
        "sha256": sha256_file(target),
        "integrity": "ok",
        "restored_at": _now(),
    }


def create_release_bundle(source_root: str | Path, output_zip: str | Path) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    output_zip = Path(output_zip).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(source_root)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in manifest["files"]:
            zf.write(source_root / item["path"], arcname=item["path"])
        zf.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "bundle": str(output_zip),
        "sha256": sha256_file(output_zip),
        "file_count": manifest["file_count"],
        "release_digest": manifest["release_digest"],
    }


def extract_release_bundle(bundle: str | Path, target_dir: str | Path, *, allow_nonempty: bool = False) -> dict[str, Any]:
    bundle = Path(bundle).resolve()
    target = Path(target_dir).resolve()
    if target.exists() and any(target.iterdir()) and not allow_nonempty:
        raise D17UnsafeOperation("rollback target is non-empty; explicit allow_nonempty is required")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle, "r") as zf:
        names = zf.namelist()
        for name in names:
            resolved = (target / name).resolve()
            if target not in resolved.parents and resolved != target:
                raise D17UnsafeOperation(f"unsafe zip member: {name}")
        zf.extractall(target)
    manifest_path = target / "RELEASE_MANIFEST.json"
    if not manifest_path.exists():
        raise D17IntegrityError("release manifest missing from bundle")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Manifest itself was added to the zip after the digest was built; remove it from verification root.
    manifest_path.unlink()
    verification = verify_manifest(target, manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if not verification["ok"]:
        raise D17IntegrityError(f"rollback bundle verification failed: {verification['mismatches'][:3]}")
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "bundle": str(bundle),
        "target": str(target),
        "verification": verification,
        "restored_at": _now(),
    }


def release_contract() -> dict[str, Any]:
    return {
        "policy_version": D17_RELEASE_POLICY_VERSION,
        "release_rules": [
            "Backup before schema/data-affecting release operations.",
            "A backup is not evidence until a restore drill passes integrity and business-row checks.",
            "Release artifacts are content-addressed with SHA256 and verified before rollback use.",
            "Rollback must restore code and database compatibility together; never roll code behind an irreversible schema change.",
            "Production platform credentials and destructive deployment actions are outside this local rehearsal unless explicitly connected and authorized.",
        ],
        "production_boundary": {
            "executed_in_d17": "Local SQLite backup/restore and code bundle rollback rehearsal.",
            "not_claimed": "No real Render/Railway/PostgreSQL production rollback was executed from this environment.",
        },
    }
