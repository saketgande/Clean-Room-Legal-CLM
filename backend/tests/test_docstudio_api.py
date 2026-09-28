"""The document view's API: what it serves, and what it refuses.

The file route is the one with teeth. It hands back bytes somebody uploaded, so
it must never be renderable by the browser in this origin — an HTML or SVG file
wearing an allowed media type would otherwise run here — and it must never
serve another organisation's document.
"""

import uuid
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.models
from app.core.database import SessionLocal
from app.core.deps import get_current_user
from app.docstudio.models import DsAnnotation, DsClause, DsDocument, DsEvent, DsVersion
from app.main import app

URL = "/api/v1/docstudio"
ORG = "docstudio-api-test-org"
OTHER_ORG = "docstudio-api-other-org"


class _User:
    """Only what the routes and the permission check read."""

    id = "docstudio-api-test-user"
    org_id = ORG
    permission_values = ("contract_file:read", "contract_file:create")


def _docx(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        session = SessionLocal()
        for model in (DsAnnotation, DsEvent, DsClause, DsVersion, DsDocument):
            for org in (ORG, OTHER_ORG):
                session.execute(delete(model).where(model.org_id == org))
        session.commit()
        session.close()


@pytest.fixture
def uploaded(client):
    content = _docx(
        f"1. TERM. The term is three years. Reference {uuid.uuid4()}.",
        "2. FEES. Payment is due within thirty days of invoice.",
    )
    response = client.post(f"{URL}/documents", files={"file": ("msa.docx", content)})
    assert response.status_code == 201, response.text
    return response.json(), content


def test_an_upload_is_read_into_clauses_and_its_file_is_kept(client, uploaded):
    version, _ = uploaded

    assert version["version_number"] == 1
    assert version["has_file"] is True and version["has_text"] is True
    assert version["read_by"].startswith("docx v")


def test_a_version_carries_its_clauses_and_geometry(client, uploaded):
    version, _ = uploaded

    detail = client.get(f"{URL}/versions/{version['id']}").json()

    assert [clause["number_label"] for clause in detail["clauses"]] == ["1.", "2."]
    assert detail["clauses"][0]["structure_source"]  # who placed it, for the panel
    assert detail["flat_text"].startswith("1. TERM.")


def test_the_file_comes_back_as_bytes_the_browser_will_not_render(client, uploaded):
    """The whole point of the route: the page draws these bytes itself."""
    version, content = uploaded

    response = client.get(f"{URL}/versions/{version['id']}/file")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_another_organisations_document_is_not_found(client, uploaded):
    """Not 403: whether a document exists is itself something to keep."""
    version, _ = uploaded
    session = SessionLocal()
    document = session.get(DsDocument, version["document_id"])
    document.org_id = OTHER_ORG
    session.commit()
    session.close()

    assert client.get(f"{URL}/versions/{version['id']}").status_code == 404
    assert client.get(f"{URL}/versions/{version['id']}/file").status_code == 404
    assert client.get(f"{URL}/documents").json() == []


def test_a_second_upload_of_a_document_becomes_its_next_version(client, uploaded):
    version, _ = uploaded

    second = client.post(
        f"{URL}/documents",
        files={"file": ("msa-v2.docx", _docx("1. TERM. The term is five years."))},
        data={"document_id": version["document_id"]},
    ).json()

    assert (second["document_id"], second["version_number"]) == (version["document_id"], 2)
    listed = client.get(f"{URL}/documents").json()
    assert [(d["version_count"], d["current"]["version_number"]) for d in listed] == [(2, 2)]


def test_a_file_type_no_parser_reads_is_refused_before_anything_is_stored(client):
    response = client.post(f"{URL}/documents", files={"file": ("notes.pages", b"whatever")})

    assert response.status_code == 422
    assert "use .pdf, .docx or .txt" in response.json()["detail"]
    assert client.get(f"{URL}/documents").json() == []


def test_an_empty_file_is_refused(client):
    assert client.post(f"{URL}/documents", files={"file": ("empty.txt", b"")}).status_code == 422


def test_a_version_stored_before_files_were_kept_says_so(client, uploaded):
    version, _ = uploaded
    session = SessionLocal()
    row = session.get(DsVersion, version["id"])
    row.storage_key = None
    session.commit()
    session.close()

    response = client.get(f"{URL}/versions/{version['id']}/file")

    assert response.status_code == 404
    assert "was not kept" in response.json()["detail"]
