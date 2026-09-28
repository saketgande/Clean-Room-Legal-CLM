"""The only thing docstudio needs from the rest of AEGIS.

Docstudio takes a file. It does not care where the file came from, and nothing
else about the caller's domain crosses this line — no Contract, no
StorageObject, no ContractVersion. That is what keeps this subsystem
independently testable and independently replaceable.

If you find yourself importing from `app.contract_files`, `app.contracts` or
`app.contract_brain` anywhere in docstudio, the thing you want belongs here
instead.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SourceFile:
    content: bytes
    filename: str
    mime_type: str


class FileSource(Protocol):
    def read(self, external_ref: str) -> SourceFile: ...


class InMemoryFileSource:
    """For tests, and for callers that already hold the bytes."""

    def __init__(self, files: dict[str, SourceFile] | None = None):
        self._files: dict[str, SourceFile] = dict(files or {})

    def add(self, external_ref: str, file: SourceFile) -> None:
        self._files[external_ref] = file

    def read(self, external_ref: str) -> SourceFile:
        try:
            return self._files[external_ref]
        except KeyError:
            raise FileNotFoundError(f"No file for external_ref {external_ref!r}") from None
