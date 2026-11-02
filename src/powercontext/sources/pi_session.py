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

"""Pi session branch history captured as per-event textual evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"


class PiSessionInput(BaseModel):
    """One Pi session archive together with the leaf that selects a branch."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi event on the selected branch with its original record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured single Pi event addressed by its session and event identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity for one Pi session event."""

    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
        identity = f"pi:sha256:{digest}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pi event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _plain_text(content: JsonValue) -> str:
    """Return the visible string or joined text blocks of a message body."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _compact_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assistant_body(content: JsonValue) -> str:
    """Render assistant/user content blocks, keeping text and tool calls only."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name") if isinstance(block.get("name"), str) else "unknown"
            call_id = block.get("id") if isinstance(block.get("id"), str) else "unknown"
            arguments = block.get("arguments")
            if not isinstance(arguments, (dict, list)):
                arguments = {}
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(arguments)}")
    return "\n".join(parts)


def _event_text(record: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(role, body)`` for a visible record, or ``None`` for control nodes."""

    record_type = record.get("type")
    if record_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        if role == "bashExecution":
            command = message.get("command")
            output = message.get("output")
            body = f"$ {command}\n{output}"
            return role, body
        if role == "toolResult":
            tool_name = message.get("toolName")
            tool_name = tool_name if isinstance(tool_name, str) and tool_name else "unknown"
            call_id = message.get("toolCallId")
            call_id = call_id if isinstance(call_id, str) and call_id else "unknown"
            return role, f"[tool_result {tool_name} {call_id}] {_plain_text(message.get('content'))}"
        body = _assistant_body(message.get("content"))
        if not body:
            return None
        return role, body
    if record_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        return record_type, summary
    if record_type == "custom_message":
        body = _assistant_body(record.get("content"))
        if not body:
            return None
        return "custom", body
    return None


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse one Pi v3 archive into the visible events on the selected branch."""

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("pi session content must be valid UTF-8") from error

    raw_records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("pi session contains malformed JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("pi session records must be JSON objects")  # noqa: TRY003
        raw_records.append(parsed)

    if not raw_records:
        raise ValueError("pi session archive must contain a header record")  # noqa: TRY003

    header = raw_records[0]
    session_id = header.get("id")
    if (
        header.get("type") != "session"
        or header.get("version") != 3
        or not isinstance(session_id, str)
        or not session_id
    ):
        raise ValueError("pi session header must declare type=session, version=3 and a non-empty id")  # noqa: TRY003

    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in raw_records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("pi session records require a non-empty id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError("pi session record ids must be unique")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("pi session parentId must reference an already seen node")  # noqa: TRY003
        nodes[event_id] = record
        order.append(event_id)

    if not order:
        raise ValueError("pi session archive must contain at least one record")  # noqa: TRY003

    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in nodes:
        raise ValueError("pi session leaf must reference an existing node")  # noqa: TRY003

    chain: list[str] = []
    cursor: str | None = leaf_id
    while cursor is not None:
        chain.append(cursor)
        cursor = nodes[cursor].get("parentId")
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        record = nodes[event_id]
        rendered = _event_text(record)
        if rendered is None:
            continue
        role, body = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=record.get("parentId"),
                event_type=record.get("type"),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read the runtime's built-in Pi session event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class PiSessionConnector:
    """Import the visible events of one selected Pi branch exactly once."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))

        checkpoint = session.checkpoint
        imported: dict[str, str] = {}
        if isinstance(checkpoint, dict):
            stored = checkpoint.get("events")
            if isinstance(stored, dict):
                imported = {key: value for key, value in stored.items() if isinstance(value, str)}

        digests: dict[str, str] = {}
        for event in events:
            digest = _record_digest(event.record)
            prior = imported.get(event.event_id)
            if prior is not None and prior != digest:
                raise ValueError("an already imported pi session event was rewritten")  # noqa: TRY003
            digests[event.event_id] = digest

        added: dict[str, str] = {}
        for event in events:
            if event.event_id in imported:
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            added[event.event_id] = digests[event.event_id]

        if not added:
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)
        merged = {**imported, **added}
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": 1, "events": merged},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
