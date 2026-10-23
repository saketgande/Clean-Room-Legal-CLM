"""FILE-01: a redline carries one native Word revision per change, each accepted or
rejected on its own, instead of deleting and re-inserting the whole contract."""

import inspect
import io
import re
import zipfile

from app.ai.tool_runtime import _build_redline_docx
from app.playbooks import service as playbooks

SOURCE = "1. Term. One year.\n\n2. Liability. Unlimited.\n\n3. Law. Delaware."


def _document_xml(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read("word/document.xml").decode()


def test_each_change_is_its_own_tracked_revision():
    start = SOURCE.index("Unlimited.")
    content = _build_redline_docx(
        title="MSA - Playbook Redline Proposal", base_version_number=3, source_text=SOURCE,
        anchored=[
            {"start": start, "end": start + len("Unlimited."), "applied": True,
             "original_text": "Unlimited.", "replacement_text": "Capped at fees paid."},
            {"start": len(SOURCE), "end": len(SOURCE), "applied": True,
             "original_text": "", "replacement_text": "\n\n4. Notices. In writing."},
        ],
        author="Legal AI Playbook", notes="Liability must be capped.",
    )
    xml = _document_xml(content)
    assert xml.count("<w:del ") == 1
    assert xml.count("<w:ins ") == 2
    revision_ids = re.findall(r'<w:(?:ins|del) [^>]*w:id="(\d+)"', xml)
    assert len(revision_ids) == len(set(revision_ids)) == 3
    assert "1. Term. One year." in xml  # untouched text stays plain, not deleted
    assert 'w:author="Legal AI Playbook"' in xml


def test_playbook_redlines_no_longer_rebuild_the_whole_document():
    source = inspect.getsource(playbooks)
    assert "_build_playbook_redline_docx" not in source
    assert "_build_redline_docx(" in source
