"""Every email Lumina sends, in one layout: a plain-text part and an HTML part built from the same blocks.

The HTML is email-client safe: tables, inline styles, a bulletproof button (VML for Outlook), a hidden preheader and a
560 px column. It is dark first like the app; a ``prefers-color-scheme: light`` block (Apple Mail, iOS Mail) switches to
the light palette, and backgrounds sit on the table cells too, so a client that drops ``<body>`` styles keeps them.
Callers pass plain strings: every value is escaped here, so no caller writes HTML.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlparse

from app.services import public_address

SERVER_NAME = "Lumina"
LOGO_PATH = "/email-mark.png"  # frontend/public/email-mark.png: favicon.svg rasterized at 128 px, unchanged

# The app's tokens (frontend/src/styles/tokens.css), dark then light.
PAPER, PAPER_2, PAPER_3 = "#0f0e0c", "#1a1814", "#221f1a"
INK, INK_2, INK_3 = "#efe9dd", "#c9c2b4", "#9d968a"
GOLD, GOLD_INK, ON_GOLD, RULE = "#e5a00d", "#f0b43a", "#15130f", "#36322b"
SERIF = "Newsreader,Georgia,'Times New Roman',serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

_LIGHT = """
@media (prefers-color-scheme: light) {
  .lm-paper { background:#f4f1ea !important; }
  .lm-card { background:#fbf9f4 !important; border-color:#e2dccf !important; border-top-color:#e5a00d !important; }
  .lm-well { background:#ece7dc !important; }
  .lm-ink { color:#15130f !important; }
  .lm-ink2 { color:#4a463f !important; }
  .lm-ink3 { color:#615c53 !important; }
  .lm-gold { color:#8a5a00 !important; }
  .lm-rule { border-color:#e2dccf !important; }
}"""


def https_url(value: str | None) -> str | None:
    """``value`` when it is an absolute https URL (a remote poster), else None."""
    parsed = urlparse(value or "")
    return value if parsed.scheme == "https" and parsed.netloc else None


def compose(
    *,
    subject: str,
    preheader: str,
    eyebrow: str,
    heading: str,
    why: str,
    paragraphs: tuple[str, ...] = (),
    meta: str | None = None,
    poster: str | None = None,
    listing: tuple[str, list[str]] | None = None,
    note: tuple[str, str] | None = None,
    button: tuple[str, str] | None = None,
    fineprint: tuple[str, ...] = (),
    unsubscribe: str | None = None,
) -> tuple[str, str]:
    """(text, html). ``poster`` must be an absolute https URL or it is left out; ``why`` says why the reader got it."""
    base = public_address.link_base()
    host = urlparse(base).netloc or base
    footer = f"Sent by {SERVER_NAME} at {host}."
    text = _text(heading, meta, paragraphs, listing, note, button, fineprint, [why, unsubscribe, footer])
    html = _html(subject, preheader, eyebrow, heading, why, paragraphs, meta, https_url(poster), listing, note, button,
                 fineprint, unsubscribe, footer, base)
    return text, html


def _text(heading, meta, paragraphs, listing, note, button, fineprint, footer) -> str:  # noqa: ANN001
    parts = [heading + (f"\n{meta}" if meta else ""), *paragraphs]
    if listing:
        parts.append(f"{listing[0]}:\n" + "\n".join(f"  - {item}" for item in listing[1]))
    if note:
        parts.append(f"{note[0]}: {note[1]}")
    if button:
        parts.append(f"{button[0]}:\n{button[1]}")
    parts += fineprint
    parts.append("--\n" + "\n".join(line for line in footer if line))
    return "\n\n".join(parts) + "\n"


def _html(subject, preheader, eyebrow, heading, why, paragraphs, meta, poster, listing, note, button, fineprint,  # noqa: ANN001, PLR0913
          unsubscribe, footer, base) -> str:
    e = escape
    label = f"font-family:{SANS};font-size:11px;line-height:1.3;font-weight:600;letter-spacing:.18em;text-transform:uppercase"
    body = f"font-family:{SERIF};font-size:17px;line-height:1.6;color:{INK_2}"
    small = f"font-family:{SANS};font-size:13px;line-height:1.55;color:{INK_3}"

    title_block = (
        f'<p class="lm-gold" style="margin:0 0 14px;{label};color:{GOLD_INK}">{e(eyebrow)}</p>'
        f'<h1 class="lm-ink lm-h1" style="margin:0;font-family:{SERIF};font-style:italic;font-weight:600;font-size:34px;'
        f'line-height:1.12;letter-spacing:-.015em;color:{INK}">{e(heading)}</h1>'
        + (f'<p class="lm-ink3" style="margin:10px 0 0;{small};letter-spacing:.06em">{e(meta)}</p>' if meta else "")
    )
    if poster:
        title_block = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
            f'<td width="104" valign="top" class="lm-well" bgcolor="{PAPER_3}" style="width:104px;background:{PAPER_3}">'
            f'<img src="{e(poster)}" width="104" height="156" alt="{e(heading)}" style="display:block;width:104px;height:156px;'
            f'border:0;object-fit:cover;font-family:{SERIF};font-style:italic;font-size:13px;color:{INK_3}"></td>'
            f'<td valign="bottom" style="padding-left:24px">{title_block}</td></tr></table>'
        )

    rows = [f'<tr><td style="padding-bottom:28px">{title_block}</td></tr>']
    rows += [f'<tr><td class="lm-ink2" style="padding-bottom:18px;{body}">{e(text)}</td></tr>' for text in paragraphs]
    if listing:
        items = "".join(
            f'<tr><td width="22" valign="top" class="lm-gold" style="{body};color:{GOLD}">&#8212;</td>'
            f'<td class="lm-ink" style="{body};color:{INK}">{e(item)}</td></tr>' for item in listing[1])
        rows.append(
            f'<tr><td style="padding:4px 0 22px"><p class="lm-ink3" style="margin:0 0 8px;{label};color:{INK_3}">{e(listing[0])}</p>'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{items}</table></td></tr>')
    if note:
        rows.append(
            f'<tr><td style="padding:4px 0 24px"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
            f'<td class="lm-well" bgcolor="{PAPER_3}" style="background:{PAPER_3};border-left:2px solid {GOLD};padding:16px 20px">'
            f'<p class="lm-ink3" style="margin:0 0 6px;{label};color:{INK_3}">{e(note[0])}</p>'
            f'<p class="lm-ink" style="margin:0;font-family:{SERIF};font-style:italic;font-size:18px;line-height:1.5;color:{INK}">'
            f'{e(note[1])}</p></td></tr></table></td></tr>')
    if button:
        rows.append(f'<tr><td style="padding:10px 0 {30 if fineprint else 6}px">{_button(*button)}</td></tr>')
    rows += [f'<tr><td class="lm-ink3 lm-rule" style="{"border-top:1px solid " + RULE + ";padding:22px 0 10px" if not i else "padding-bottom:10px"};'
             f'{small};word-break:break-word">{e(text)}</td></tr>' for i, text in enumerate(fineprint)]

    foot = [why, unsubscribe, footer]
    footer_html = "".join(
        f'<p class="lm-ink3" style="margin:0 0 6px;{small};font-size:12px">{e(line)}</p>' for line in foot if line)
    font = (f"@font-face {{ font-family:'Newsreader'; font-style:italic; font-weight:200 800; "
            f"src:url('{e(base)}/fonts/Newsreader-Italic-variable.woff2') format('woff2'); }}")
    return f"""<!doctype html>
<html lang="en" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{e(subject)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>
{font}
:root {{ color-scheme:light dark; supported-color-schemes:light dark; }}
body {{ margin:0 !important; padding:0 !important; width:100% !important; -webkit-text-size-adjust:100%; }}
a {{ color:{GOLD_INK}; }}
@media (max-width:600px) {{
  .lm-outer {{ padding:20px 10px 28px !important; }}
  .lm-pad {{ padding:32px 24px 30px !important; }}
  .lm-head {{ padding:0 4px 18px !important; }}
  .lm-h1 {{ font-size:28px !important; }}
  .lm-btn, .lm-btn a {{ display:block !important; text-align:center !important; }}
}}{_LIGHT}
</style>
</head>
<body class="lm-paper" bgcolor="{PAPER}" style="margin:0;padding:0;background:{PAPER}">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all">{e(preheader)}{"&#8199;&#847; " * 40}</div>
<table role="presentation" class="lm-paper" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER}">
<tr><td align="center" class="lm-outer" style="padding:40px 16px 48px">
<!--[if mso]><table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;margin:0 auto">
<tr><td class="lm-head" style="padding:0 4px 22px"><a href="{e(base, quote=True)}" style="text-decoration:none">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle" style="padding-right:12px"><img src="{e(base, quote=True)}{LOGO_PATH}" width="36" height="36" alt="{SERVER_NAME}" style="display:block;width:36px;height:36px;border:0;font-family:{SANS};font-size:10px;line-height:36px;color:{INK_3}"></td>
<td valign="middle" class="lm-ink" style="font-family:{SERIF};font-style:italic;font-weight:600;font-size:24px;line-height:1;letter-spacing:-.01em;color:{INK}">{SERVER_NAME}</td>
</tr></table></a></td></tr>
<tr><td class="lm-card lm-pad" bgcolor="{PAPER_2}" style="background:{PAPER_2};border:1px solid {RULE};border-top:2px solid {GOLD};padding:44px 44px 40px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{"".join(rows)}
</table></td></tr>
<tr><td style="padding:24px 4px 0">{footer_html}</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body>
</html>"""


def _button(label: str, url: str) -> str:
    """A gold button that survives Outlook (VML) and image blocking (it is a styled link, not a picture)."""
    href, text = escape(url, quote=True), escape(label)
    width = max(200, len(label) * 11 + 64)
    style = (f"display:inline-block;background:{GOLD};color:{ON_GOLD};font-family:{SANS};font-size:13px;font-weight:700;"
             f"line-height:48px;letter-spacing:.12em;text-transform:uppercase;text-decoration:none;padding:0 30px;border-radius:2px;"
             "mso-hide:all")
    return (
        f'<div class="lm-btn"><!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" '
        f'href="{href}" style="height:48px;v-text-anchor:middle;width:{width}px;" arcsize="4%" stroke="f" fillcolor="{GOLD}">'
        f'<w:anchorlock/><center style="color:{ON_GOLD};font-family:Segoe UI,Arial,sans-serif;font-size:13px;font-weight:bold;'
        f'letter-spacing:2px;">{escape(label.upper())}</center></v:roundrect><![endif]-->'
        f'<a href="{href}" target="_blank" style="{style}">{text}</a></div>'
    )
