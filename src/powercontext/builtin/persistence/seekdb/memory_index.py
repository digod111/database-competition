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

"""Embedded seekdb Memory search indexes with one native hybrid candidate query."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

# The two branches share the frozen active-head projections: the full-text branch
# reads ``pc_memory_entry_heads`` and the vector branch reads
# ``pc_memory_vector_entries``. ``NULL AS distance`` keeps the UNION column shape
# identical while leaving the full-text rows without a semantic distance.
_SEEKDB_FTS_BRANCH_SQL = """
SELECT 'fts' AS channel,
       h.memory_artifact_id,
       h.head_revision,
       h.entry_id,
       h.entry_version_id,
       v.text,
       CAST(NULL AS DOUBLE) AS distance
FROM pc_memory_entry_heads AS h
JOIN pc_memory_entry_versions AS v
  ON v.scope_id = h.scope_id
 AND v.memory_artifact_id = h.memory_artifact_id
 AND v.entry_version_id = h.entry_version_id
WHERE h.scope_id = :scope_id
  AND h.memory_artifact_id IN :memory_artifact_ids
  AND MATCH(h.searchable_text) AGAINST(:analyzed_query)
ORDER BY MATCH(h.searchable_text) AGAINST(:analyzed_query) DESC, h.memory_artifact_id, h.entry_id, h.entry_version_id
LIMIT :candidate_limit
"""
_SEEKDB_VECTOR_BRANCH_SQL = """
SELECT 'vector' AS channel,
       m.memory_artifact_id,
       m.head_revision,
       m.entry_id,
       m.entry_version_id,
       v.text,
       l2_distance(m.embedding, :query_vector) AS distance
FROM pc_memory_vector_entries AS m
JOIN pc_memory_entry_versions AS v
  ON v.scope_id = m.scope_id
 AND v.memory_artifact_id = m.memory_artifact_id
 AND v.entry_version_id = m.entry_version_id
WHERE m.scope_id = :scope_id
  AND m.memory_artifact_id IN :memory_artifact_ids
ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
LIMIT :candidate_limit
"""


class SeekDBMemoryHybridIndex:
    """Seekdb Memory search that resolves a hybrid request in one native statement.

    Full-text-only and vector-only requests keep delegating to the frozen
    single-channel strategies, so their public behaviour is unchanged. A hybrid
    request is answered by a single ``UNION ALL`` statement so that both candidate
    rankings are produced by their native indexes instead of two round trips fused
    in Python.
    """

    def __init__(self, profile: EmbeddingProfile) -> None:
        self._profile = profile
        self._fts = OceanBaseMemoryFTSIndex()
        self._vector = OceanBaseMemoryVectorIndex(profile)
        self.capabilities = MemoryCapabilities(
            fts=True,
            vector=True,
            hybrid=True,
            tag_filter=True,
            embedding_profile=profile,
        )
        self.tables = self._fts.tables + self._vector.tables
        self._hybrid_sql = text(self._union_sql(tagged=False)).bindparams(
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )
        self._tagged_hybrid_sql = text(self._union_sql(tagged=True)).bindparams(
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("tag_keys", expanding=True),
            bindparam("tag_hashes", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )

    @staticmethod
    def _union_sql(*, tagged: bool) -> str:
        fts = _SEEKDB_FTS_BRANCH_SQL
        vector = _SEEKDB_VECTOR_BRANCH_SQL
        if tagged:
            fts = fts.replace("ORDER BY", memory_tag_sql("h") + "ORDER BY")
            # A tag predicate reads the tag table per head, so the approximate
            # top-k shortcut no longer applies; drop it like the vector channel.
            vector = vector.replace("ORDER BY", memory_tag_sql("m") + "ORDER BY").replace(" APPROXIMATE", "")
        return f"({fts})\nUNION ALL\n({vector})"

    async def initialize(self, connection: AsyncConnection, /) -> None:
        await self._fts.initialize(connection)
        await self._vector.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._fts.replace(connection, scope_id, memory_ref, projections)
        await self._vector.replace(connection, scope_id, memory_ref, projections)

    async def delete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        entry_ids: tuple[str, ...],
        /,
    ) -> None:
        await self._fts.delete(connection, scope_id, memory_ref, entry_ids)
        await self._vector.delete(connection, scope_id, memory_ref, entry_ids)

    async def upsert(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._fts.upsert(connection, scope_id, memory_ref, projections)
        await self._vector.upsert(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode == "fts":
            return await self._fts.search(connection, scope_id, request)
        if request.mode == "vector":
            return await self._vector.search(connection, scope_id, request)
        if request.mode != "hybrid":
            return MemorySearchChannels()
        if request.embedding_profile != self._profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector.vector_complete(connection, scope_id, request.memories, self._profile):
            raise CapabilityNotSupportedError("vector")
        rows = (
            await connection.execute(
                self._hybrid_sql if request.tag_filter is None else self._tagged_hybrid_sql,
                {
                    "scope_id": scope_id,
                    "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                    "analyzed_query": request.analyzed_query,
                    "query_vector": validate_embedding(
                        request.query_vector,
                        dimension=self._profile.dimension,
                    ),
                    "candidate_limit": request.candidate_limit,
                    **memory_tag_parameters(request.tag_filter),
                },
            )
        ).mappings()
        fts_rows: list[dict[str, object]] = []
        vector_rows: list[dict[str, object]] = []
        for row in rows:
            materialized = dict(row)
            (fts_rows if materialized["channel"] == "fts" else vector_rows).append(materialized)
        return MemorySearchChannels(
            fts=memory_channel_hits(fts_rows, request.memories),
            vector=memory_channel_hits(vector_rows, request.memories),
        )

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return await self._vector.vector_complete(connection, scope_id, memories, profile)

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        projections = await self._fts.hydrate(connection, scope_id, projections)
        return await self._vector.hydrate(connection, scope_id, projections)
