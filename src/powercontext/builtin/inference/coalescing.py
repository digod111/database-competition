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

"""Coalesce concurrent embedding requests and cache successful vectors.

A :class:`CoalescingEmbeddingModel` sits in front of any object implementing the
:class:`~powercontext.builtin.inference.protocols.EmbeddingModel` port. It lets callers that
are already on the same event loop share the cost of identical text:

* identical texts submitted by one call, by concurrent calls inside the coalescing window,
  and by calls that arrive while a request is still running share one underlying request;
* texts that already produced a vector are served from a bounded LRU cache instead of the
  provider, while successful vectors alone are cached (failures may be retried);
* the wrapper never issues an underlying request larger than ``max_batch_size`` unique texts
  and never issues one for empty input.

Every returned :class:`EmbeddingResult` preserves the caller's original order and its
duplicate positions. ``usage`` aggregates the distinct underlying batches that this caller
actually waited for, so a shared batch is charged to each of its waiters exactly once and a
call served entirely from cache reports ``requests == 0``.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]


class _Batch:
    """One underlying request shared by every cell assigned to it."""

    __slots__ = ("cells", "usage")

    def __init__(self, cells: list[_Cell]) -> None:
        self.cells = cells
        self.usage: InferenceUsage | None = None


class _Cell:
    """The single in-flight result slot for one distinct text."""

    __slots__ = ("text", "future", "batch")

    def __init__(self, text: str) -> None:
        self.text = text
        self.future: asyncio.Future[EmbeddingVector] = asyncio.get_running_loop().create_future()
        self.batch: _Batch | None = None


class CoalescingEmbeddingModel:
    """Wrap an embedding model with cross-call request merging and an LRU vector cache."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self._base = model
        self.profile: EmbeddingProfile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_ms = coalesce_ms
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, _Cell] = {}
        self._inflight: dict[str, _Cell] = {}
        self._flush: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[object]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed ``texts`` once per distinct value, restoring order and duplicates."""

        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique = tuple(dict.fromkeys(texts))
        cached: dict[str, EmbeddingVector] = {}
        cells: dict[str, _Cell] = {}
        scheduled = False
        for text in unique:
            if text in self._cache:
                self._cache.move_to_end(text)
                cached[text] = self._cache[text]
                continue
            cell = self._inflight.get(text) or self._pending.get(text)
            if cell is None:
                cell = _Cell(text)
                self._pending[text] = cell
                scheduled = True
            cells[text] = cell
        if scheduled:
            self._ensure_flush()

        resolved: dict[str, EmbeddingVector] = {}
        if cells:
            ordered = list(cells)
            results = await asyncio.gather(*(asyncio.shield(cells[text].future) for text in ordered))
            resolved = dict(zip(ordered, results))

        vectors = tuple(cached[text] if text in cached else resolved[text] for text in texts)
        return EmbeddingResult(vectors=vectors, usage=self._summarize_usage(unique, cached, cells))

    def _summarize_usage(
        self,
        unique: tuple[str, ...],
        cached: dict[str, EmbeddingVector],
        cells: dict[str, _Cell],
    ) -> InferenceUsage:
        requests = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        seen: set[int] = set()
        for text in unique:
            if text in cached:
                continue
            batch = cells[text].batch
            if batch is None or batch.usage is None or id(batch) in seen:
                continue
            seen.add(id(batch))
            requests += batch.usage.requests
            if batch.usage.input_tokens is not None:
                input_tokens = (input_tokens or 0) + batch.usage.input_tokens
            if batch.usage.output_tokens is not None:
                output_tokens = (output_tokens or 0) + batch.usage.output_tokens
        return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)

    def _ensure_flush(self) -> None:
        task = self._flush
        if task is not None and not task.done():
            return
        task = asyncio.get_running_loop().create_task(self._flush_loop())
        self._flush = task
        self._tasks.add(task)
        task.add_done_callback(self._on_flush_done)

    def _on_flush_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if self._flush is task:
            self._flush = None
        if self._pending:
            self._ensure_flush()

    async def _flush_loop(self) -> None:
        if self._coalesce_ms > 0:
            await asyncio.sleep(self._coalesce_ms / 1000.0)
        else:
            await asyncio.sleep(0)
        pending = self._pending
        self._pending = {}
        if not pending:
            return
        texts = list(pending)
        for text in texts:
            self._inflight[text] = pending[text]
        for start in range(0, len(texts), self._max_batch_size):
            chunk = texts[start : start + self._max_batch_size]
            cells = [pending[text] for text in chunk]
            batch = _Batch(cells)
            for cell in cells:
                cell.batch = batch
            self._spawn_batch(batch)

    def _spawn_batch(self, batch: _Batch) -> None:
        task = asyncio.get_running_loop().create_task(self._run_batch(batch))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, batch: _Batch) -> None:
        cells = batch.cells
        texts = tuple(cell.text for cell in cells)
        try:
            result = await self._base.embed(texts)
            vectors = result.vectors
            if len(vectors) != len(texts):
                raise ValueError(
                    f"embedding model returned {len(vectors)} vectors for {len(texts)} texts"
                )
        except BaseException as error:  # noqa: BLE001 - propagate the failure to every waiter
            for cell in cells:
                if not cell.future.done():
                    cell.future.set_exception(error)
            self._forget_inflight(cells)
            return
        batch.usage = result.usage
        for cell, vector in zip(cells, vectors):
            self._store(cell.text, vector)
            if not cell.future.done():
                cell.future.set_result(vector)
        self._forget_inflight(cells)

    def _forget_inflight(self, cells: list[_Cell]) -> None:
        for cell in cells:
            if self._inflight.get(cell.text) is cell:
                del self._inflight[cell.text]

    def _store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
