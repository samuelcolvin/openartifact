"""Print a served artifact page to PDF, or screenshot it, with Chrome headless.

The page carries its own print stylesheet, including the paper size: each artifact type's sheet sets `@page`
(16:9 slides for a deck, A4 for a document or page), and Chrome honours it, so no paper flags are passed here.
A screenshot is the page as a viewer sees it in a window of the given size; the URL's `#N` hash picks the page
of a deck, or scrolls a document to it. Both take a URL rather than a file because the page is not
self-contained: it loads `openartifact.js` and its images from the application server. Standard library only;
`chrome/server.py` wraps them in HTTP endpoints and `chrome/Dockerfile` supplies Chromium.

Chrome's stderr is kept in every case: it is the only record of what happened when a page fails to load, since
Chrome then exits 0 without writing a file (`Page load failed: net::ERR_...` is all there is).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class ChromeError(Exception):
    """Chrome could not be run, failed, or wrote nothing; the message carries the command to run by hand."""

    def __init__(self, message: str, stderr: str = ''):
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class Printed:
    """What `print_to_pdf` or `screenshot` produced: the file, and everything Chrome wrote to stderr meanwhile."""

    path: Path
    stderr: str


def find_chrome() -> str | None:
    """Locate a Chrome or Chromium executable: the macOS app bundle, then names on PATH."""
    mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    if sys.platform == 'darwin' and os.path.exists(mac_path):
        return mac_path
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found


def container_flags() -> list[str]:
    """Flags for Chrome inside a container, on when `CHROME_NO_SANDBOX` is set (the chrome image sets it).

    Chrome's sandbox needs user namespaces, which Docker's default seccomp profile does not grant, so it has to
    be turned off there; and /dev/shm is 64 MB by default in a container, too small for rendering, so shared
    memory is moved to /tmp.
    """
    if os.environ.get('CHROME_NO_SANDBOX'):
        return ['--no-sandbox', '--disable-dev-shm-usage']
    return []


def print_to_pdf(url: str, pdf_path: Path) -> Printed:
    """Print the page at `url` to PDF with Chrome headless; returns the resolved path and Chrome's stderr.

    `ChromeError` carries the exact command when Chrome is missing, exits non-zero, or exits 0 without writing
    the file (what Chrome does when the page fails to load); its `stderr` is Chrome's output.
    """
    # The flag was `--print-to-pdf-no-header` before Chrome ~130; current builds silently ignore that one.
    return run_chrome(url, pdf_path, ['--no-pdf-header-footer', '--print-to-pdf={path}'], 'a PDF')


def screenshot(url: str, png_path: Path, width: int, height: int) -> Printed:
    """Capture the page at `url` as a PNG of `width` x `height` CSS pixels, as a viewer with that window sees it.

    The same errors as `print_to_pdf`. The hash of `url` is honoured by the page itself: `#3` is the third page
    of a deck, or the third page of a document scrolled into view.
    """
    flags = ['--hide-scrollbars', f'--window-size={width},{height}', '--screenshot={path}']
    return run_chrome(url, png_path, flags, 'a screenshot')


def run_chrome(url: str, output: Path, flags: list[str], what: str) -> Printed:
    """Run Chrome headless against `url` with `flags` (`{path}` in them is the output file) and check it wrote it."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    args = [
        '--headless=new',
        '--disable-gpu',
        *container_flags(),
        *(flag.replace('{path}', str(output)) for flag in flags),
        url,
    ]
    command = ' '.join(shell_quote(a) for a in (chrome or 'google-chrome', *args))
    if chrome is None:
        raise ChromeError(f'Chrome / Chromium not found on PATH or in /Applications; run this yourself:\n  {command}')
    result = subprocess.run([chrome, *args], check=False, capture_output=True, text=True)
    stderr = result.stderr.strip()
    if result.returncode != 0:
        raise ChromeError(f'Chrome exited with code {result.returncode}:\n  {command}\n{stderr}', stderr)
    if not output.is_file():
        raise ChromeError(f'Chrome exited without writing {what}:\n  {command}\n{stderr}', stderr)
    return Printed(output, stderr)


def shell_quote(s: str) -> str:
    """Quote one argument for display (POSIX style)."""
    if re.fullmatch(r'[A-Za-z0-9_\-./:=@%+,]+', s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
