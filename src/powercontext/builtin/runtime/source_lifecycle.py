#
# Copyright (c) 2024 OceanBase.
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

"""Propagate Source availability through a per-Scope evidence dependency graph.

Callers register a directed acyclic graph of nodes (Sources, Memory entries and
Topic Memory artifacts) plus the ``all``/``any`` policy each derived node uses to
aggregate its parents. Applying a Source availability event recomputes every
node's eligibility and reconciles the native Memory entry states, while Topic
Memory eligibility gates the native Topic search. Registered content versions and
historical citations are never rewritten: retraction only flips use eligibility.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = ["SourceLifecycleError", "SourceLifecycleEvent"]

_NODE_KIND_SOURCE = "source"
_NODE_KIND_MEMORY = "memory"
_NODE_KIND_TOPIC = "topic"
_DERIVED_POLICIES = frozenset({"all", "any"})
_PURPOSE_GENERATION = "generation"
_PURPOSE_AUDIT = "audit"
_MAX_NODE_ID_LENGTH = 200
_RETRACTION_REASON = "source-lifecycle-retraction"
_RESTORATION_REASON = "source-lifecycle-restoration"

_Reference = SourceRef | MemoryCitation | ArtifactRef

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS pc_source_lifecycle_node (
        scope_id VARCHAR(255) NOT NULL,
        node_id VARCHAR(255) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        parents TEXT NOT NULL,
        policy VARCHAR(8),
        ref TEXT NOT NULL,
        revision INTEGER,
        sequence INTEGER NOT NULL,
        available INTEGER NOT NULL,
        PRIMARY KEY (scope_id, node_id)
    )
    """,
)


class SourceLifecycleError(ValueError):
    """A Source lifecycle registration, event, or resolution is invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(f"invalid source lifecycle operation: {code}")
        self.code = code


class SourceLifecycleEvent(BaseModel):
    """One Source availability transition at a given lifecycle version and sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must be a non-blank string")
        return value


@dataclass(frozen=True)
class _Node:
    node_id: str
    kind: str
    parents: tuple[str, ...]
    policy: str | None
    ref: _Reference
    revision: int | None
    sequence: int
    available: bool


@dataclass(frozen=True)
class _ScopeState:
    active: frozenset[str]
    nodes: dict[str, _Node]


class LifecycleApplication:
    """Expose the Source lifecycle service for one Runtime."""

    def __init__(self, runtime: BuiltinRuntime, /, *, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._registry = SourceLifecycleRegistry(runtime, database=database)

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._registry, self._runtime, validate_scope_id(scope_id))

    async def filter_topic_search(self, scope_id: str, result: Any, /) -> Any:
        """Drop Topic Memory hits whose dependency graph no longer supports them."""

        return await self._registry.filter_topic_search(scope_id, result)


