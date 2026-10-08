"""The Word export: a document artifact as a `.docx` of ordinary Word paragraphs, built from the outline the page
rendered.

A document's text flows, so the export is structure rather than geometry (compare `powerpoint.py`): the runtime's
outline mode (`frontend/src/outline.ts`, a document opened with `?scene`) writes the pages as headings,
paragraphs, lists, code blocks, quotes, tables, images and rules into the page, the chrome service reads it out
of the DOM (`POST /scene/`), and python-docx turns each block into its Word counterpart with the built-in styles
(Heading 1 to 6, List Bullet and List Number at three levels, Quote, Table Grid), one page break between the
artifact's pages. Nothing of the theme is carried over: no page or text colour, Word's defaults throughout, so
the file is a plain document to go on editing. Images are read from the artifact's directory, except SVGs, which
Word cannot show: the runtime numbers each (inline or an `<img>` of a `.svg` file) and, opened with `&svg=N`,
shows that one alone at its rendered size, which the chrome service photographs at 2x (`svg_pictures`). Every
image is sized in Word as it was on the page.
"""

# python-docx's XML layer (numbering, borders, hyperlinks) is untyped, and this module leans on it.
# pyright: basic

from __future__ import annotations

import asyncio
import io
import math
from pathlib import Path
from typing import Literal

import render
from docx import Document
from docx.document import Document as DocumentType
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph
from docx.text.run import Run as RunType
from PIL import Image as PilImage
from pydantic import BaseModel

import workspace

MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
# Word's list styles go three levels deep; a deeper list stays at the third.
LIST_LEVELS = 3
CODE_FONT = 'Consolas'
CODE_SIZE = Pt(9.5)
# Images are shown at their rendered width, 96 CSS pixels to the inch, capped at the text width of the default
# template (Letter, one-inch margins).
IMAGE_WIDTH = Inches(6)
PX_PER_INCH = 96
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff'}
# The device pixel ratio an SVG is photographed at, and how many are photographed at once.
PICTURE_SCALE = 2
CONCURRENCY = 4
# The chrome service's smallest window; a smaller SVG is cut out of one that size.
MIN_WINDOW = 200


class Run(BaseModel):
    """A stretch of text in one style; `text` of a newline alone is a line break. Mirrors `OutlineRun` in outline.ts."""

    text: str
    bold: bool
    italic: bool
    code: bool
    underline: bool
    strike: bool
    href: str | None = None


class Item(BaseModel):
    runs: list[Run]
    blocks: list[Block]


class Cell(BaseModel):
    runs: list[Run]
    header: bool


class Heading(BaseModel):
    type: Literal['heading']
    level: int
    runs: list[Run]


class ParagraphBlock(BaseModel):
    type: Literal['paragraph']
    runs: list[Run]


class ListBlock(BaseModel):
    type: Literal['list']
    ordered: bool
    items: list[Item]


class Code(BaseModel):
    type: Literal['code']
    text: str


class Quote(BaseModel):
    type: Literal['quote']
    blocks: list[Block]


class Table(BaseModel):
    type: Literal['table']
    rows: list[list[Cell]]


class Image(BaseModel):
    """An image, by its `src` as written, with its rendered size in CSS pixels; `svg` numbers an SVG for the runtime
    to show alone when the page is opened with `&svg=N`."""

    type: Literal['image']
    src: str
    alt: str
    width: float = 0
    height: float = 0
    svg: int | None = None

    @property
    def key(self) -> str:
        """What the image's bytes are filed under: the SVG number for one the chrome service renders, else the path."""
        return f'svg:{self.svg}' if self.svg else self.src


class Rule(BaseModel):
    type: Literal['rule']


Block = Heading | ParagraphBlock | ListBlock | Code | Quote | Table | Image | Rule


class Page(BaseModel):
    index: int
    blocks: list[Block]


class Outline(BaseModel):
    """What the runtime wrote: the pages of the document as blocks. Mirrors `Outline` in outline.ts."""

    kind: Literal['document']
    pages: list[Page]


async def export(found: workspace.Artifact) -> bytes:
    """The document as a `.docx`: its outline from the chrome service, its images from the checkout, assembled in a thread.

    The page is built first, so a broken artifact fails as a build error; the chrome service's failures are
    `render.RenderError`s.
    """
    async with workspace.open_artifact(found) as directory:
        await render.ensure_built(found, directory)
    outline = Outline.model_validate_json(
        await render.call_chrome('/scene/', {'url': render.print_url(found, scene=True)})
    )
    async with workspace.open_artifact(found) as directory:
        images = await asyncio.to_thread(load_images, outline, directory)
    images.update(await svg_pictures(found, outline))
    return await asyncio.to_thread(build_docx, outline, images, found.title)


