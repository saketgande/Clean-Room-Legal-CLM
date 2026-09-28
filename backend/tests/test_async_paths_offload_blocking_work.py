"""PERF-01: async request paths hand synchronous, slow work (AI triage, document
parsing, malware scans, storage writes) to a thread instead of running it on the
event loop every other request shares."""

import inspect

from app.ai.tool_runtime import ToolRuntime
from app.contract_files import service as contract_files
from app.intake import routes as intake_routes
from app.notices import routes as notices_routes


def test_ai_calling_sync_tools_run_in_a_thread():
    source = inspect.getsource(ToolRuntime._execute_validated)
    for handler in ("_create_intake_request", "_create_notice", "_draft_notice_response"):
        assert f"run_in_threadpool(self.{handler}" in source


def test_async_routes_offload_their_sync_services():
    assert "run_in_threadpool(ingest_mod.handle_teams_activity" in inspect.getsource(intake_routes.teams_webhook)
    assert "run_in_threadpool(\n        service.extract_from_upload" in inspect.getsource(notices_routes.extract_from_document)
    assert "run_in_threadpool(\n        service.add_document" in inspect.getsource(notices_routes.add_document)


def test_upload_paths_offload_scanning_and_storage():
    assert "asyncio.to_thread(_scan_for_malware" in inspect.getsource(contract_files.ingest_upload)
    for upload in (contract_files.create_contract_from_upload, contract_files.add_version_from_upload):
        source = inspect.getsource(upload)
        assert "await ingest_upload(" in source
        assert "asyncio.to_thread(\n        storage_service.save_bytes" in source
