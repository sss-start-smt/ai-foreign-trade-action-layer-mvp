from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import d17_release_ops as d17


def _db(path: Path, value: str = "v1") -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO demo(value) VALUES(?)", (value,))
    conn.commit(); conn.close()


def test_manifest_verifies_and_detects_tamper(tmp_path):
    root = tmp_path / "release"; root.mkdir()
    (root / "a.txt").write_text("A", encoding="utf-8")
    (root / "b.txt").write_text("B", encoding="utf-8")
    manifest = d17.build_manifest(root)
    assert d17.verify_manifest(root, manifest)["ok"] is True
    (root / "a.txt").write_text("tampered", encoding="utf-8")
    check = d17.verify_manifest(root, manifest)
    assert check["ok"] is False
    assert any(x["kind"] == "HASH_MISMATCH" for x in check["mismatches"])


def test_manifest_excludes_cache_and_test_databases(tmp_path):
    root = tmp_path / "release"; (root / "__pycache__").mkdir(parents=True); (root / "tests").mkdir()
    (root / "keep.py").write_text("print('ok')", encoding="utf-8")
    (root / "__pycache__" / "x.pyc").write_bytes(b"junk")
    (root / "tests" / "test.db").write_bytes(b"junk")
    manifest = d17.build_manifest(root)
    paths = {x["path"] for x in manifest["files"]}
    assert paths == {"keep.py"}


def test_sqlite_backup_restore_is_real_and_preserves_business_rows(tmp_path):
    source = tmp_path / "source.db"; _db(source, "order-state-v1")
    backup = d17.backup_sqlite(source, tmp_path / "backups", label="d17")
    # Damage the source after backup to prove restore is from the backup, not a file-exists check.
    conn = sqlite3.connect(source); conn.execute("UPDATE demo SET value='damaged'"); conn.commit(); conn.close()
    restored = tmp_path / "restored.db"
    result = d17.restore_sqlite(backup["backup"], restored)
    conn = sqlite3.connect(restored); value = conn.execute("SELECT value FROM demo").fetchone()[0]; conn.close()
    assert result["integrity"] == "ok"
    assert value == "order-state-v1"


def test_restore_requires_explicit_overwrite(tmp_path):
    source = tmp_path / "source.db"; _db(source)
    backup = d17.backup_sqlite(source, tmp_path / "backups")
    target = tmp_path / "target.db"; _db(target, "existing")
    with pytest.raises(d17.D17UnsafeOperation):
        d17.restore_sqlite(backup["backup"], target)
    assert d17.restore_sqlite(backup["backup"], target, allow_overwrite=True)["integrity"] == "ok"


def test_release_bundle_can_be_used_for_verified_code_rollback(tmp_path):
    root = tmp_path / "release-v1"; root.mkdir()
    (root / "app.py").write_text("VERSION='v1'", encoding="utf-8")
    (root / "config.json").write_text('{"policy":"safe"}', encoding="utf-8")
    bundle = tmp_path / "release-v1.zip"
    info = d17.create_release_bundle(root, bundle)
    assert info["file_count"] == 2
    rollback = tmp_path / "rollback"
    restored = d17.extract_release_bundle(bundle, rollback)
    assert restored["verification"]["ok"] is True
    assert (rollback / "app.py").read_text(encoding="utf-8") == "VERSION='v1'"


def test_bundle_extraction_rejects_nonempty_target(tmp_path):
    root = tmp_path / "release"; root.mkdir(); (root / "a.txt").write_text("A")
    bundle = tmp_path / "r.zip"; d17.create_release_bundle(root, bundle)
    target = tmp_path / "target"; target.mkdir(); (target / "keep.txt").write_text("do not overwrite")
    with pytest.raises(d17.D17UnsafeOperation):
        d17.extract_release_bundle(bundle, target)


def test_release_contract_does_not_claim_production_rollback():
    contract = d17.release_contract()
    assert "Local SQLite" in contract["production_boundary"]["executed_in_d17"]
    assert "No real Render/Railway/PostgreSQL" in contract["production_boundary"]["not_claimed"]
