"""Chapter import cleanup regression tests."""

from app.bridge.chapter_import import _clean_content_for_import


def test_removes_review_markers_before_plain_text():
    content = "  [待修订]\n[未通过]\n\n第一段正文。\n第二段正文。"
    assert _clean_content_for_import(content) == "第一段正文。\n第二段正文。"


def test_extracts_body_from_valid_json_wrapper():
    content = '{"title":"旧账浮现","body":"第一段。\\n\\n第二段。"}'
    assert _clean_content_for_import(content) == "第一段。\n\n第二段。"


def test_extracts_body_from_invalid_json_with_literal_newlines():
    content = '{"title":"旧账浮现","body":"第一段。\n\n第二段。"}'
    assert _clean_content_for_import(content) == "第一段。\n\n第二段。"


def test_plain_content_is_preserved_except_leading_whitespace():
    content = "\n\n第一段正文。\n\n第二段正文。"
    assert _clean_content_for_import(content) == "第一段正文。\n\n第二段正文。"


def test_preserves_escaped_quotes_inside_body():
    content = '{"title":"旧账浮现","body":"她说：\\"不要回头。\\"\n然后关上门。"}'
    assert _clean_content_for_import(content) == '她说："不要回头。"\n然后关上门。'


def test_stops_at_body_closing_quote_not_later_fields():
    content = '{"title":"旧账浮现","body":"正文。","title_alts":["备选"]}'
    assert _clean_content_for_import(content) == "正文。"


def test_final_chapter_number_accepts_only_selected_chapters():
    """ch_NNNN.txt 才是正式章节；A/B/C 候选与 meta 不得当章节导入。

    历史缺陷（e2e 实跑 2026-07-19）：两个导入循环用 glob("ch_*.txt") +
    split("_")[1] 取章号，把 bootstrap 候选 ch_0001_vA/vB/vC.txt 也当第 1
    章——首次导入被去重挡住，但 reimport（force 覆盖）按排序最后写，
    用 vC 候选覆盖用户已选定的正式稿。"""
    from app.bridge.chapter_import import _final_chapter_number

    assert _final_chapter_number("ch_0001.txt") == 1
    assert _final_chapter_number("ch_0012.txt") == 12
    assert _final_chapter_number("ch_12.txt") == 12
    for name in ("ch_0001_vA.txt", "ch_0001_vB.txt", "ch_0001_vC.txt",
                 "ch_0001_meta.json", "ch_abc.txt", "chapter_1.txt"):
        assert _final_chapter_number(name) is None, name
