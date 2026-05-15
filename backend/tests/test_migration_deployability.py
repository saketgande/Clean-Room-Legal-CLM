from pathlib import Path


def test_deployable_schema_migration_covers_phase1_auth_tables_and_project_share():
    migration = Path("alembic/versions/0005_deployable_schema_and_project_shares.py").read_text()

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
    migration = Path("alembic/versions/0005_deployable_schema_and_project_shares.py").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "USING hnsw" in migration
    assert "vector_cosine_ops" in migration
