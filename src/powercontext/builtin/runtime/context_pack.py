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

"""Wire format for full context packs and prefix-reusing deltas.

A :class:`ContextPack` splits one prepared context into deterministic UTF-8 chunks and carries a
SHA-256 digest over its schema and chunks. A :class:`ContextPackDelta` keeps a valid common prefix
of an earlier pack and only appends the chunks that changed, so a receiver that already holds the
base pack can rebuild the target pack from serialized bytes alone. The wire contract is fixed:
canonical JSON (sorted keys, compact separators), Base64 chunk arrays, and a ``byte_count`` that
equals the length of the serialized bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512


class ContextPackError(Exception):
    """A context pack or delta failed protocol, chunking, or digest validation."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context encoded as bounded, digest-checked byte chunks."""

    items: tuple[bytes, ...]
    schema: ClassVar[str] = PACK_SCHEMA

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Construct a pack from an explicit chunk tuple."""

        return cls(items=tuple(bytes(item) for item in items))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split one text deterministically into chunks of at most ``chunk_bytes`` bytes."""

        return cls(items=_chunk_text(content, chunk_bytes))

    @property
    def digest(self) -> str:
        """SHA-256 over the canonical JSON of this pack's schema and chunks."""

        return _digest(_unsigned_payload(self.schema, self.items))

    @property
    def byte_count(self) -> int:
        """Length in bytes of the serialized pack, including this field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this pack to canonical wire bytes."""

        return _serialize_pack(self.schema, self.items)

    @classmethod
    def from_bytes(cls, data: bytes | str, /) -> ContextPack:
        """Parse and validate a serialized pack, returning the rebuilt pack."""

        payload = _load_object(data)
        if set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack payload must contain exactly schema, items, digest, byte_count")  # noqa: TRY003
        schema = payload["schema"]
        if schema != PACK_SCHEMA:
            raise ContextPackError("unexpected pack schema")  # noqa: TRY003
        items = _decode_items(payload["items"], field="items")
        expected_digest = _digest(_unsigned_payload(PACK_SCHEMA, items))
        if payload["digest"] != expected_digest:
            raise ContextPackError("pack digest does not match its contents")  # noqa: TRY003
        pack = cls(items=items)
        if payload["byte_count"] != len(_as_bytes(data)):
            raise ContextPackError("pack byte_count does not match its serialized length")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A reused base prefix plus the appended chunks that complete a target pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    schema: ClassVar[str] = DELTA_SCHEMA

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Choose a valid common prefix of ``base`` and carry the target suffix."""

        prefix = _common_prefix(base.items, target.items)
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=tuple(target.items[prefix:]),
        )

    @property
    def byte_count(self) -> int:
        """Length in bytes of the serialized delta; not written as a wire field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta to canonical wire bytes."""

        payload = {
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        }
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes | str, /) -> ContextPackDelta:
        """Parse and validate a serialized delta."""

        payload = _load_object(data)
        if set(payload) != {"schema", "base_digest", "target_digest", "prefix_items", "appended"}:
            raise ContextPackError("delta payload must contain exactly its five wire fields")  # noqa: TRY003
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unexpected delta schema")  # noqa: TRY003
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("delta digests must be strings")  # noqa: TRY003
        prefix = _validate_prefix(payload["prefix_items"])
        appended = _decode_items(payload["appended"], field="appended")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Validate a delta against ``base`` and rebuild the target pack."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("apply_context_pack_delta requires a pack and a delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the supplied base pack")  # noqa: TRY003
    prefix = _validate_prefix(delta.prefix_items)
    if prefix > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack chunk count")  # noqa: TRY003
    rebuilt = ContextPack(items=(*base.items[:prefix], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack digest does not match the delta target digest")  # noqa: TRY003
    return rebuilt


def _chunk_text(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
    if not content:
        return ()
    chunks: list[bytes] = []
    current = bytearray()
    for char in content:
        encoded = char.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("a character does not fit within chunk_bytes")  # noqa: TRY003
        if len(current) + len(encoded) > chunk_bytes:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(encoded)
    if current:
        chunks.append(bytes(current))
    return tuple(chunks)


def _common_prefix(base: tuple[bytes, ...], target: tuple[bytes, ...]) -> int:
    length = 0
    for left, right in zip(base, target, strict=False):
        if left != right:
            break
        length += 1
    return length


def _unsigned_payload(schema: str, items: tuple[bytes, ...]) -> dict[str, Any]:
    return {
        "schema": schema,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }


def _digest(unsigned: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _serialize_pack(schema: str, items: tuple[bytes, ...]) -> bytes:
    unsigned = _unsigned_payload(schema, items)
    digest = _digest(unsigned)
    byte_count = 0
    for _ in range(8):
        payload = {**unsigned, "digest": digest, "byte_count": byte_count}
        rendered = _canonical(payload)
        if len(rendered) == byte_count:
            break
        byte_count = len(rendered)
    return _canonical(payload)


def _load_object(data: bytes | str) -> dict[str, Any]:
    try:
        payload = json.loads(_as_bytes(data))
    except (ValueError, UnicodeDecodeError) as error:
        raise ContextPackError("payload is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("payload must be a JSON object")  # noqa: TRY003
    return payload


def _decode_items(raw: Any, *, field: str) -> tuple[bytes, ...]:
    if not isinstance(raw, list):
        raise ContextPackError(f"{field} must be a list of Base64 strings")  # noqa: TRY003
    items: list[bytes] = []
    for value in raw:
        if not isinstance(value, str):
            raise ContextPackError(f"{field} entries must be Base64 strings")  # noqa: TRY003
        try:
            items.append(base64.b64decode(value, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError(f"{field} contains invalid Base64") from error
    return tuple(items)


def _validate_prefix(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
    return value


def _as_bytes(data: bytes | str) -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
