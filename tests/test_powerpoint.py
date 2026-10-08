"""Tests for `backend/powerpoint.py`: the scene to slides mapping, read back with python-pptx. No Postgres, no Chrome."""

# python-pptx types its shapes as the base class, so reading a text box back is untyped from there down.
# pyright: basic

from __future__ import annotations

import io

import powerpoint
import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt


def png(width: int = 4, height: int = 2) -> bytes:
    """A real PNG, since python-pptx reads the image header when it adds one."""
    out = io.BytesIO()
    Image.new('RGB', (width, height), (9, 34, 36)).save(out, 'PNG')
    return out.getvalue()


def run(text: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        'text': text,
        'font': 'Segoe UI',
        'size': 18.4,
        'bold': False,
        'italic': False,
        'underline': False,
        'strike': False,
        'color': '#fbffea',
        'spacing': 0,
    }
    return {**base, **overrides}


SCENE = {
    'width': 1055.9,
    'height': 594.1,
    'pages': [
        {
            'index': 1,
            'blocks': [
                {
                    'x': 81,
                    'y': 83,
                    'w': 929,
                    'h': 52,
                    'align': 'left',
                    'lineHeight': 51.52,
                    'runs': [run('Hello, ', font='sans-serif', size=44.8, bold=True), run('page', italic=True)],
                },
                {
                    'x': 285,
                    'y': 278,
                    'w': 485,
                    'h': 64,
                    'align': 'center',
                    'lineHeight': 62.56,
                    'runs': [
                        run('One ', underline=True, href='https://example.com/'),
                        run('\n'),
                        run('two', strike=True, spacing=-1),
                    ],
                },
            ],
        },
        {
            'index': 2,
            'blocks': [
                {
                    'x': 10,
                    'y': 10,
                    'w': 100,
                    'h': 20,
                    'align': 'right',
                    'lineHeight': 20,
                    'runs': [run('code', font='Menlo')],
                }
            ],
        },
    ],
}


def test_build_pptx_lays_text_over_each_slides_picture():
    scene = powerpoint.Scene.model_validate(SCENE)
    data = powerpoint.build_pptx(scene, [png(), png(8, 4)], 'Starter')
    prs = Presentation(io.BytesIO(data))
    # The slide is the CSS size at 96 px to the inch, so CSS sizes come out as the same number of points.
    assert (prs.slide_width, prs.slide_height) == (Emu(round(1055.9 * 9525)), Emu(round(594.1 * 9525)))
    assert prs.core_properties.title == 'Starter'
    assert len(prs.slides) == 2
    first, second = prs.slides
    # Each slide's picture is its background fill, not a shape that could be selected or moved.
    for slide in (first, second):
        background = slide.element.cSld[0]
        assert background.tag == qn('p:bg')
        blip = background.find(f'.//{qn("a:blip")}')
        assert blip is not None and slide.part.related_part(blip.get(qn('r:embed'))).blob.startswith(b'\x89PNG')
    assert [shape.name for shape in first.shapes] == ['Text 1', 'Text 2']

    heading, statement = first.shapes
    # The box is where the browser put it, widened a little to the right for PowerPoint's own wrapping.
    assert (heading.left, heading.top, heading.height) == (
        Emu(81 * 9525),
        Emu(83 * 9525),
        Emu(52 * 9525),
    )
    assert heading.width == Emu(round(929 * 1.03 * 9525))
    frame = heading.text_frame  # pyright: ignore
    assert frame.word_wrap and frame.margin_left == 0 and frame.margin_top == 0
    [paragraph] = frame.paragraphs
    assert paragraph.alignment == PP_ALIGN.LEFT and paragraph.line_spacing == Pt(51.52 * 0.75)
    hello, page = paragraph.runs
    assert (hello.text, hello.font.name, hello.font.bold) == ('Hello, ', 'Calibri', True)
    # A size is kept in hundredths of a point.
    assert hello.font.size is not None and hello.font.size.pt == pytest.approx(44.8 * 0.75, abs=0.01)
    assert str(hello.font.color.rgb) == 'FBFFEA'
    assert (page.text, page.font.name, page.font.italic, page.font.bold) == ('page', 'Segoe UI', True, False)

    # Centred text keeps its centre: the slack is split either side. A newline run is a line break, not a paragraph.
    assert statement.left == Emu(round((285 - 485 * 0.015) * 9525))
    [paragraph] = statement.text_frame.paragraphs  # pyright: ignore
    assert paragraph.alignment == PP_ALIGN.CENTER
    one, two = paragraph.runs
    assert one.text == 'One ' and one.font.underline and one.hyperlink.address == 'https://example.com/'
    assert paragraph._p.findall(qn('a:br')) != []
    properties = two._r.get_or_add_rPr()
    assert two.text == 'two' and properties.get('strike') == 'sngStrike' and properties.get('spc') == '-75'

    [code] = second.shapes
    [paragraph] = code.text_frame.paragraphs  # pyright: ignore
    assert paragraph.alignment == PP_ALIGN.RIGHT and paragraph.runs[0].font.name == 'Consolas'
    assert code.left == Emu(round((10 - 100 * 0.03) * 9525))


def test_font_name():
    assert powerpoint.font_name('Inter') == 'Inter'
    assert powerpoint.font_name('monospace') == 'Consolas'
    assert powerpoint.font_name('SFMono-Regular') == 'Consolas'
    assert powerpoint.font_name('serif') == 'Georgia'
