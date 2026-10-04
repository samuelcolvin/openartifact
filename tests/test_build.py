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


# --- slide validation ------------------------------------------------------


def test_validate_slides_counts_markers():
    assert build.validate_slides('<slide/>\n# one\n<slide layout="title">\n# two\n', Path('main.md')) == 2


def test_validate_slides_ignores_markers_in_fences():
    source = '<slide/>\n```html\n<slide/>\n```\n~~~\n<slide/>\n~~~\n'
    assert build.validate_slides(source, Path('main.md')) == 1


def test_validate_slides_rejects_preamble():
    with pytest.raises(BuildError, match=r'main\.md:1: content before the first'):
        build.validate_slides('# hello\n<slide/>\n', Path('main.md'))


def test_validate_slides_rejects_empty():
    with pytest.raises(BuildError, match='no slides found'):
        build.validate_slides('\n\n', Path('main.md'))


def test_validate_slides_rejects_self_closing_component():
    with pytest.raises(BuildError, match=r'main\.md:2: self-closing'):
        build.validate_slides('<slide/>\n<component src="X.html"/>\n', Path('main.md'))


# --- components ------------------------------------------------------------


def test_collect_components_follows_nesting(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'A.html', '<div>a<component src="B.html"></component></div>')
    write(comps / 'B.html', '<p>b</p>')
    found = build.collect_components('<component src="A.html"></component>', comps)
    assert set(found) == {'A.html', 'B.html'}
    assert found['B.html'] == '<p>b</p>'


def test_collect_components_detects_cycle(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'A.html', '<component src="B.html"></component>')
    write(comps / 'B.html', '<component src="A.html"></component>')
    with pytest.raises(BuildError, match=re.escape('component cycle: A.html -> B.html -> A.html')):
        build.collect_components('<component src="A.html"></component>', comps)


def test_svg_component_is_inlined_without_prolog(tmp_path: Path):
    comps = tmp_path / 'components'
    write(
        comps / 'Arrow.svg',
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0L10 10" stroke="var(--accent)"/></svg>\n',
    )
    found = build.collect_components('<component src="Arrow.svg"></component>', comps)
    assert found['Arrow.svg'].startswith('<svg xmlns=')
    assert 'var(--accent)' in found['Arrow.svg']


def test_svg_component_must_be_svg(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'Nope.svg', '<div>not svg</div>')
    with pytest.raises(BuildError, match='expected an <svg> root element'):
        build.collect_components('<component src="Nope.svg"></component>', comps)


def test_component_extension_is_checked(tmp_path: Path):
    comps = tmp_path / 'components'
    write(comps / 'Card.txt', '<div/>')
    with pytest.raises(BuildError, match=r'must be one of \.html, \.svg'):
        build.collect_components('<component src="Card.txt"></component>', comps)


def test_svg_component_reference_is_not_an_image(tmp_path: Path):
    assert build.check_images(['<component src="Arrow.svg"></component>'], tmp_path) == []


def test_collect_components_rejects_escape(tmp_path: Path):
    comps = tmp_path / 'components'
    comps.mkdir()
    write(tmp_path / 'secret.html', 'x')
    with pytest.raises(BuildError, match='escapes'):
        build.collect_components('<component src="../secret.html"></component>', comps)


def test_collect_components_missing(tmp_path: Path):
    comps = tmp_path / 'components'
    comps.mkdir()
    with pytest.raises(BuildError, match='component not found'):
        build.collect_components('<component src="Nope.html"></component>', comps)


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
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
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
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
    page = build.build_html(tmp_path, runtime_url='/static/openartifact.js').read_text()
    assert '<script src="/static/openartifact.js"></script>' in page


def test_load_config_favicon(tmp_path: Path):
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
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
    # The examples talk about slide markers in prose, but contain none.
    markdown = build.decode_block(block_of(page, 'id="artifact-markdown"'))
    assert not any(build.SLIDE_RE.match(line) for line in markdown.splitlines())


def test_type_defaults_to_deck(tmp_path: Path):
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
    assert build.load_config(tmp_path).type == 'deck'


def test_load_config_rejects_bad_type(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'type = "scroll"\n')
    with pytest.raises(BuildError, match='invalid type'):
        build.load_config(tmp_path)


def test_tabs_are_deck_only(tmp_path: Path):
    write(tmp_path / 'main.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'type = "page"\ntabs = [{ id = "a", label = "A" }]\n')
    with pytest.raises(BuildError, match='`tabs` are only used when type = "deck"'):
        build.load_config(tmp_path)


def test_prose_rejects_slide_markers():
    with pytest.raises(BuildError, match=r'main\.md:3: <slide .../> markers are only used when type = "deck"'):
        build.validate_prose('# Title\n\n<slide/>\n', Path('main.md'), 'document')


def test_prose_allows_markers_in_fences_and_rejects_self_closing_components():
    build.validate_prose('# Title\n```md\n<slide/>\n```\n', Path('main.md'), 'page')
    with pytest.raises(BuildError, match='self-closing'):
        build.validate_prose('# Title\n<component src="X.html"/>\n', Path('main.md'), 'page')
    with pytest.raises(BuildError, match='no content'):
        build.validate_prose('\n\n', Path('main.md'), 'page')


def test_build_document_from_plain_markdown(tmp_path: Path):
    write(tmp_path / 'main.md', '# Report\n\nBody text.\n')
    write(tmp_path / 'artifact.toml', 'type = "document"\n')
    page = build.build_html(tmp_path).read_text()
    assert config_of(page)['type'] == 'document'
    # A deck with the same markdown fails: the marker rule is per type.
    write(tmp_path / 'artifact.toml', 'type = "deck"\n')
    with pytest.raises(BuildError, match='content before the first'):
        build.build_html(tmp_path)


def test_load_config_rejects_bad_theme(tmp_path: Path):
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
    write(tmp_path / 'artifact.toml', 'theme = "neon"\n')
    with pytest.raises(BuildError, match='invalid theme'):
        build.load_config(tmp_path)


def test_load_config_rejects_dropped_keys(tmp_path: Path):
    write(tmp_path / 'main.md', '<slide/>\n# hi\n')
    write(tmp_path / 'artifact.toml', 'code_light_theme = "github-light"\n')
    with pytest.raises(BuildError, match='no longer supported'):
        build.load_config(tmp_path)
