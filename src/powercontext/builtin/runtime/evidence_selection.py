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

"""Rank literal, query-relevant spans inside recalled Memory text.

The ranking is deliberately offline and deterministic: it scores whole sentences by the
smoothed inverse document frequency of the query words they contain, then hands back spans that
index the *original* text. Nothing here rewrites, translates, or summarizes a memory: a span is
always a character range into the candidate that produced it, so the caller can cite exactly
what was selected. The caller owns the byte budget, because only the caller knows how much the
surrounding citations and envelope cost.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactRef

_TOKEN = re.compile(r"[a-z]+|\d+")
_BOUNDARY = re.compile(r"[.!?](?=\s)|\n")


@dataclass(frozen=True)
class EvidenceCandidate:
    """One recalled Memory entry whose text may contribute literal fragments."""

    scope_id: str
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str


@dataclass(frozen=True)
class EvidenceSpan:
    """A half-open character range into ``EvidenceCandidate.text``."""

    candidate: int
    start: int
    end: int


def query_terms(query: str) -> tuple[str, ...]:
    """Extract the ordered, de-duplicated content words of one query."""

    return tuple(dict.fromkeys(_TOKEN.findall(query.lower())))


def split_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """Partition ``text`` into contiguous character spans at sentence boundaries.

    The spans tile the whole string, so two neighbouring spans are always adjacent. That lets the
    caller merge two selected spans without copying or skipping any literal character between
    them.
    """

    if not text:
        return ()
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        while end < len(text) and text[end].isspace():
            end += 1
        if end > start:
            spans.append((start, end))
            start = end
    if start < len(text):
        spans.append((start, len(text)))
    return tuple(spans) if spans else ((0, len(text)),)


def rank_spans(query: str, candidates: Sequence[EvidenceCandidate]) -> tuple[EvidenceSpan, ...]:
    """Order candidate sentences best-first, alternating across candidates.

    Sentences with no query word are dropped: inside a long entry they are usually boilerplate
    that would only consume budget. Alternating by candidate keeps a request from spending its
    whole budget inside the first abstract, which matters when the evidence lives in a later one.
    """

    terms = query_terms(query)
    if not terms or not candidates:
        return ()

    per_candidate: list[tuple[tuple[int, int], ...]] = []
    per_sentence: list[list[dict[str, int]]] = []
    document_frequency = dict.fromkeys(terms, 0)
    for candidate in candidates:
        spans = split_sentences(candidate.text)
        per_candidate.append(spans)
        local_counts: list[dict[str, int]] = []
        for start, end in spans:
            counts: dict[str, int] = {}
            for token in _TOKEN.findall(candidate.text[start:end].lower()):
                if token in document_frequency:
                    counts[token] = counts.get(token, 0) + 1
            for token in counts:
                document_frequency[token] += 1
            local_counts.append(counts)
        per_sentence.append(local_counts)

    total = sum(len(spans) for spans in per_candidate) or 1
    idf = {term: math.log(1.0 + total / (1.0 + document_frequency[term])) for term in terms}

    ranked: list[list[EvidenceSpan]] = []
    for index, spans in enumerate(per_candidate):
        scored: list[tuple[float, int, int, int]] = []
        for position, (start, end) in enumerate(spans):
            score = 0.0
            for term, frequency in per_sentence[index][position].items():
                score += idf[term] * (1.0 + math.log(frequency))
            if score > 0.0:
                scored.append((score, position, start, end))
        scored.sort(key=lambda item: (-item[0], item[1]))
        ranked.append([EvidenceSpan(candidate=index, start=start, end=end) for _, _, start, end in scored])

    ordered: list[EvidenceSpan] = []
    depth = 0
    while any(depth < len(spans) for spans in ranked):
        for spans in ranked:
            if depth < len(spans):
                ordered.append(spans[depth])
        depth += 1
    return tuple(ordered)


def merge_adjacent(spans: Sequence[EvidenceSpan]) -> tuple[EvidenceSpan, ...]:
    """Collapse adjoining spans of one candidate into the range they jointly cover.

    Merging drops one citation from the rendered output while keeping every literal character,
    so it never costs budget and always frees some. Ordering becomes document order, which the
    caller must keep for both the fragments and their origins.
    """

    merged: list[EvidenceSpan] = []
    for span in sorted(spans, key=lambda item: (item.candidate, item.start)):
        if merged and merged[-1].candidate == span.candidate and merged[-1].end == span.start:
            previous = merged[-1]
            merged[-1] = EvidenceSpan(candidate=previous.candidate, start=previous.start, end=span.end)
        else:
            merged.append(span)
    return tuple(merged)
