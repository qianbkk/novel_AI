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
