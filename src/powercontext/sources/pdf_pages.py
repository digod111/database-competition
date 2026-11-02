# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Page-level PDF Sources with stable identity and standard text evidence."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

# Ask the lexical index for a deeper ranked pool than the number of pages we return so
# same-page duplicates never starve the delivered citation list.
_SEARCH_POOL_MULTIPLIER = 4


class PDFDocumentInput(BaseModel):
    """One offline PDF document to parse into page-level Sources."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical PDF page with its document identity and extracted text."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured single physical page addressed by document, revision and page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable, delimiter-safe identity for one PDF page."""

    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        identity = f"pdf:{hashlib.sha256(f'{document_id}\x00{document_sha256}\x00{page_number}'.encode()).hexdigest()}"
    return identity


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse one complete PDF into immutable page inputs, failing atomically."""

    document_id = value.document_id
    if not document_id or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003

    content = bytes(value.content)
    document_sha256 = hashlib.sha256(content).hexdigest()

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("pdf requires a password to be read")  # noqa: TRY003
        page_text = [page.extract_text() or "" for page in reader.pages]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("pdf content must be a complete, readable PDF") from error

    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(page_text, start=1)
    )


class PDFPageSourceAdapter:
    """Resolve and read the runtime's built-in page-level PDF Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one physical page as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct physical-page citations for one question.

    Only the public scoped-Memory API is used, so every returned citation is a real,
    independently retrievable entry. Lexical ranking is delegated to the index; this
    function's job is to walk that ranking and keep the first distinct pages.
    """

    if limit < 1:
        return ()

    page = await memory.search(SearchMemoryRequest(query=question, mode="fts", limit=limit * _SEARCH_POOL_MULTIPLIER))
    citations: list[MemoryCitation] = []
    seen: set[str] = set()
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        locations = {reference.source_id for reference in record.entry.sources if reference.source_type == PDF_PAGE_SOURCE_NAME}
        if len(locations) != 1:
            continue
        location = next(iter(locations))
        if location in seen:
            continue
        seen.add(location)
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
