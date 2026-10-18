"""Extract readable article text from already fetched HTML; never fetch resources."""
import json
import re
import unicodedata
from urllib.parse import urlsplit

from fastapi import HTTPException
from lxml import etree, html as lxml_html
from trafilatura import bare_extraction

from backend.ocr.service import ENGLISH_WORD

MAX_ARTICLE_CHARACTERS = 100_000
MAX_NODES = 50_000
_HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none\b|visibility\s*:\s*(?:hidden|collapse)\b)", re.I)
_DROP = {'script', 'style', 'noscript', 'template', 'nav', 'aside', 'footer',
         'form', 'button', 'input', 'textarea', 'select', 'dialog', 'iframe',
         'object', 'embed', 'svg', 'canvas'}
_CHALLENGES = ('just a moment', 'access denied', 'attention required', 'verify you are human',
               'checking your browser', 'sign in', 'log in', 'login', 'subscribe to continue')


def _unavailable():
    return HTTPException(422, '未能提取可阅读的英文正文；网页可能需要登录、付费或脚本加载，请改用“粘贴文章”。')


def _clean(value: str) -> str:
    return ''.join(char for char in value if unicodedata.category(char) not in {'Cs', 'Cc'} or char in '\n\t')


def _is_paid(value) -> bool:
    if isinstance(value, dict):
        if value.get('isAccessibleForFree') is False or str(value.get('isAccessibleForFree', '')).lower() == 'false':
            return True
        return any(_is_paid(item) for item in value.values())
    return isinstance(value, list) and any(_is_paid(item) for item in value)


def extract_article(html: str, source_url: str) -> tuple[str, str]:
    if not html.strip() or any(unicodedata.category(char) == 'Cs' for char in html):
        raise _unavailable()
    try:
        # HTML has already been decoded using the response's declared charset.
        # Bytes prevent an XML encoding declaration from overriding that decision.
        parser = lxml_html.HTMLParser(recover=True, no_network=True, huge_tree=False, encoding='utf-8')
        tree = lxml_html.fromstring(html.encode('utf-8'), parser=parser, base_url=None)
    except (etree.LxmlError, ValueError, UnicodeError):
        raise _unavailable() from None
    nodes = list(tree.iter())
    if len(nodes) > MAX_NODES:
        raise HTTPException(413, '网页结构过于复杂，请改用“粘贴文章”。')
    # Public HTML can advertise a paywall through standard article metadata.
    # Do not use hidden full text or structured data to work around it.
    for node in nodes:
        if not isinstance(node.tag, str):
            continue
        if node.tag == 'meta' and node.get('itemprop', '').lower() == 'isaccessibleforfree' and node.get('content', '').lower() == 'false':
            raise _unavailable()
        if node.tag == 'script' and node.get('type', '').lower() == 'application/ld+json':
            try:
                if _is_paid(json.loads(node.text or 'null')):
                    raise _unavailable()
            except (ValueError, RecursionError):
                pass
    # Work from a sanitized tree so title selection also ignores hidden headings.
    for node in reversed(nodes):
        if not isinstance(node.tag, str) or node is tree:
            continue
        hidden = ('hidden' in node.attrib or 'inert' in node.attrib
                  or node.get('aria-hidden', '').lower() == 'true'
                  or _HIDDEN_STYLE.search(node.get('style', '')))
        if node.tag in _DROP or hidden:
            node.drop_tree()
    titles = tree.xpath('(//article//h1 | //main//h1)[1]') or tree.xpath('//title[1]') or tree.xpath('//h1[1]')
    title = re.sub(r'\s+', ' ', _clean(titles[0].text_content())).strip() if titles else ''
    try:
        result = bare_extraction(tree, with_metadata=False, include_comments=False,
                                 include_tables=False, include_links=False, include_images=False,
                                 deduplicate=False)
        text = result.text if result else ''
    except (etree.LxmlError, ValueError, TypeError, RecursionError):
        raise _unavailable() from None
    paragraphs = [re.sub(r'[^\S\n]+', ' ', _clean(line)).strip() for line in (text or '').splitlines()]
    text = '\n\n'.join(line for line in paragraphs if line)
    if len(text) > MAX_ARTICLE_CHARACTERS:
        raise HTTPException(413, '网页正文超过 100,000 字，请选择较短的文章或粘贴需要阅读的部分。')
    words = list(ENGLISH_WORD.finditer(text))
    if len(text) < 80 or len(words) < 10:
        raise _unavailable()
    if any(title.lower().strip(' .!…').startswith(value) for value in _CHALLENGES) and len(words) < 100:
        raise _unavailable()
    title = (title or next((line for line in paragraphs if line), '') or urlsplit(source_url).hostname or '网页文章')[:80]
    return title, text
