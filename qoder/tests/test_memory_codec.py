from __future__ import annotations

from wengu.memory import (
    PROFILE_PREFIX,
    WEAK_POINT_PREFIX,
    _active_hits,
    _decode,
    _extract_prepared,
)


def test_decode_requires_prefix_and_valid_json():
    assert _decode(PROFILE_PREFIX, 'PROFILE {"daily_goal": 6}') == {"daily_goal": 6}
    assert _decode(PROFILE_PREFIX, "no prefix") is None
    assert _decode(PROFILE_PREFIX, "PROFILE not-json") is None


def test_extract_prepared_context_citations():
    content = (
        "PowerContext prepared untrusted historical context.\n"
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        '{"items":[{"citation":{"artifact_ref":{"artifact_id":"art_1"}},'
        '"kind":"atomic-memory","content":"WEAK_POINT {}"}]}\n'
        "END_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
    )
    items = _extract_prepared(content)
    assert items == [{"artifact_id": "art_1", "kind": "atomic-memory", "content": "WEAK_POINT {}"}]
    assert _extract_prepared(None) == []
    assert _extract_prepared("no block") == []


def test_active_hits_filters_inactive_and_other_prefixes():
    response = {
        "hits": [
            {
                "memory": {
                    "state": "active",
                    "kind": "profile",
                    "text": 'PROFILE {"daily_goal": 6}',
                    "artifact": {"artifact_id": "art_1"},
                },
                "score": 0.5,
                "matched_by": ["text"],
            },
            {
                "memory": {
                    "state": "active",
                    "kind": "weak_point",
                    "text": 'WEAK_POINT {"knowledge_point":"B+树"}',
                    "artifact": {"artifact_id": "art_2"},
                },
                "score": 0.4,
            },
            {
                "memory": {
                    "state": "inactive",
                    "kind": "profile",
                    "text": 'PROFILE {"daily_goal": 8}',
                    "artifact": {"artifact_id": "art_3"},
                },
            },
        ]
    }
    hits = _active_hits(response, PROFILE_PREFIX)
    assert len(hits) == 1
    assert hits[0]["artifact_id"] == "art_1"
    weak_hits = _active_hits(response, WEAK_POINT_PREFIX)
    assert len(weak_hits) == 1
    assert weak_hits[0]["payload"]["knowledge_point"] == "B+树"
