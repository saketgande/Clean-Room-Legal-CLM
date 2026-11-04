"""Attachment pre-flight. Failure mode guarded: an oversized, malformed or by-reference
attachment reaching the platform (or being fetched by the tool server) instead of being refused
up front with a message that names the limit and the overage."""

from __future__ import annotations

import base64

import pytest

from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.limits import MIB, preflight_attachments
from aegis_intake_mcp.schemas import AttachmentInput


def att(content: str, name: str = "f.pdf") -> AttachmentInput:
    return AttachmentInput(filename=name, mime_type="application/pdf", content_base64=content)


def blob(size: int, name: str = "f.pdf") -> AttachmentInput:
    return att(base64.b64encode(b"x" * size).decode(), name)


def refuse(items: list[AttachmentInput]) -> ToolError:
    with pytest.raises(ToolError) as info:
        preflight_attachments(items)
    return info.value


def test_valid_attachments_pass_and_report_sizes():
    out = preflight_attachments([blob(10, "a.pdf"), blob(20, "b.pdf")])
    assert [(a.filename, a.size_bytes) for a in out] == [("a.pdf", 10), ("b.pdf", 20)]


def test_five_files_ok_six_refused_with_overage():
    assert len(preflight_attachments([blob(1)] * 5)) == 5
    err = refuse([blob(1)] * 6)
    assert err.code == "too_large"
    assert "6 attachments" in err.message and "at most 5" in err.message
    assert "1 too many" in err.message and "Nothing was sent" in err.message


def test_one_byte_over_per_file_limit():
    err = refuse([blob(25 * MIB + 1, "big.pdf")])
    assert err.code == "too_large"
    assert "'big.pdf'" in err.message and "25 MB per file" in err.message
    assert "less than 0.1 MB" in err.message  # a 1-byte overage is not "0.0 MB"


def test_exactly_25_mib_is_allowed():
    assert preflight_attachments([blob(25 * MIB)])[0].size_bytes == 25 * MIB


def test_total_over_40_mib_names_limit_and_overage():
    err = refuse([blob(21 * MIB, "a.pdf"), blob(21 * MIB, "b.pdf")])
    assert err.code == "too_large"
    assert "42.0 MB" in err.message and "40 MB per filing" in err.message
    assert "over by 2.0 MB" in err.message


def test_empty_attachment():
    err = refuse([att("", "e.pdf")])
    assert err.code == "invalid_input" and "'e.pdf' is empty" in err.message


def test_invalid_base64():
    err = refuse([att("not*base64!", "x.pdf")])
    assert err.code == "invalid_input" and "'x.pdf' is not valid base64" in err.message


@pytest.mark.parametrize(
    "value",
    ["https://example.com/a.pdf", "file:///etc/passwd", "/etc/passwd", "C:\\x", "~/a.pdf",
     "\\\\server\\share\\a.pdf", "c:/windows/a.pdf"],
)
def test_url_or_path_is_refused_not_fetched(value):
    err = refuse([att(value, "ref.pdf")])
    assert err.code == "invalid_input"
    assert "inline base64" in err.message and "nothing was fetched" in err.message


def test_base64_starting_with_slash_is_still_accepted():
    """A JPEG's base64 starts '/9j/'; treating every leading '/' as a path would refuse
    every JPEG."""
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 16).decode()
    assert jpeg.startswith("/9j/")
    assert preflight_attachments([att(jpeg, "p.jpg")])[0].size_bytes == 20


def test_line_wrapped_base64_is_normalised():
    wrapped = base64.encodebytes(b"hello world" * 10).decode()
    assert "\n" in wrapped
    out = preflight_attachments([att(wrapped)])
    assert "\n" not in out[0].content_base64 and out[0].size_bytes == 110


def test_count_is_checked_before_content():
    err = refuse([att("https://x/y")] * 6)
    assert err.code == "too_large"
