"""Print a served artifact page to PDF with Chrome headless.

The page already carries its print stylesheet (`@page` in frontend/src/styles/base.css); this module only runs
Chrome against its URL with the matching paper size. It takes a URL rather than a file because the page is not
self-contained: it loads `openartifact.js` and its images from `server.py`. Standard library only, like `build.py`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from build import BuildError

# Slide page size in inches, matching the @page rule in frontend/src/styles/base.css (16:9).
PAPER_WIDTH_IN = 11
PAPER_HEIGHT_IN = 6.1875


def find_chrome() -> str | None:
    """Locate a Chrome or Chromium executable: the macOS app bundle, then names on PATH."""
    mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    if sys.platform == 'darwin' and os.path.exists(mac_path):
        return mac_path
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found


def print_to_pdf(url: str, pdf_path: Path) -> Path:
    """Print the page at `url` to PDF with Chrome headless at the slide page size; returns `pdf_path` resolved.

    When Chrome is missing or exits non-zero the `BuildError` carries the exact command, so it can be copied and
    run by hand.
    """
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    args = [
        '--headless=new',
        '--disable-gpu',
        '--no-margins',
        '--print-to-pdf-no-header',
        f'--paper-width={PAPER_WIDTH_IN}',
        f'--paper-height={PAPER_HEIGHT_IN}',
        f'--print-to-pdf={pdf_path}',
        url,
    ]
    command = ' '.join(shell_quote(a) for a in (chrome or 'google-chrome', *args))
    if chrome is None:
        raise BuildError(f'Chrome / Chromium not found on PATH or in /Applications; run this yourself:\n  {command}')
    result = subprocess.run([chrome, *args], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise BuildError(f'Chrome exited with code {result.returncode}:\n  {command}\n{result.stderr.strip()}')
    return pdf_path


def shell_quote(s: str) -> str:
    """Quote one argument for display (POSIX style)."""
    if re.fullmatch(r'[A-Za-z0-9_\-./:=@%+,]+', s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