async def svg_pictures(found: workspace.Artifact, outline: Outline) -> dict[str, bytes]:
    """A PNG of every SVG in the outline, photographed by the chrome service from the page opened with `&svg=N`.

    The window is the SVG's rendered size (at least the service's minimum, the picture cut to size after), at
    `PICTURE_SCALE`.
    """
    limit = asyncio.Semaphore(CONCURRENCY)
    svgs = {image.svg: image for page in outline.pages for image in walk(page.blocks) if image.svg}

    async def picture(image: Image) -> tuple[str, bytes]:
        width, height = max(1, math.ceil(image.width)), max(1, math.ceil(image.height))
        body: dict[str, object] = {
            'url': f'{render.print_url(found, scene=True)}&svg={image.svg}',
            'width': max(width, MIN_WINDOW),
            'height': max(height, MIN_WINDOW),
            'scale': PICTURE_SCALE,
        }
        async with limit:
            png = await render.call_chrome('/screenshot/', body)
        return image.key, await asyncio.to_thread(crop, png, width * PICTURE_SCALE, height * PICTURE_SCALE)

    return dict(await asyncio.gather(*(picture(image) for image in svgs.values())))


def crop(png: bytes, width: int, height: int) -> bytes:
    """`png` cut to `width` x `height` pixels from its top-left corner, where the isolated SVG is; untouched if it fits."""
    with PilImage.open(io.BytesIO(png)) as picture:
        if picture.width <= width and picture.height <= height:
            return png
        out = io.BytesIO()
        picture.crop((0, 0, min(width, picture.width), min(height, picture.height))).save(out, 'PNG')
        return out.getvalue()


def load_images(outline: Outline, directory: Path) -> dict[str, bytes]:
    """The bytes of every image the outline refers to that is a raster file inside the artifact; others are left out."""
    images: dict[str, bytes] = {}
    root = directory.resolve()
    for page in outline.pages:
        for image in walk(page.blocks):
            if image.svg or image.src in images:
                continue
            path = (directory / image.src).resolve()
            if path.is_relative_to(root) and path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                images[image.src] = path.read_bytes()
    return images


def walk(blocks: list[Block]) -> list[Image]:
    """Every image block in `blocks`, including those inside lists and quotes."""
    found: list[Image] = []
    for block in blocks:
        if isinstance(block, Image):
            found.append(block)
        elif isinstance(block, ListBlock):
            for item in block.items:
                found.extend(walk(item.blocks))
        elif isinstance(block, Quote):
            found.extend(walk(block.blocks))
    return found


def build_docx(outline: Outline, images: dict[str, bytes], title: str = '') -> bytes:
    """Assemble the document: the blocks of each page in order, a page break between pages."""
    document = Document()
    document.core_properties.title = title
    writer = Writer(document, images)
    for n, page in enumerate(outline.pages):
        if n:
            document.add_page_break()
        writer.blocks(page.blocks)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


