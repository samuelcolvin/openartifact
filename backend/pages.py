"""The server's own HTML pages: sign-in, access denied, and the OAuth consent and error pages FastMCP shows to
MCP clients. One shell in the toolbar's visual language (system font, the dark palette, a centred card), inline
CSS only and no scripts, so the pages satisfy the Content Security Policy FastMCP applies to its consent flow.

`consent_html` and `oauth_error_html` replace FastMCP's renderers (`auth.make_auth_provider` installs them): the
consent logic, CSRF tokens and cookies stay FastMCP's; only the markup is ours, and it keeps the form fields
FastMCP's handler reads (`txn_id`, `csrf_token`, `action` = `approve` | `deny`).
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse

# FastMCP's default consent policy, also applied to our own pages: no scripts, inline styles, https images.
# The brand mark: the "star" vertex of the Penrose kite-and-dart tiling, five darts meeting at their apexes,
# drawn as lines the way the Pydantic logo is: the same path as the app's favicon.
MARK = (
    '<svg class="mark" viewBox="0 0 100 100" aria-hidden="true">'
    '<path d="M 50.00 5.78 L 67.76 30.22 L 96.50 39.56 L 78.74 64.01 L 78.74 94.22 '
    'L 50.00 84.89 L 21.26 94.22 L 21.26 64.01 L 3.50 39.56 L 32.24 30.22 Z '
    'M 50 54.67 L 50.00 5.78 '
    'M 50 54.67 L 96.50 39.56 '
    'M 50 54.67 L 78.74 94.22 '
    'M 50 54.67 L 21.26 94.22 '
    'M 50 54.67 L 3.50 39.56" '
    'fill="none" stroke="#e620e9" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"/></svg>'
)
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' https: data:; base-uri 'none'"

STYLES = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font: 15px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; -webkit-font-smoothing: antialiased; }
body { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 28px; padding: 32px 16px; background: #101317; color: rgba(255, 255, 255, 0.88); }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 15px; letter-spacing: 0.01em;
  color: rgba(255, 255, 255, 0.88); text-decoration: none; }
.brand .mark { width: 16px; height: 16px; }
.card { width: 100%; max-width: 420px; padding: 28px; border-radius: 14px; background: #1c2026;
  border: 1px solid rgba(255, 255, 255, 0.09); box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4); }
.card h1 { font-size: 20px; font-weight: 600; margin-bottom: 8px; color: #f2f4f7; }
.card p { color: rgba(255, 255, 255, 0.62); margin-bottom: 16px; }
.card p.small { font-size: 13px; }
.card dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; margin: 0 0 20px;
  font-size: 13px; color: rgba(255, 255, 255, 0.62); }
.card dt { font-weight: 500; color: rgba(255, 255, 255, 0.45); }
.card dd { overflow-wrap: anywhere; }
code { font: 13px ui-monospace, 'SFMono-Regular', Menlo, monospace; color: #f2f4f7; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; }
.button { display: inline-flex; align-items: center; justify-content: center; gap: 10px; min-height: 42px;
  padding: 0 18px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.12); background: rgba(255, 255, 255, 0.06);
  color: #f2f4f7; font: inherit; font-weight: 500; text-decoration: none; cursor: pointer; }
.button:hover { background: rgba(255, 255, 255, 0.11); }
.button.primary { background: #4a9eff; border-color: #4a9eff; color: #0b1320; }
.button.primary:hover { background: #6bb0ff; }
.button.wide { width: 100%; }
.button svg { width: 18px; height: 18px; }
form.inline { display: contents; }
.field { display: grid; gap: 4px; margin-bottom: 12px; font-size: 13px; color: rgba(255, 255, 255, 0.62); }
.field input { font: inherit; padding: 8px 10px; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.14);
  background: #101317; color: #f2f4f7; }
hr { border: 0; border-top: 1px solid rgba(255, 255, 255, 0.09); margin: 20px 0; }
.foot { font-size: 12px; color: rgba(255, 255, 255, 0.35); }
"""

GOOGLE_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M23 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.2'
    'c-.3 1.4-1.1 2.6-2.3 3.4v2.9h3.7c2.2-2 3.4-5 3.4-8.5z"/><path fill="#34A853" d="M12 24c3.1 0 5.7-1 7.6-2.8'
    'l-3.7-2.9c-1 .7-2.3 1.1-3.9 1.1-3 0-5.5-2-6.4-4.7H1.8v3C3.7 21.4 7.6 24 12 24z"/><path fill="#FBBC05" '
    'd="M5.6 14.7c-.5-1.4-.5-2.9 0-4.3v-3H1.8c-1.6 3.2-1.6 7 0 10.2l3.8-2.9z"/><path fill="#EA4335" d="M12 4.7'
    'c1.7 0 3.2.6 4.4 1.7l3.3-3.3C17.7 1.2 15.1 0 12 0 7.6 0 3.7 2.6 1.8 6.4l3.8 3C6.5 6.7 9 4.7 12 4.7z"/></svg>'
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_page(title: str, body: str, *, csp: str | None = CSP) -> str:
    """The shell: the brand above a card holding `body` (already HTML)."""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{_e(csp)}">\n' if csp else ''
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f'{meta}<title>{_e(title)} - OpenArtifact</title>\n<style>{STYLES}</style>\n</head>\n<body>\n'
        f'<a class="brand" href="/">{MARK}OpenArtifact</a>\n'
        f'<main class="card">\n{body}\n</main>\n</body>\n</html>\n'
    )


