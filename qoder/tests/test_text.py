from __future__ import annotations

from wengu.text import (
    chunk_markdown,
    highlight_html,
    meta_list,
    parse_frontmatter,
    sha256_text,
    split_query_terms,
)


def test_parse_frontmatter_and_chunk():
    text = """---
course: 数据结构
chapter: 排序
title: 测试笔记
keywords: 快排, 归并
---
## 快速排序
快排的平均复杂度是 O(n log n)。

## 归并排序
归并需要额外 O(n) 空间。
"""
    meta, body = parse_frontmatter(text)
    assert meta["course"] == "数据结构"
    assert meta["keywords"] == "快排, 归并"
    sections = chunk_markdown(body)
    assert [section["heading"] for section in sections] == ["快速排序", "归并排序"]
    assert "O(n log n)" in sections[0]["content"]


def test_chunk_without_heading_uses_overview():
    sections = chunk_markdown("一段没有二级标题的正文。")
    assert len(sections) == 1
    assert sections[0]["heading"] == "概述"


def test_meta_list_supports_chinese_and_ascii_separators():
    assert meta_list("快排, 归并，堆排; 哈希、B+树") == ["快排", "归并", "堆排", "哈希", "B+树"]
    assert meta_list("") == []


def test_split_query_terms_drops_short_tokens_and_duplicates():
    terms = split_query_terms("B+树 插入 插入，分裂  a")
    assert "B+树" in terms
    assert terms.count("插入") == 1
    assert "a" not in terms


def test_highlight_escapes_html_and_marks_terms():
    html = str(highlight_html("<script>alert(1)</script> B+树 的插入与分裂", "B+树 插入"))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<mark>B+树</mark>" in html
    assert "<mark>插入</mark>" in html


def test_sha256_is_stable():
    assert sha256_text("温故") == sha256_text("温故")
    assert len(sha256_text("温故")) == 64