class Writer:
    """Writes blocks into a python-docx document."""

    def __init__(self, document: DocumentType, images: dict[str, bytes]):
        self.document = document
        self.images = images

    def blocks(self, blocks: list[Block], depth: int = 0, quoted: bool = False) -> None:
        """Write `blocks`; `depth` is the list nesting for list paragraphs, `quoted` sets the Quote style on text."""
        for block in blocks:
            if isinstance(block, Heading):
                paragraph = self.document.add_heading(level=min(max(block.level, 1), 9))
                self.runs(paragraph, block.runs)
            elif isinstance(block, ParagraphBlock):
                paragraph = self.document.add_paragraph(style='Quote' if quoted else None)
                self.runs(paragraph, block.runs)
            elif isinstance(block, ListBlock):
                self.list(block, depth)
            elif isinstance(block, Code):
                self.code(block.text)
            elif isinstance(block, Quote):
                self.blocks(block.blocks, depth, quoted=True)
            elif isinstance(block, Table):
                self.table(block)
            elif isinstance(block, Image):
                self.image(block)
            else:
                # A rule: Word has no horizontal rule of its own; a bottom border on an empty paragraph is the idiom.
                self.rule()

    def runs(self, paragraph: Paragraph, runs: list[Run]) -> None:
        for run in runs:
            if run.text == '\n':
                paragraph.add_run().add_break(WD_BREAK.LINE)
                continue
            if run.href:
                added = hyperlink(paragraph, run.href, run.text)
            else:
                added = paragraph.add_run(run.text)
            font = added.font
            font.bold = run.bold or None
            font.italic = run.italic or None
            font.underline = run.underline or None
            font.strike = run.strike or None
            if run.code:
                font.name = CODE_FONT
                font.size = CODE_SIZE

    def list(self, block: ListBlock, depth: int) -> None:
        level = min(depth, LIST_LEVELS - 1)
        style = ('List Number' if block.ordered else 'List Bullet') + (f' {level + 1}' if level else '')
        numbering = None
        for item in block.items:
            paragraph = self.document.add_paragraph(style=style)
            if block.ordered:
                # Word numbers every 'List Number' paragraph in one sequence; each list gets a sequence of its own.
                numbering = numbering or restart_numbering(self.document, paragraph)
                set_numbering(paragraph, numbering, level)
            self.runs(paragraph, item.runs)
            self.blocks(item.blocks, depth + 1)

    def code(self, text: str) -> None:
        paragraph = self.document.add_paragraph(style='No Spacing')
        paragraph.paragraph_format.space_after = Pt(8)
        for n, line in enumerate(text.split('\n')):
            if n:
                paragraph.add_run().add_break(WD_BREAK.LINE)
            run = paragraph.add_run(line)
            run.font.name = CODE_FONT
            run.font.size = CODE_SIZE

    def table(self, block: Table) -> None:
        columns = max((len(row) for row in block.rows), default=0)
        if not columns:
            return
        table = self.document.add_table(rows=len(block.rows), cols=columns)
        table.style = 'Table Grid'
        for row, cells in zip(table.rows, block.rows, strict=True):
            for cell, content in zip(row.cells, cells, strict=False):
                self.runs(cell.paragraphs[0], content.runs)
                if content.header:
                    for run in cell.paragraphs[0].runs:
                        run.font.bold = True
        self.document.add_paragraph()

    def image(self, block: Image) -> None:
        data = self.images.get(block.key)
        if data is None:
            # Not a file in the artifact, or not one Word can show: say what was here.
            self.document.add_paragraph(f'[image: {block.alt or block.src}]').runs[0].font.italic = True
            return
        width = min(Inches(block.width / PX_PER_INCH), IMAGE_WIDTH) if block.width > 0 else IMAGE_WIDTH
        self.document.add_picture(io.BytesIO(data), width=width)

    def rule(self) -> None:
        paragraph = self.document.add_paragraph()
        properties = paragraph.paragraph_format.element.get_or_add_pPr()
        border = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        for name, value in (('val', 'single'), ('sz', '6'), ('space', '1'), ('color', 'auto')):
            bottom.set(qn(f'w:{name}'), value)
        border.append(bottom)
        properties.append(border)


def hyperlink(paragraph: Paragraph, url: str, text: str) -> RunType:
    """Add `text` to `paragraph` as a link to `url`: python-docx has no API for one, so the `w:hyperlink` is written by hand."""
    rid = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), rid)
    run = OxmlElement('w:r')
    properties = OxmlElement('w:rPr')
    underline = OxmlElement('w:u')
    underline.set(qn('w:val'), 'single')
    properties.append(underline)
    run.append(properties)
    node = OxmlElement('w:t')
    node.text = text
    node.set(qn('xml:space'), 'preserve')
    run.append(node)
    link.append(run)
    paragraph._p.append(link)
    return RunType(run, paragraph)


def restart_numbering(document: DocumentType, paragraph: Paragraph) -> int:
    """A new numbering instance of the 'List Number' definition starting at 1, so this list counts from 1."""
    numbering = document.part.numbering_part.element
    style = paragraph.style
    assert style is not None
    num_id = style.element.pPr.numPr.numId.val
    abstract = numbering.num_having_numId(num_id).abstractNumId.val
    new = numbering.add_num(abstract)
    new.add_lvlOverride(0).add_startOverride(1)
    return new.numId


def set_numbering(paragraph: Paragraph, num_id: int, level: int) -> None:
    """Point `paragraph` at numbering instance `num_id` at `level`."""
    properties = paragraph.paragraph_format.element.get_or_add_pPr()
    num_pr = properties.get_or_add_numPr()
    num_pr.get_or_add_numId().val = num_id
    num_pr.get_or_add_ilvl().val = level
