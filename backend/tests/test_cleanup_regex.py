"""Edge cases for the chapter JSON-wrapper fallback parser."""

from app.bridge.chapter_import import _clean_content_for_import


def test_preserves_escaped_quotes_inside_body():
    content = (
        '{"title":"旧账浮现","body":"她说：\\"不要回头。\\"\n然后关上门。"}'
    )

    assert _clean_content_for_import(content) == '她说："不要回头。"\n然后关上门。'


def test_stops_at_body_closing_quote_not_later_fields():
    content = '{"title":"旧账浮现","body":"正文。","title_alts":["备选"]}'

    assert _clean_content_for_import(content) == "正文。"
