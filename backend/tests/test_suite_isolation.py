"""The test process must never reach the dev worker's queue (see conftest)."""


def test_celery_in_tests_uses_an_in_memory_broker():
    from app.jobs.celery_app import celery_app

    assert celery_app.conf.broker_url == "memory://"
    with celery_app.connection_for_write() as conn:
        assert conn.transport_cls == "memory"


def test_dispatching_a_job_sends_nothing_to_redis():
    from app.jobs.tasks import run_ai_job

    result = run_ai_job.delay("job-that-does-not-exist")  # lands in the in-memory queue only
    assert result.id
