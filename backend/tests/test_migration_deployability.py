import subprocess
from pathlib import Path


def test_initial_migration_is_static_and_reviewable():
    migration = Path("alembic/versions/0001_initial_contract_platform.py").read_text()

    assert "Base.metadata.create_all" not in migration
    assert "Base.metadata.drop_all" not in migration
    assert "import app.models" not in migration
    assert "CREATE TABLE audit_log" in migration
    assert "CREATE TABLE contract" in migration
    assert "CREATE TABLE project" in migration
    assert "CREATE TABLE contract_version" in migration
    assert "row_hash VARCHAR(64) NOT NULL" in migration


def test_deployable_schema_migration_covers_phase1_auth_tables_and_project_share():
    migration = Path("alembic/versions/0005_schema_project_share.py").read_text()

    for table_name in [
        "refresh_token",
        "api_key",
        "user_invitation",
        "org_join_request",
        "user_approval_decision",
        "project_share",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in migration


def test_deployable_schema_migration_adds_pgvector_index():
    migration = Path("alembic/versions/0005_schema_project_share.py").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "USING hnsw" in migration
    assert "vector_cosine_ops" in migration


def test_audit_hash_chain_migration_backfills_and_recreates_triggers():
    migration = Path("alembic/versions/0006_audit_hash_chain.py").read_text()

    assert "ADD COLUMN IF NOT EXISTS prev_hash" in migration
    assert "ADD COLUMN IF NOT EXISTS row_hash" in migration
    assert "ALTER COLUMN row_hash SET NOT NULL" in migration
    assert "requires online Alembic migration for existing rows" in migration
    assert "prevent_audit_log_mutation" in migration


def test_alembic_offline_sql_generation_succeeds():
    result = subprocess.run(
        ["alembic", "upgrade", "head", "--sql"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "CREATE TABLE audit_log" in result.stdout
    assert "0006_audit_hash_chain" in result.stdout
    assert "create_all" not in result.stdout
