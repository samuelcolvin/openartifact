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
    assert build.validate_slides('<slide/>\n# one\n<slide layout="title">\n# two\n', Path('deck.md')) == 2


def test_validate_slides_ignores_markers_in_fences():
    source = '<slide/>\n```html\n<slide/>\n```\n~~~\n<slide/>\n~~~\n'
    assert build.validate_slides(source, Path('deck.md')) == 1


def test_validate_slides_rejects_preamble():
    with pytest.raises(BuildError, match=r'deck\.md:1: content before the first'):
        build.validate_slides('# hello\n<slide/>\n', Path('deck.md'))


def test_validate_slides_rejects_empty():
    with pytest.raises(BuildError, match='no slides found'):
        build.validate_slides('\n\n', Path('deck.md'))


def test_validate_slides_rejects_self_closing_component():
    with pytest.raises(BuildError, match=r'deck\.md:2: self-closing'):
        build.validate_slides('<slide/>\n<component src="X.html"/>\n', Path('deck.md'))


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


def blob_of(page: str) -> str:
    """The raw text of the JSON blob embedded in a built page."""
    match = re.search(r'id="artifact-data">(.*?)</script>', page, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_render_page_escapes_script_breakers():
    data: dict[str, object] = {'markdown': '</script><!--<script>', 'components': {}}
    page = build.render_page('T & T', None, data, '/openartifact.js')
    blob = blob_of(page)
    assert '<' not in blob
    assert json.loads(blob) == data
    assert '<title>T &amp; T</title>' in page
    assert '<script src="/openartifact.js"></script>' in page
    assert '<link rel="icon"' not in page


def test_render_page_links_favicon_and_runtime_relatively():
    page = build.render_page('T', 'assets/fav.svg', {}, 'https://cdn.example/openartifact.js?v="1"')
    assert '<link rel="icon" href="assets/fav.svg">' in page
    assert '<script src="https://cdn.example/openartifact.js?v=&quot;1&quot;"></script>' in page


# --- end to end ------------------------------------------------------------


def test_build_starter_example(tmp_path: Path):
    out = build.build_html(ROOT / 'examples' / 'starter', tmp_path / 'out' / 'index.html')
    assert out.is_file()
    assert sorted(p.name for p in out.parent.iterdir()) == ['index.html']
    page = out.read_text(encoding='utf-8')
    assert '<script src="/openartifact.js"></script>' in page
    blob = json.loads(blob_of(page))
    assert set(blob) == {'config', 'markdown', 'components', 'styles'}
    assert blob['config']['type'] == 'deck'
    assert blob['config']['theme'] == 'markdown-dark'
    assert 'Hero.html' in blob['components'] and 'Callout.html' in blob['components']
    assert 'data:' not in page
    assert '<slide' in blob['markdown']


def test_build_uses_given_runtime_url(tmp_path: Path):
    write(tmp_path / 'deck.md', '<slide/>\n# hi\n')
    page = build.build_html(tmp_path, runtime_url='/static/openartifact.js').read_text()
    assert '<script src="/static/openartifact.js"></script>' in page


def test_load_config_favicon(tmp_path: Path):
    write(tmp_path / 'deck.md', '<slide/>\n# hi\n')
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
    blob = json.loads(blob_of(out.read_text()))
    assert blob['config']['type'] == name
    # The examples talk about slide markers in prose, but contain none.
    assert not any(build.SLIDE_RE.match(line) for line in blob['markdown'].splitlines())


def test_type_defaults_to_deck(tmp_path: Path):
    write(tmp_path / 'deck.md', '<slide/>\n# hi\n')
    assert build.load_config(tmp_path).type == 'deck'


def test_load_config_rejects_bad_type(tmp_path: Path):
    write(tmp_path / 'deck.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'type = "scroll"\n')
    with pytest.raises(BuildError, match='invalid type'):
        build.load_config(tmp_path)


def test_tabs_are_deck_only(tmp_path: Path):
    write(tmp_path / 'deck.md', '# hi\n')
    write(tmp_path / 'artifact.toml', 'type = "page"\ntabs = [{ id = "a", label = "A" }]\n')
    with pytest.raises(BuildError, match='`tabs` are only used when type = "deck"'):
        build.load_config(tmp_path)


def test_prose_rejects_slide_markers():
    with pytest.raises(BuildError, match=r'deck\.md:3: <slide .../> markers are only used when type = "deck"'):
        build.validate_prose('# Title\n\n<slide/>\n', Path('deck.md'), 'document')


def test_prose_allows_markers_in_fences_and_rejects_self_closing_components():
    build.validate_prose('# Title\n```md\n<slide/>\n```\n', Path('deck.md'), 'page')
    with pytest.raises(BuildError, match='self-closing'):
        build.validate_prose('# Title\n<component src="X.html"/>\n', Path('deck.md'), 'page')
    with pytest.raises(BuildError, match='no content'):
        build.validate_prose('\n\n', Path('deck.md'), 'page')


def test_build_document_from_plain_markdown(tmp_path: Path):
    write(tmp_path / 'deck.md', '# Report\n\nBody text.\n')
    write(tmp_path / 'artifact.toml', 'type = "document"\n')
    page = build.build_html(tmp_path).read_text()
    assert json.loads(blob_of(page))['config']['type'] == 'document'
    # A deck with the same markdown fails: the marker rule is per type.
    write(tmp_path / 'artifact.toml', 'type = "deck"\n')
    with pytest.raises(BuildError, match='content before the first'):
        build.build_html(tmp_path)


def test_load_config_rejects_bad_theme(tmp_path: Path):
    write(tmp_path / 'deck.md', '<slide/>\n# hi\n')
    write(tmp_path / 'artifact.toml', 'theme = "neon"\n')
    with pytest.raises(BuildError, match='invalid theme'):
        build.load_config(tmp_path)


def test_load_config_rejects_dropped_keys(tmp_path: Path):
    write(tmp_path / 'deck.md', '<slide/>\n# hi\n')
    write(tmp_path / 'artifact.toml', 'code_light_theme = "github-light"\n')
    with pytest.raises(BuildError, match='no longer supported'):
        build.load_config(tmp_path)
