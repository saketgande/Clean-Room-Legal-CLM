"""Optional retrieval-quality features (reranker, embedding provider) were
silent when off — no error, no UI indicator, nothing short of reading the
source told an operator whether they were active. Pin that create_app logs
their status at boot."""

import inspect

from app.main import create_app


def test_create_app_logs_retrieval_quality_feature_status():
    source = inspect.getsource(create_app)

    assert "rerank_enabled" in source
    assert "embedding_provider" in source
    assert "rerank_provider" in source
