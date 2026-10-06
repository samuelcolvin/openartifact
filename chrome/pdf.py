"""Print a served artifact page to PDF with Chrome headless.

The page carries its own print stylesheet, including the paper size: each artifact type's sheet sets `@page`
(16:9 slides for a deck, A4 for a document or page), and Chrome honours it, so no paper flags are passed here.
It takes a URL rather than a file because the page is not self-contained: it loads `openartifact.js` and its
images from the application server. Standard library only; `chrome/server.py` wraps it in an HTTP endpoint and
`chrome/Dockerfile` supplies Chromium.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class ChromeError(Exception):
    """Chrome could not be run or failed; the message carries the exact command, so it can be run by hand."""


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


def print_to_pdf(url: str, pdf_path: Path) -> Path:
    """Print the page at `url` to PDF with Chrome headless; returns `pdf_path` resolved.

    When Chrome is missing or exits non-zero the `ChromeError` carries the exact command, so it can be copied
    and run by hand.
    """
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    args = [
        '--headless=new',
        '--disable-gpu',
        *container_flags(),
        # The flag was `--print-to-pdf-no-header` before Chrome ~130; current builds silently ignore that one.
        '--no-pdf-header-footer',
        f'--print-to-pdf={pdf_path}',
        url,
    ]
    command = ' '.join(shell_quote(a) for a in (chrome or 'google-chrome', *args))
    if chrome is None:
        raise ChromeError(f'Chrome / Chromium not found on PATH or in /Applications; run this yourself:\n  {command}')
    result = subprocess.run([chrome, *args], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ChromeError(f'Chrome exited with code {result.returncode}:\n  {command}\n{result.stderr.strip()}')
    return pdf_path


def shell_quote(s: str) -> str:
    """Quote one argument for display (POSIX style)."""
    if re.fullmatch(r'[A-Za-z0-9_\-./:=@%+,]+', s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