def page_response(title: str, body: str, *, status: int = 200) -> HTMLResponse:
    """`render_page` as a response, with the policy and frame headers the content relies on."""
    return HTMLResponse(
        render_page(title, body),
        status_code=status,
        headers={'Content-Security-Policy': CSP, 'X-Frame-Options': 'DENY', 'Cache-Control': 'no-store'},
    )


def login_html(next_path: str, *, google: bool, dev: bool) -> str:
    """The sign-in page: Google when configured, the development sign-in otherwise."""
    parts = ['<h1>Sign in</h1>', '<p>Sign in to see this artifact and the ones shared with you.</p>']
    if google:
        href = '/login/google?' + urlencode({'next': next_path})
        parts.append(f'<a class="button primary wide" href="{_e(href)}">{GOOGLE_ICON}Continue with Google</a>')
    if dev:
        if google:
            parts.append('<hr>')
        parts.append(
            '<form method="POST" action="/login/dev">'
            f'<input type="hidden" name="next" value="{_e(next_path)}">'
            '<label class="field">Email<input name="email" placeholder="dev@localhost"></label>'
            '<label class="field">Workspace domain (optional, joins that organisation)'
            '<input name="hd" placeholder="example.com"></label>'
            '<button class="button wide" type="submit">Continue as the development user</button></form>'
        )
    if not google and not dev:
        parts.append('<p>Sign-in is not configured on this server.</p>')
    parts.append('<hr><p class="foot">OpenArtifact keeps your name, email and picture from Google, nothing else.</p>')
    return '\n'.join(parts)


def forbidden_html(email: str | None, next_path: str) -> str:
    """Signed in, but this artifact is not shared with this account."""
    who = f'You are signed in as <code>{_e(email)}</code>.' if email else 'You are signed in.'
    return (
        '<h1>This artifact is private</h1>'
        f'<p>{who} It has not been shared with you or your organisation.</p>'
        '<div class="actions">'
        f'<form class="inline" method="POST" action="/logout"><input type="hidden" name="next" value="{_e(next_path)}">'
        '<button class="button" type="submit">Switch account</button></form>'
        '<a class="button" href="/">Home</a></div>'
    )


def consent_html(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: Iterable[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    is_cimd_client: bool = False,
    cimd_domain: str | None = None,
    **_: object,
) -> str:
    """FastMCP's consent page in our shell: an MCP client asks to act as the signed-in user."""
    name = client_name or client_id
    origin = cimd_domain if is_cimd_client and cimd_domain else client_id
    scope_text = ', '.join(scopes) or 'none requested'
    body = (
        '<h1>Allow access?</h1>'
        f'<p><strong>{_e(name)}</strong> wants to use OpenArtifact as you: it will create and edit artifacts in '
        'your name and see the ones shared with you.</p>'
        f'<dl><dt>Client</dt><dd><code>{_e(origin)}</code></dd>'
        f'<dt>Returns to</dt><dd><code>{_e(redirect_uri)}</code></dd>'
        f'<dt>Scopes</dt><dd>{_e(scope_text)}</dd></dl>'
        '<form method="POST" action="">'
        f'<input type="hidden" name="txn_id" value="{_e(txn_id)}">'
        f'<input type="hidden" name="csrf_token" value="{_e(csrf_token)}">'
        '<div class="actions">'
        '<button class="button primary" type="submit" name="action" value="approve">Allow</button>'
        '<button class="button" type="submit" name="action" value="deny">Deny</button>'
        '</div></form>'
        '<hr><p class="foot">Only allow clients you started yourself.</p>'
    )
    return render_page('Allow access', body)


def oauth_error_html(
    error_title: str, error_message: str, error_details: dict[str, str] | None = None, **_: object
) -> str:
    """FastMCP's OAuth error page in our shell."""
    details = ''
    if error_details:
        rows = ''.join(f'<dt>{_e(k)}</dt><dd><code>{_e(v)}</code></dd>' for k, v in error_details.items())
        details = f'<dl>{rows}</dl>'
    body = f'<h1>{_e(error_title)}</h1><p>{_e(error_message)}</p>{details}<a class="button" href="/">Home</a>'
    return render_page(error_title, body)
