import socket

from fastapi import HTTPException
import pytest

from backend.services import web_extraction
from backend.services.text_import import text_occurrences

PARAGRAPHS = '''<p>🧠 中文引导 — A model learns from examples, and a curious reader notices the patterns.</p>
<p>The model improves when readers compare ideas. Reading a little every day makes new words memorable.</p>
<p>A careful reader asks questions, explores unfamiliar words, and returns to useful examples. A model can offer help, but personal curiosity keeps learning meaningful.</p>
<p>Although difficult sentences take patience, regular practice builds confidence. The model becomes clearer when we examine its context and discuss the evidence.</p>'''


def page(body=PARAGRAPHS, *, head='', heading='Learning with curiosity 🧠'):
    return f'<html><head><title>Site title</title>{head}</head><body><main><article><h1>{heading}</h1>{body}</article></main></body></html>'


def extract(value):
    return web_extraction.extract_article(value, 'https://example.com/article')


def test_main_body_hidden_boilerplate_and_unicode_are_preserved_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Extraction must never open an outbound connection')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    html = page(PARAGRAPHS + '<aside>AD_GARBAGE</aside><p hidden>HIDDEN_GARBAGE</p>'
                '<p aria-hidden="true">ARIA_GARBAGE</p><p style="color:red;display: none !important">STYLE_GARBAGE</p>'
                '<form><p>FORM_GARBAGE</p></form><script>alert("SCRIPT_GARBAGE")</script>'
                '<template><p>TEMPLATE_GARBAGE</p></template>')
    html = html.replace('<main>', '<nav>NAV_GARBAGE</nav><main>').replace('</main>', '</main><footer>FOOTER_GARBAGE</footer>')
    title, text = extract(html)
    assert title == 'Learning with curiosity 🧠' and '🧠 中文引导' in text
    assert 'GARBAGE' not in text and '\n\n' in text
    assert text.count('model') == 4
    for word in text_occurrences(text):
        assert text[word.text_start:word.text_end] == word.word


def test_div_based_article_and_literal_markup_are_text_not_executable():
    title, text = extract(page(PARAGRAPHS + '<p>The phrase &lt;script&gt; is a literal tag in this explanation, not executable content.</p>').replace('<article>', '<div class="story">').replace('</article>', '</div>'))
    assert 'Learning' in title and '<script>' in text
    assert 'Although difficult sentences take patience' in text


def test_hidden_h1_is_not_used_and_long_title_is_bounded():
    title, _ = extract(page().replace('<h1>', '<h1 hidden>Hidden heading</h1><h1>'))
    assert title == 'Learning with curiosity 🧠'
    title, _ = extract(page(heading='Reading ' * 30))
    assert len(title) == 80


def test_article_heading_takes_priority_over_site_banner():
    html = page().replace('<main>', '<header><h1>Site banner</h1></header><main>')
    assert extract(html)[0] == 'Learning with curiosity 🧠'


def test_classic_table_layout_keeps_prose_instead_of_rejecting_the_article():
    content = '<html><head><title>Reading practice</title></head><body><table><tr><td><img src="/logo.png"></td><td><font face="verdana">' + PARAGRAPHS + '</font></td></tr></table></body></html>'
    title, text = extract(content)
    assert title == 'Reading practice' and 'curious reader' in text and 'Although difficult' in text


@pytest.mark.parametrize('head', [
    '<meta itemprop="isAccessibleForFree" content="false">',
    '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>',
    '<script type="application/ld+json">[{"@graph":[{"isAccessibleForFree":"False"}]}]</script>',
])
def test_paywall_metadata_rejects_instead_of_extracting_hidden_article(head):
    with pytest.raises(HTTPException) as result:
        extract(page(head=head))
    assert result.value.status_code == 422


@pytest.mark.parametrize('html', ['', '<html><body><div id="root"></div></body></html>',
    page(body='<p>中文正文没有有效的英文段落。</p>', heading='中文标题'),
    page(body='<p>Hello world.</p>'), page(body=PARAGRAPHS[:180], heading='Access denied'), '\ud800'])
def test_unreadable_short_or_invalid_pages_have_actionable_failure(html):
    with pytest.raises(HTTPException) as result:
        extract(html)
    assert result.value.status_code == 422 and '粘贴文章' in result.value.detail


def test_article_limit_rejects_without_silent_truncation(monkeypatch):
    monkeypatch.setattr(web_extraction, 'MAX_ARTICLE_CHARACTERS', 100)
    with pytest.raises(HTTPException) as result:
        extract(page())
    assert result.value.status_code == 413


def test_invalid_jsonld_is_ignored_but_never_used_as_article():
    _, text = extract(page(head='<script type="application/ld+json">{invalid}</script>'))
    assert 'curious reader' in text and '{invalid}' not in text
