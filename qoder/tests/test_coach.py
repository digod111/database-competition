from __future__ import annotations

from wengu.coach import _profile_with_defaults, compose_explanation


EVIDENCE = [
    {
        "id": "mat_1",
        "title": "排序算法对比 / 归并排序",
        "course": "数据结构",
        "chapter": "排序",
        "source_path": "notes/ds/01_sorting.md",
        "content": "归并排序把数组分成两半，分别排序后再合并两个有序子数组。时间复杂度稳定为 O(n log n)。",
        "score": 11.269,
    },
    {
        "id": "mat_2",
        "title": "排序算法对比 / 快速排序",
        "course": "数据结构",
        "chapter": "排序",
        "source_path": "notes/ds/01_sorting.md",
        "content": "快速排序采用分治思想，平均时间复杂度 O(n log n)。",
        "score": 5.1,
    },
]

QUESTION = {
    "knowledge_point": "归并排序",
    "explanation": "归并排序每次都能把区间均分。最坏和平均时间复杂度都是 O(n log n)。",
}


def test_profile_defaults_and_clamp():
    profile = _profile_with_defaults(None, "小林")
    assert profile["display_name"] == "小林"
    assert profile["daily_goal"] == 6
    assert profile["explanation_style"] == "concise"
    assert _profile_with_defaults({"daily_goal": 99})["daily_goal"] == 12
    assert _profile_with_defaults({"daily_goal": 1})["daily_goal"] == 3
    assert _profile_with_defaults({"explanation_style": "weird"})["explanation_style"] == "concise"


def test_compose_concise_explanation_uses_one_evidence():
    result = compose_explanation(QUESTION, EVIDENCE, "concise")
    assert result["style_label"] == "简洁"
    assert result["text"].endswith("。")
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["id"] == "mat_1"
    assert "<mark>归并排序</mark>" in result["evidence"][0]["snippet_html"]


def test_compose_detailed_explanation_uses_two_evidence():
    result = compose_explanation(QUESTION, EVIDENCE, "detailed")
    assert result["style_label"] == "详细"
    assert result["text"] == QUESTION["explanation"]
    assert len(result["evidence"]) == 2
    assert result["evidence"][1]["id"] == "mat_2"