class ScopedSourceLifecycle:
    """Register evidence nodes and apply Source availability events in one Scope."""

    def __init__(self, registry: SourceLifecycleRegistry, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._registry = registry
        self._runtime = runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        """Register a Source node bound to one lifecycle ``revision``."""

        node = _require_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-ref")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            raise SourceLifecycleError("source-revision")
        await self._register(node, kind=_NODE_KIND_SOURCE, ref=source_ref, parents=(), policy=None, revision=revision)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        """Register a Memory entry node derived from its parent nodes."""

        node = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-citation")
        await self._register(
            node,
            kind=_NODE_KIND_MEMORY,
            ref=citation,
            parents=_require_parents(node, parents, policy),
            policy=policy,
            revision=None,
        )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        """Register a Topic Memory artifact node derived from its parent nodes."""

        node = _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("topic-ref")
        await self._register(
            node,
            kind=_NODE_KIND_TOPIC,
            ref=artifact_ref,
            parents=_require_parents(node, parents, policy),
            policy=policy,
            revision=None,
        )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every currently usable node id, sorted."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event")
        if event.node_id != event.node_id.strip():
            raise SourceLifecycleError("event-node-id")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            await self._registry.ensure_schema()
            nodes = await self._registry.read_graph(self.scope_id)
            node = nodes.get(event.node_id)
            if node is None or node.kind != _NODE_KIND_SOURCE:
                raise SourceLifecycleError("unknown-source")
            if event.revision != node.revision:
                raise SourceLifecycleError("revision-mismatch")
            if event.sequence < node.sequence:
                raise SourceLifecycleError("stale-sequence")
            if event.sequence == node.sequence and event.available != node.available:
                raise SourceLifecycleError("sequence-conflict")
            if event.sequence > node.sequence:
                await self._registry.write_source_state(
                    self.scope_id, node.node_id, sequence=event.sequence, available=event.available
                )
            state = await self._propagate()
            return sorted(state.active)

    async def resolve(self, node_id: str, /, *, purpose: str) -> _Reference:
        """Return the registered reference for a node under one access purpose."""

        if purpose not in (_PURPOSE_GENERATION, _PURPOSE_AUDIT):
            raise SourceLifecycleError("unknown-purpose")
        async with self._runtime._scope_operation(self.scope_id):
            state = await self._registry.scope_state(self.scope_id)
        node = state.nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == _PURPOSE_GENERATION and node.node_id not in state.active:
            raise SourceLifecycleError("inactive-node")
        return node.ref

    async def _register(
        self,
        node_id: str,
        *,
        kind: str,
        ref: _Reference,
        parents: tuple[str, ...],
        policy: str | None,
        revision: int | None,
    ) -> None:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            await self._registry.ensure_schema()
            nodes = await self._registry.read_graph(self.scope_id)
            existing = nodes.get(node_id)
            if existing is not None:
                if _same_binding(existing, kind=kind, ref=ref, parents=parents, policy=policy, revision=revision):
                    return
                raise SourceLifecycleError("identity-conflict")
            for parent in parents:
                if parent not in nodes:
                    raise SourceLifecycleError("missing-parent")
            await self._validate_reference(kind, ref)
            await self._registry.insert_node(
                self.scope_id,
                node_id,
                kind=kind,
                ref=ref,
                parents=parents,
                policy=policy,
                revision=revision,
            )
            await self._propagate()

    async def _validate_reference(self, kind: str, ref: _Reference) -> None:
        if kind == _NODE_KIND_SOURCE:
            assert isinstance(ref, SourceRef)
            await self._runtime.records.for_scope(self.scope_id).get_source(ref.source_type, ref.source_id)
            return
        if kind == _NODE_KIND_TOPIC:
            assert isinstance(ref, ArtifactRef)
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                ref.family, ref.artifact_id, ref.revision
            )
            return
        assert isinstance(ref, MemoryCitation)
        async with self._runtime._context(self.scope_id) as context:
            await context.artifacts.memory.validate_citation(ref)

    async def _propagate(self) -> _ScopeState:
        state = await self._registry.scope_state(self.scope_id, refresh=True)
        await self._apply_memory_states(state)
        return state

    async def _apply_memory_states(self, state: _ScopeState) -> None:
        desired: dict[str, bool] = {}
        for node in state.nodes.values():
            if node.kind == _NODE_KIND_MEMORY:
                assert isinstance(node.ref, MemoryCitation)
                desired[node.ref.entry_id] = node.node_id in state.active
        if not desired:
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            memory = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if memory is None:
                return
            current = {entry.entry_id: entry for entry in await service.entries(memory)}
            manifest = {entry.entry_id: entry.state for entry in memory.content.manifest.entries}
            deactivate = []
            reactivate = []
            for entry_id, want_active in desired.items():
                entry = current.get(entry_id)
                if entry is None:
                    continue
                target = "active" if want_active else "inactive"
                if manifest.get(entry_id) == target:
                    continue
                (reactivate if want_active else deactivate).append(entry)
            if deactivate:
                memory = await service.forget(memory, entries=tuple(deactivate), reason=_RETRACTION_REASON)
            if reactivate:
                await service.reactivate(memory, entries=tuple(reactivate), reason=_RESTORATION_REASON)


