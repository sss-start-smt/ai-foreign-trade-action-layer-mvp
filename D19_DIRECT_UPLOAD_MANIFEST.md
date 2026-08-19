# FlowOrder D19 Direct Upload Manifest

This package is an **overlay for the repository source supplied on 2026-08-19**.
Upload every file and folder in this directory to the repository root, preserving paths and replacing files with the same names.

It contains the D19 frontend/auth/API changes plus the D7-D18 runtime modules and Alembic migrations required because the supplied repository is older than the D18 engineering baseline.

It intentionally excludes local databases, Python caches, large evaluation datasets, old release evidence and unrelated documentation.
