"""Tests for `backend/build.py`. Run with `uv run pytest`."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import build
from build import BuildError

ROOT = Path(__file__).resolve().parent.parent


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


# --- pages -----------------------------------------------------------------


def pages(source: str) -> list[build.RawPage]:
    return build.split_pages(source, Path('main.md'))


def test_split_pages_on_dashes():
    found = pages('# one\n\n---\n\n# two\n\n---\n# three\n')
    # Leading blank lines of a page are dropped with the directives; the rest of the body is kept verbatim.
    assert [(p.line, p.body) for p in found] == [(1, '# one\n'), (4, '# two\n'), (8, '# three\n')]
    assert [p.classes for p in found] == [[], [], []]
    assert [p.title for p in found] == [None, None, None]


def test_split_pages_ignores_dashes_in_fences_and_rules():
    source = '# one\n\n```md\n---\n```\n\n~~~\n---\n~~~\n\n***\n\n  --- \n\n# two\n'
    assert [p.line for p in pages(source)] == [1, 14]


def test_split_pages_reads_directives():
    source = (
        '<!-- class: cover light; title: Welcome; home -->\n\n# one\n\n---\n\n<!--\nclass: tight\ntitle: Two; and more\n-->\n'
        '<!-- class: extra -->\n# two\n\n---\n\n<!-- just a note -->\n# three\n'
    )
    [one, two, three] = pages(source)
    assert (one.classes, one.title, one.body) == (['cover', 'light'], 'Welcome; home', '# one\n')
    assert (two.classes, two.title, two.body) == (['tight', 'extra'], 'Two; and more', '# two\n')
    # A comment that does not open with `key:` is ordinary markdown and stays in the body.
    assert (three.classes, three.title, three.body) == ([], None, '<!-- just a note -->\n# three\n')


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        ('<slide/>\n# one\n', r'main\.md:1: <slide \.\.\./> is no longer supported; separate pages'),
        ('# one\n---\n# two\n', r'main\.md:2: put a blank line before ---'),
        ('---\n\n# one\n', r'main\.md:1: empty page: nothing before the first ---'),
        ('# one\n\n---\n\n---\n\n# two\n', r'main\.md:4: empty page: nothing between this --- and the previous one'),
        ('# one\n\n---\n\n<!-- class: x -->\n', r'main\.md:4: empty page'),
        ('\n\n', r'main\.md:1: empty page'),
        ('<!-- layout: cover -->\n# one\n', r"main\.md:1: unknown page directive 'layout'; use class or title"),
        ('<!-- class: a; title: x -->\n<!-- title: y -->\n# one\n', r"main\.md:2: page directive 'title' given twice"),
        ('<!-- class: cover; light -->\n# one\n', r"main\.md:1: invalid class name 'cover;'"),
        (
            '<!--\nclass: a\nnonsense\n-->\n# one\n',
            r"main\.md:1: cannot parse page directive 'nonsense' \(expected key: value\)",
        ),
        ('<!-- class: 1st -->\n# one\n', r"main\.md:1: invalid class name '1st'"),
        ('<!-- class: cover\n# one\n', r'main\.md:1: page directive comment is never closed'),
        ('# one\n\n<!-- class: light -->\n', r"main\.md:3: page directives must come before the page's content"),
    ],
)
def test_split_pages_errors(source: str, message: str):
    with pytest.raises(BuildError, match=message):
        pages(source)


def test_build_rejects_self_closing_component(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n<component src="X.html"/>\n')
    with pytest.raises(BuildError, match=r'main\.md:2: self-closing'):
        build.build_html(tmp_path)


# --- components ------------------------------------------------------------

MD = Path('main.md')
NAMES = build.BUILTINS


def collect(body: str, comps: Path, names: frozenset[str] = NAMES) -> dict[str, str]:
    """`collect_components` for one markdown root, reduced to the embedded sources."""
    return {src: spec.source for src, spec in build.collect_components([(body, MD)], comps, names).items()}


def test_collect_components_follows_nesting(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'A.html', '<div>a<component src="B.html"></component></div>')
    write(comps / 'B.html', '<p>b</p>')
    found = collect('<component src="A.html"></component>', comps)
    assert set(found) == {'A.html', 'B.html'}
    assert found['B.html'] == '<p>b</p>'


def test_collect_components_detects_cycle(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'A.html', '<component src="B.html"></component>')
    write(comps / 'B.html', '<component src="A.html"></component>')
    with pytest.raises(BuildError, match=re.escape('component cycle: A.html -> B.html -> A.html')):
        collect('<component src="A.html"></component>', comps)


def test_svg_component_is_inlined_without_prolog(tmp_path: Path):
    comps = tmp_path / 'components'
    write(
        comps / 'Arrow.svg',
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0L10 10" stroke="var(--accent)"/></svg>\n',
    )
    found = collect('<component src="Arrow.svg"></component>', comps)
    assert found['Arrow.svg'].startswith('<svg xmlns=')
    assert 'var(--accent)' in found['Arrow.svg']


def test_svg_component_must_be_svg(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'Nope.svg', '<div>not svg</div>')
    with pytest.raises(BuildError, match='expected an <svg> root element'):
        collect('<component src="Nope.svg"></component>', comps)


def test_component_extension_is_checked(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'Card.txt', '<div/>')
    with pytest.raises(BuildError, match=r'must be one of \.html, \.svg'):
        collect('<component src="Card.txt"></component>', comps)


def test_svg_component_reference_is_not_an_image(tmp_path: Path):
    assert build.check_images(['<component src="Arrow.svg"></component>'], tmp_path) == []


def test_images_inside_component_children_are_checked(tmp_path: Path):
    with pytest.raises(BuildError, match='image not found'):
        build.check_images(['<component src="Card.html" title="x">\n\n![x](assets/nope.png)\n\n</component>'], tmp_path)


def test_collect_components_rejects_escape(tmp_path: Path):
    comps = tmp_path / 'components'
    comps.mkdir()
    write(tmp_path / 'secret.html', 'x')
    with pytest.raises(BuildError, match='escapes'):
        collect('<component src="../secret.html"></component>', comps)


def test_collect_components_missing(tmp_path: Path):
    comps = tmp_path / 'components'
    comps.mkdir()
    with pytest.raises(BuildError, match='component not found'):
        collect('<component src="Nope.html"></component>', comps)


# --- component tags in markdown ----------------------------------------------


def uses(text: str, *, markdown: bool = True) -> list[build.ComponentUse]:
    return build.find_component_uses(text, MD, markdown=markdown)


def test_find_component_uses_reads_attributes_and_children():
    text = (
        '# Title\n\n<component src="Card.html" title="Fast builds" icon=\'x\' flag>\n\n'
        'Body with **markdown**.\n\n</component>\n\n<component src="Icon.svg"></component>\n'
    )
    [card, icon] = uses(text)
    assert (card.src, card.line, card.has_content) == ('Card.html', 3, True)
    assert card.attrs == {'title': 'Fast builds', 'icon': 'x', 'flag': ''}
    assert (icon.src, icon.line, icon.has_content, icon.attrs) == ('Icon.svg', 9, False, {})


def test_find_component_uses_handles_nesting_and_inline_use():
    text = (
        '<component src="Outer.html">\n\n<component src="Inner.html">\n\ninner\n\n</component>\n\n</component>\n'
        'Fast <component src="Tag.html">beta</component> text\n'
    )
    found = uses(text)
    assert [(u.src, u.line, u.has_content) for u in found] == [
        ('Inner.html', 3, True),
        ('Outer.html', 1, True),
        ('Tag.html', 10, True),
    ]


def test_find_component_uses_ignores_code():
    text = '```html\n<component src="Shown.html"></component>\n```\n\nUse `<component src="Inline.html">` like this.\n'
    assert uses(text) == []
    # Inside a component file nothing is markdown, so a tag in what looks like code still counts.
    assert [u.src for u in uses('<pre>`<component src="X.html"></component>`</pre>', markdown=False)] == ['X.html']


@pytest.mark.parametrize(
    ('text', 'message'),
    [
        ('<component src="X.html"/>', r'main\.md:1: self-closing <component \.\.\./>'),
        ('a\n</component>', r'main\.md:2: </component> without an opening'),
        ('<component src="X.html">\n\nbody\n', r'main\.md:1: <component src="X\.html"> is never closed'),
        ('<component\n  src="X.html"></component>', r'main\.md:1: the opening <component> tag must be on one line'),
        ('<component title="x"></component>', r'main\.md:1: <component> needs a src attribute'),
        ('<component src="X.html" fontSize="1"></component>', r"attribute 'fontSize' on <component> must match"),
        ('<component src="X.html" a="1" a="2"></component>', r"attribute 'a' given twice"),
        (
            '<component src="X.html" t="{{ PAGE_NUMBER }}"></component>',
            r'in an attribute of <component> is not substituted',
        ),
        ('<component src="X.html" t="unterminated></component>', r'main\.md:1: cannot parse this <component> tag'),
        (
            '<component src="X.html">\nbody\n\n</component>',
            r'main\.md:1: put a blank line after <component src="X\.html">',
        ),
        ('<component src="X.html">\n\nbody\n</component>', r'main\.md:4: put a blank line before </component>'),
    ],
)
def test_find_component_uses_errors(text: str, message: str):
    with pytest.raises(BuildError, match=message):
        uses(text)


# --- parameters --------------------------------------------------------------


def test_parse_params():
    params, body = build.parse_params(
        '<!-- params: title, icon="x", href=\'a, b\', flag="" -->\n<div>{{ title }}</div>\n', MD
    )
    assert params == {'title': None, 'icon': 'x', 'href': 'a, b', 'flag': ''}
    assert body == '<div>{{ title }}</div>\n'
    assert build.parse_params('<div>plain</div>', MD) == ({}, '<div>plain</div>')


@pytest.mark.parametrize(
    ('decl', 'message'),
    [
        ('title, Title', r"parameter 'Title' must match"),
        ('font-size', r"parameter 'font-size' must match"),
        ('src', r"'src' is reserved"),
        ('title, title', r"parameter 'title' is declared twice"),
        ('title icon', r'cannot parse <!-- params: \.\.\. -->'),
        ('title="unterminated', r'cannot parse <!-- params: \.\.\. -->'),
    ],
)
def test_parse_params_errors(decl: str, message: str):
    with pytest.raises(BuildError, match=message):
        build.parse_params(f'<!-- params: {decl} -->\n', Path('components/Card.html'))


def card_dir(
    tmp_path: Path,
    card: str = '<!-- params: title, icon="*" -->\n<div class="card"><b>{{ icon }} {{ title }}</b>\n{{ CONTENT }}</div>\n',
) -> Path:
    comps = tmp_path / 'components'
    write(comps / 'Card.html', card)
    return comps


def test_component_parameters_pass_checks(tmp_path: Path):
    comps = card_dir(tmp_path)
    body = '<component src="Card.html" title="Fast"></component>\n\n<component src="Card.html" title="x" icon="y">\n\ntext\n\n</component>\n'
    assert set(collect(body, comps)) == {'Card.html'}


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        (
            '<component src="Card.html"></component>',
            r"<component src=\"Card\.html\"> is missing required parameter 'title'",
        ),
        (
            '<component src="Card.html" title="x" colour="red"></component>',
            r"has no parameter 'colour'; declared: title, icon='\*'",
        ),
    ],
)
def test_component_use_errors(tmp_path: Path, body: str, message: str):
    comps = card_dir(tmp_path)
    with pytest.raises(BuildError, match=message):
        collect(body, comps)


def test_component_children_need_a_content_slot(tmp_path: Path):
    comps = card_dir(tmp_path, '<!-- params: title -->\n<b>{{ title }}</b>\n')
    with pytest.raises(
        BuildError,
        match=r'main\.md:1: <component src="Card\.html"> has children but Card\.html has no \{\{ CONTENT \}\}',
    ):
        collect('<component src="Card.html" title="x">\n\ntext\n\n</component>', comps)
    # The other way round is fine: an empty tag just renders nothing where CONTENT is.
    comps = card_dir(tmp_path)
    assert collect('<component src="Card.html" title="x"></component>', comps)


@pytest.mark.parametrize(
    ('card', 'message'),
    [
        ('<b>{{ title }}</b>', r'Card\.html:1: unknown placeholder \{\{ title \}\}; declared parameters: \(none\)'),
        (
            '<!-- params: title -->\n<b>{{ title }} {{ Title }}</b>',
            r'Card\.html:2: unknown placeholder \{\{ Title \}\}',
        ),
        (
            '<!-- params: title -->\n<b>{{ title }} {{ AUTHOR }}</b>',
            r'Card\.html:2: unknown placeholder \{\{ AUTHOR \}\}; .*built-ins and \[context\]: CONTENT, PAGE_COUNT',
        ),
        (
            '<!-- params: title, icon -->\n<b>{{ title }}</b>',
            r"Card\.html:1: parameter 'icon' is declared but never used",
        ),
        (
            '<!-- params: title -->\n{{ title }}{{ CONTENT }}\n{{ CONTENT }}',
            r'Card\.html: \{\{ CONTENT \}\} may appear only once',
        ),
        (
            '<!-- params: title -->\n<b>{{ title }}</b>\n<i data-x="{{ CONTENT }}"></i>',
            r'Card\.html:3: \{\{ CONTENT \}\} is markup and cannot be used inside an attribute',
        ),
    ],
)
def test_component_file_errors(tmp_path: Path, card: str, message: str):
    comps = card_dir(tmp_path, card)
    with pytest.raises(BuildError, match=message):
        collect('<component src="Card.html" title="x"></component>', comps)


def test_context_names_are_valid_everywhere(tmp_path: Path):
    comps = card_dir(tmp_path, '<!-- params: title -->\n<b>{{ title }} by {{ AUTHOR }} on {{ PAGE_NUMBER }}</b>\n')
    names = NAMES | {'AUTHOR'}
    assert collect('<component src="Card.html" title="x"></component>', comps, names)
    build.check_body_placeholders('Written by {{ AUTHOR }}, page {{ PAGE_NUMBER }}; `{{ NOPE }}` is code', MD, names)
    with pytest.raises(BuildError, match=r'main\.md:1: unknown placeholder \{\{ NOPE \}\}'):
        build.check_body_placeholders('{{ NOPE }}', MD, names)
    with pytest.raises(BuildError, match=r'main\.md:2: \{\{ CONTENT \}\} is only available inside a component'):
        build.check_body_placeholders('ok\n{{ CONTENT }}', MD, names)
    # Lowercase placeholders in prose are text, as are mixed-case ones that do not start with a capital.
    build.check_body_placeholders('a {{ jinja_var }} and {{ camelCase }}', MD, names)


def test_load_context(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi {{ DATE }} {{ N }} {{ FLAG }}\n')
    write(tmp_path / 'artifact.toml', '[context]\nDATE = "April 2026"\nN = 3\nFLAG = true\n')
    cfg = build.load_config(tmp_path)
    assert cfg.context == {'DATE': 'April 2026', 'N': '3', 'FLAG': 'true'}
    assert cfg.to_json()['context'] == cfg.context
    assert build.build_html(tmp_path).is_file()
    for toml, message in (
        ('[context]\nauthor = "x"\n', r"\[context\] key 'author' must be uppercase"),
        ('[context]\nPAGE_COUNT = "x"\n', r"\[context\] key 'PAGE_COUNT' is a built-in"),
        ('[context]\nDATE = ["x"]\n', r"\[context\] value for 'DATE' must be a string, number or boolean"),
        ('context = "x"\n', r'\[context\] must be a table'),
    ):
        write(tmp_path / 'artifact.toml', toml)
        with pytest.raises(BuildError, match=message):
            build.load_config(tmp_path)


# --- images ----------------------------------------------------------------


def test_check_images_normalises_paths_and_skips_external(tmp_path: Path):
    write(tmp_path / 'assets' / 'a.png', 'png')
    write(tmp_path / 'assets' / 'b.svg', '<svg/>')
    texts = [
        '![alt](./assets/a.png) and <img src="assets//b.svg">',
        'background: url("assets/../assets/a.png"); <img src="https://x/y.png"> <img src="/abs.png">',
    ]
    assert build.check_images(texts, tmp_path) == ['assets/a.png', 'assets/b.svg']


def test_check_images_missing_file(tmp_path: Path):
    with pytest.raises(BuildError, match='image not found'):
        build.check_images(['![x](assets/missing.png)'], tmp_path)


def test_check_images_rejects_escape(tmp_path: Path):
    write(tmp_path.parent / 'outside.png', 'png')
    with pytest.raises(BuildError, match='escapes the deck directory'):
        build.check_images(['![x](../outside.png)'], tmp_path)


# --- page ------------------------------------------------------------------


def block_of(page: str, selector: str) -> str:
    """The raw content of one data block in a built page, as the runtime would read it before decoding."""
    match = re.search(rf'<script[^>]*{re.escape(selector)}[^>]*>\n?(.*?)</script>', page, re.DOTALL)
    assert match is not None, selector
    return match.group(1)


def config_of(page: str) -> dict[str, object]:
    return json.loads(block_of(page, 'id="artifact-config"'))


# Sequences that would end a data block early or change how `</script>` is tokenized.
BLOCK_BREAKERS = re.compile(r'</?script|<!--', re.IGNORECASE)


@pytest.mark.parametrize(
    'text',
    [
        '</script><!--<script>',
        'a </SCRIPT> b <Script> c',
        'R&D &amp; &lt; &amp;lt; &lt;script &nbsp;',
        '<!-- params: title -->\n<div>{{ title }}</div>',
        'plain <b>html</b> and <br>',
    ],
)
def test_block_codec_round_trips(text: str):
    encoded = build.encode_block(text)
    assert build.decode_block(encoded) == text
    assert not BLOCK_BREAKERS.search(encoded)


def test_block_codec_leaves_ordinary_text_alone():
    assert build.encode_block('R&D, a < b, &nbsp; and <div>') == 'R&D, a < b, &nbsp; and <div>'


def test_render_page_writes_readable_blocks():
    data: dict[str, object] = {
        'config': {'type': 'deck', 'title': '</script>'},
        'markdown': '# Hi\n\n</script><!--<script>\n',
        'components': {'Hero.html': '<!-- params: x -->\n<b>{{ x }}</b>\n'},
        'styles': ':root { --accent: red }\n',
    }
    page = build.render_page('T & T', None, data, '/openartifact.js', 'main.md')
    assert config_of(page) == data['config']
    assert '\\u003c/script>' in block_of(page, 'id="artifact-config"')
    assert build.decode_block(block_of(page, 'id="artifact-markdown"')) == data['markdown']
    assert build.decode_block(block_of(page, 'data-component="Hero.html"')) == '<!-- params: x -->\n<b>{{ x }}</b>\n'
    # The markdown reads as markdown in the page source: only the breaker sequences are touched.
    assert '# Hi\n\n&lt;/script>&lt;!--&lt;script>' in page
    assert '<style id="artifact-styles">\n:root { --accent: red }\n</style>' in page
    assert '<link rel="alternate" type="text/markdown" href="main.md">' in page
    assert '<title>T &amp; T</title>' in page
    assert '<script src="/openartifact.js"></script>' in page
    assert '<link rel="icon"' not in page
    assert 'id="artifact-data"' not in page


def test_render_page_links_favicon_and_runtime_relatively():
    page = build.render_page('T', 'assets/fav.svg', {}, 'https://cdn.example/openartifact.js?v="1"', 'docs/x.md')
    assert '<link rel="icon" href="assets/fav.svg">' in page
    assert '<link rel="alternate" type="text/markdown" href="docs/x.md">' in page
    assert '<script src="https://cdn.example/openartifact.js?v=&quot;1&quot;"></script>' in page


def test_styles_cannot_close_the_style_element(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'styles.css', ':root {}\n</STYLE><script>alert(1)</script>\n')
    with pytest.raises(BuildError, match=r"styles\.css:2: '</style' cannot appear"):
        build.build_html(tmp_path)


# --- end to end ------------------------------------------------------------


def test_build_starter_example(tmp_path: Path):
    out = build.build_html(ROOT / 'examples' / 'starter', tmp_path / 'out' / 'index.html')
    assert out.is_file()
    assert sorted(p.name for p in out.parent.iterdir()) == ['index.html']
    page = out.read_text(encoding='utf-8')
    assert '<script src="/openartifact.js"></script>' in page
    config = config_of(page)
    assert config['type'] == 'deck'
    assert config['theme'] == 'markdown-dark'
    starter = ROOT / 'examples' / 'starter'
    assert build.decode_block(block_of(page, 'id="artifact-markdown"')) == (starter / 'main.md').read_text()
    for name in ('Hero.html', 'Callout.html'):
        component = build.decode_block(block_of(page, f'data-component="{name}"'))
        assert component == (starter / 'components' / name).read_text()
    assert (starter / 'styles.css').read_text() in page
    assert '<link rel="alternate" type="text/markdown" href="main.md">' in page
    assert 'data:' not in page


def test_build_uses_given_runtime_url(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    page = build.build_html(tmp_path, runtime_url='/static/openartifact.js').read_text()
    assert '<script src="/static/openartifact.js"></script>' in page


def test_load_config_favicon(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'assets' / 'fav.svg', '<svg/>')
    write(tmp_path / 'artifact.toml', 'favicon = "assets/fav.svg"\n')
    assert build.load_config(tmp_path).favicon == 'assets/fav.svg'
    write(tmp_path / 'artifact.toml', 'favicon = "https://x/fav.svg"\n')
    with pytest.raises(BuildError, match='must be a relative path'):
        build.load_config(tmp_path)
    write(tmp_path / 'artifact.toml', 'favicon = "assets/missing.svg"\n')
    with pytest.raises(BuildError, match='favicon not found'):
        build.load_config(tmp_path)


# --- artifact types --------------------------------------------------------


@pytest.mark.parametrize('name', ['document', 'page'])
def test_build_prose_examples(tmp_path: Path, name: str):
    out = build.build_html(ROOT / 'examples' / name, tmp_path / 'index.html')
    page = out.read_text()
    assert config_of(page)['type'] == name
    markdown = build.decode_block(block_of(page, 'id="artifact-markdown"'))
    assert markdown == (ROOT / 'examples' / name / 'main.md').read_text()


def test_type_defaults_to_deck(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    assert build.load_config(tmp_path).type == 'deck'


def test_load_config_rejects_bad_type(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'type = "scroll"\n')
    with pytest.raises(BuildError, match='invalid type'):
        build.load_config(tmp_path)


@pytest.mark.parametrize('toml', ['footer = "x"\n', 'tabs = [{ id = "a", label = "A" }]\n'])
def test_footer_and_tabs_are_gone(tmp_path: Path, toml: str):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', toml)
    with pytest.raises(BuildError, match='is no longer supported; render it from a `page_component`'):
        build.load_config(tmp_path)


# --- page component ------------------------------------------------------------


def page_component_dir(tmp_path: Path, frame: str, toml: str = '') -> Path:
    write(tmp_path / 'main.md', '# Hi\n\nPage {{ PAGE_NUMBER }}.\n')
    write(tmp_path / 'artifact.toml', f'page_component = "Page.html"\n{toml}')
    write(tmp_path / 'components' / 'Page.html', frame)
    return tmp_path


def test_page_component_is_collected_and_checked(tmp_path: Path):
    frame = (
        '<!-- params: brand="Acme" -->\n<header>{{ brand }} - {{ PAGE_TITLE }} - {{ PAGE_NUMBER }}/{{ PAGE_COUNT }}'
        '<component src="Logo.html"></component></header>\n{{ CONTENT }}\n'
    )
    page_component_dir(tmp_path, frame)
    write(tmp_path / 'components' / 'Logo.html', '<b>logo</b>')
    page = build.build_html(tmp_path).read_text()
    assert config_of(page)['page_component'] == 'Page.html'
    assert build.decode_block(block_of(page, 'data-component="Page.html"')) == frame
    assert 'data-component="Logo.html"' in page


@pytest.mark.parametrize(
    ('frame', 'message'),
    [
        ('<header></header>', r'Page\.html: a page component must contain exactly one \{\{ CONTENT \}\}, found 0'),
        ('{{ CONTENT }}{{ CONTENT }}', r'may appear only once'),
        (
            '<!-- params: brand -->\n{{ brand }} {{ CONTENT }}',
            r"rendered without attributes, so parameter 'brand' needs a default",
        ),
        ('{{ nope }} {{ CONTENT }}', r'unknown placeholder \{\{ nope \}\}'),
    ],
)
def test_page_component_errors(tmp_path: Path, frame: str, message: str):
    page_component_dir(tmp_path, frame)
    with pytest.raises(BuildError, match=message):
        build.build_html(tmp_path)


def test_page_component_must_be_html(tmp_path: Path):
    page_component_dir(tmp_path, '{{ CONTENT }}', toml='')
    write(tmp_path / 'artifact.toml', 'page_component = "Page.svg"\n')
    with pytest.raises(BuildError, match=r'`page_component` must be an \.html file'):
        build.load_config(tmp_path)
    write(tmp_path / 'artifact.toml', 'page_component = "Missing.html"\n')
    with pytest.raises(BuildError, match='component not found'):
        build.build_html(tmp_path)


def test_build_document_with_pages(tmp_path: Path):
    write(tmp_path / 'main.md', '# Report\n\nBody text.\n\n---\n\n## Appendix\n')
    write(tmp_path / 'artifact.toml', 'type = "document"\n')
    page = build.build_html(tmp_path).read_text()
    assert config_of(page)['type'] == 'document'
    # The same markdown is a two-page deck: pages are an artifact concept, the type only lays them out.
    write(tmp_path / 'artifact.toml', 'type = "deck"\n')
    assert build.build_html(tmp_path).is_file()


def test_load_config_rejects_bad_theme(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'theme = "neon"\n')
    with pytest.raises(BuildError, match='invalid theme'):
        build.load_config(tmp_path)


def test_load_config_rejects_dropped_keys(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'code_light_theme = "github-light"\n')
    with pytest.raises(BuildError, match='no longer supported'):
        build.load_config(tmp_path)