class SourceLifecycleRegistry:
    """Persist and evaluate the evidence graph for every Scope of one Runtime."""

    def __init__(self, runtime: BuiltinRuntime, /, *, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()
        self._cache: dict[str, _ScopeState] = {}

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            database = self._require_database()
            async with database.transaction() as connection:
                for statement in _SCHEMA_STATEMENTS:
                    await connection.execute(text(statement))
            self._schema_ready = True

    async def read_graph(self, scope_id: str) -> dict[str, _Node]:
        await self.ensure_schema()
        database = self._require_database()
        async with database.transaction() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT node_id, kind, parents, policy, ref, revision, sequence, available "
                        "FROM pc_source_lifecycle_node WHERE scope_id = :scope_id"
                    ),
                    {"scope_id": scope_id},
                )
            ).mappings().all()
        return {row["node_id"]: _decode_node(row) for row in rows}

    async def insert_node(
        self,
        scope_id: str,
        node_id: str,
        *,
        kind: str,
        ref: _Reference,
        parents: tuple[str, ...],
        policy: str | None,
        revision: int | None,
    ) -> None:
        database = self._require_database()
        async with database.transaction() as connection:
            await connection.execute(
                text(
                    "INSERT INTO pc_source_lifecycle_node "
                    "(scope_id, node_id, kind, parents, policy, ref, revision, sequence, available) "
                    "VALUES (:scope_id, :node_id, :kind, :parents, :policy, :ref, :revision, :sequence, :available)"
                ),
                {
                    "scope_id": scope_id,
                    "node_id": node_id,
                    "kind": kind,
                    "parents": json.dumps(list(parents)),
                    "policy": policy,
                    "ref": ref.model_dump_json(),
                    "revision": revision,
                    "sequence": 0,
                    "available": 1 if kind == _NODE_KIND_SOURCE else 0,
                },
            )
        self._cache.pop(scope_id, None)

    async def write_source_state(self, scope_id: str, node_id: str, *, sequence: int, available: bool) -> None:
        database = self._require_database()
        async with database.transaction() as connection:
            await connection.execute(
                text(
                    "UPDATE pc_source_lifecycle_node SET sequence = :sequence, available = :available "
                    "WHERE scope_id = :scope_id AND node_id = :node_id"
                ),
                {
                    "scope_id": scope_id,
                    "node_id": node_id,
                    "sequence": sequence,
                    "available": 1 if available else 0,
                },
            )
        self._cache.pop(scope_id, None)

    async def scope_state(self, scope_id: str, /, *, refresh: bool = False) -> _ScopeState:
        if not refresh:
            cached = self._cache.get(scope_id)
            if cached is not None:
                return cached
        nodes = await self.read_graph(scope_id)
        state = _ScopeState(active=frozenset(_compute_active(nodes)), nodes=nodes)
        self._cache[scope_id] = state
        return state

    async def filter_topic_search(self, scope_id: str, result: Any, /) -> Any:
        # Topic search is a shared read path: a Runtime without lifecycle storage or an
        # unexpected storage failure must stay transparent rather than fail the search.
        if self._database is None:
            return result
        try:
            state = await self.scope_state(scope_id)
        except Exception:  # noqa: BLE001 - never let lifecycle gating break Topic search
            return result
        suppressed = {
            node.ref.artifact_id
            for node in state.nodes.values()
            if node.kind == _NODE_KIND_TOPIC and node.node_id not in state.active and isinstance(node.ref, ArtifactRef)
        }
        if not suppressed:
            return result
        hits = tuple(hit for hit in result.hits if hit.artifact_ref.artifact_id not in suppressed)
        if len(hits) == len(result.hits):
            return result
        return result.model_copy(update={"hits": hits})

    def _require_database(self) -> AsyncDatabase:
        if self._database is None:
            raise SourceLifecycleError("database")
        return self._database


async def _head_or_none(service: Any, artifact_id: str) -> Any:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


def _require_node_id(node_id: object) -> str:
    if not isinstance(node_id, str) or not node_id or node_id != node_id.strip():
        raise SourceLifecycleError("node-id")
    if len(node_id) > _MAX_NODE_ID_LENGTH:
        raise SourceLifecycleError("node-id-length")
    return node_id


def _require_parents(node_id: str, parents: Sequence[str], policy: str) -> tuple[str, ...]:
    if policy not in _DERIVED_POLICIES:
        raise SourceLifecycleError("policy")
    resolved = tuple(parents)
    if not resolved:
        raise SourceLifecycleError("empty-parents")
    if any(not isinstance(parent, str) or not parent for parent in resolved):
        raise SourceLifecycleError("parent")
    if node_id in resolved:
        raise SourceLifecycleError("self-loop")
    if len(set(resolved)) != len(resolved):
        raise SourceLifecycleError("duplicate-parents")
    return resolved


def _same_binding(
    node: _Node,
    *,
    kind: str,
    ref: _Reference,
    parents: tuple[str, ...],
    policy: str | None,
    revision: int | None,
) -> bool:
    return (
        node.kind == kind
        and node.ref == ref
        and node.parents == parents
        and node.policy == policy
        and node.revision == revision
    )


def _decode_node(row: Any) -> _Node:
    kind = row["kind"]
    parents = tuple(json.loads(row["parents"]))
    if kind == _NODE_KIND_SOURCE:
        ref: _Reference = SourceRef.model_validate_json(row["ref"])
    elif kind == _NODE_KIND_TOPIC:
        ref = ArtifactRef.model_validate_json(row["ref"])
    else:
        ref = MemoryCitation.model_validate_json(row["ref"])
    return _Node(
        node_id=row["node_id"],
        kind=kind,
        parents=parents,
        policy=row["policy"],
        ref=ref,
        revision=row["revision"],
        sequence=row["sequence"],
        available=bool(row["available"]),
    )


def _compute_active(nodes: dict[str, _Node]) -> set[str]:
    active: set[str] = set()
    for node_id in _topological_order(nodes):
        node = nodes[node_id]
        if node.kind == _NODE_KIND_SOURCE:
            if node.available:
                active.add(node_id)
            continue
        support = [parent in active for parent in node.parents]
        supported = all(support) if node.policy == "all" else any(support)
        if supported:
            active.add(node_id)
    return active


def _topological_order(nodes: dict[str, _Node]) -> list[str]:
    indegree = {node_id: 0 for node_id in nodes}
    children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        for parent in node.parents:
            if parent in nodes:
                indegree[node_id] += 1
                children[parent].append(node_id)
    queue = [node_id for node_id in nodes if indegree[node_id] == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop()
        order.append(node_id)
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) < len(nodes):
        seen = set(order)
        order.extend(node_id for node_id in nodes if node_id not in seen)
    return order
