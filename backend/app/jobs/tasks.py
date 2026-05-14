from app.jobs.celery_app import celery_app


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def run_placeholder_job(self, job_id: str) -> dict:
    return {"job_id": job_id, "status": "placeholder"}
