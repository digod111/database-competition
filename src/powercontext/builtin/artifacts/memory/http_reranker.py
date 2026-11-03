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

"""HTTP cross-encoder reranking over bounded candidate batches.

The scoring service enforces a per-request candidate ceiling, so the adapter splits one coarse
pool into batches, maps each batch back to the original one-based global ranks, and only then
performs a single global selection. Batching never renumbers candidates: every request body
carries the original global ``rank`` and its matching text, and the returned ``rank`` values map
straight back onto the caller's candidate tuple.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankCandidate,
    MemoryRerankDecision,
)
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Select Memory hits with scores from a batched HTTP rerank service."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate in batches, then select the global top ``limit`` ranks."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scored: list[tuple[int, float]] = []
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = range(start + 1, start + len(batch) + 1)
            response = await self._client.post(
                self._endpoint,
                json=_request_body(query, batch, start=start),
            )
            response.raise_for_status()
            requests += 1
            scored.extend(_parse_scores(response, expected_ranks=ranks))

        ordered = sorted(scored, key=lambda item: (-item[1], item[0]))
        selected_ranks = tuple(rank for rank, _score in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=requests),
        )


def _request_body(query: str, batch: tuple[MemoryHit, ...], *, start: int) -> dict[str, object]:
    return {
        "query": query,
        "candidates": [
            MemoryRerankCandidate(rank=start + offset + 1, text=candidate.text).model_dump()
            for offset, candidate in enumerate(batch)
        ],
    }


def _parse_scores(response: httpx.Response, *, expected_ranks: Iterable[int]) -> list[tuple[int, float]]:
    try:
        body = response.json()
    except ValueError as error:
        raise ValueError("rerank response is not valid JSON") from error
    if not isinstance(body, dict) or set(body) != {"scores"}:
        raise ValueError("rerank response must contain exactly the 'scores' field")  # noqa: TRY003
    raw_scores = body["scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("rerank response 'scores' must be a list")  # noqa: TRY003

    parsed: dict[int, float] = {}
    for item in raw_scores:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("each score must contain exactly 'rank' and 'score'")  # noqa: TRY003
        rank = item["rank"]
        score = item["score"]
        if type(rank) is not int or rank < 1:
            raise ValueError("score rank must be a positive integer")  # noqa: TRY003
        if type(score) is bool or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("score must be a finite number")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("rerank response repeats a rank")  # noqa: TRY003
        parsed[rank] = float(score)

    expected = set(expected_ranks)
    if set(parsed) != expected:
        raise ValueError("rerank response must cover exactly this batch's ranks")  # noqa: TRY003
    return [(rank, parsed[rank]) for rank in expected_ranks]


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
