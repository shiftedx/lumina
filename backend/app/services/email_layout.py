"""Lumina's media-led emails: escaped live content, plain-text parity, and email-safe tables.

Static vault artwork and request posters add atmosphere; status, facts and actions remain readable when images are
blocked. Inline styles and Outlook's VML button are the baseline; responsive and light-theme CSS enhance it.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlparse

from app.services import public_address

SERVER_NAME = "Lumina"
LOGO_PATH = "/email/mark-v1.png"
HERO_PATH = "/email/vault-v1.jpg"
ART_PATHS = {
    "connected": "/email/film-archive-v1.jpg",
    "failed": "/email/film-archive-v1.jpg",
    "security": "/email/security-keys-v1.jpg",
}
PAPER, PAPER_2, PAPER_3 = "#0f0e0c", "#1a1814", "#221f1a"
INK, INK_2, INK_3 = "#efe9dd", "#c9c2b4", "#9d968a"
BRAND, LIGHT_INK, RULE = "#dbb688", "#15130f", "#36322b"
SERIF = "Newsreader,Georgia,'Times New Roman',serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
# Functional status colours mirror the app; colour never carries the status by itself.
STATES = {
    "pending": ("◷", BRAND, LIGHT_INK),
    "approved": ("✓", BRAND, LIGHT_INK),
    "available": ("▶", "#8cc79a", "#2f6b3a"),
    "declined": ("—", INK_2, "#4a463f"),
    "failed": ("!", "#e58a74", "#a3321f"),
    "security": ("!", "#e58a74", "#a3321f"),
    "connected": ("✓", "#8cc79a", "#2f6b3a"),
    "invite": ("↗", BRAND, LIGHT_INK),
}

_LIGHT = """
@media (prefers-color-scheme: light) {
  .lm-paper { background:#f4f1ea !important; }
  .lm-card { background:#fbf9f4 !important; }
  .lm-well { background:#ece7dc !important; }
  .lm-ink { color:#15130f !important; }
  .lm-ink2 { color:#4a463f !important; }
  .lm-ink3 { color:#615c53 !important; }
  .lm-accent, a { color:#15130f !important; }
  .lm-btn a { background:#15130f !important; color:#f4f1ea !important; }
  .lm-rule { border-color:#e2dccf !important; }
  .lm-future { background:#e2dccf !important; }
}"""


def https_url(value: str | None) -> str | None:
    """Only absolute HTTPS posters can enter the email; private relative art stays out."""
    parsed = urlparse(value or "")
    return value if parsed.scheme == "https" and parsed.netloc else None


def compose(
    *, subject: str, preheader: str, eyebrow: str, heading: str, why: str,
    paragraphs: tuple[str, ...] = (), meta: str | None = None, poster: str | None = None,
    listing: tuple[str, list[str]] | None = None, note: tuple[str, str] | None = None,
    button: tuple[str, str] | None = None, fineprint: tuple[str, ...] = (), unsubscribe: str | None = None, image_cid: str | None = None,
    facts: tuple[tuple[str, str], ...] = (), status: str | None = None, progress: int | None = None,
) -> tuple[str, str]:
    """(text, html). Every caller value is plain text, escaped here; no caller supplies markup."""
    base = public_address.link_base()
    footer = f"Sent by {SERVER_NAME} at {urlparse(base).netloc or base}."
    text = _text(heading, meta, paragraphs, facts, listing, note, button, fineprint, [why, unsubscribe, footer])
    html = _html(subject, preheader, eyebrow, heading, why, paragraphs, meta, https_url(poster), listing, note, button,
                 fineprint, unsubscribe, footer, base, facts, status, progress, image_cid)
    return text, html


def _text(heading, meta, paragraphs, facts, listing, note, button, fineprint, footer) -> str:  # noqa: ANN001
    parts = [heading + (f"\n{meta}" if meta else ""), *paragraphs]
    if facts:
        parts.append("\n".join(f"{label}: {value}" for label, value in facts))
    if listing:
        parts.append(f"{listing[0]}:\n" + "\n".join(f"  - {item}" for item in listing[1]))
    if note:
        parts.append(f"{note[0]}: {note[1]}")
    if button:
        parts.append(f"{button[0]}:\n{button[1]}")
    parts += fineprint
    parts.append("--\n" + "\n".join(line for line in footer if line))
    return "\n\n".join(parts) + "\n"


def _progress(step: int) -> str:
    cells = []
    for i, name in enumerate(("Requested", "Approved", "Ready")):
        active = i <= step
        colour = BRAND if active else RULE
        cells.append(
            f'<td width="33%" style="padding:0 {0 if i == 2 else 8}px 0 0">'
            f'<div class="{"" if active else "lm-future"}" style="height:3px;background:{colour};font-size:1px;line-height:3px">&nbsp;</div>'
            f'<p class="{"lm-accent" if active else "lm-ink3"}" style="margin:8px 0 0;font-family:{SANS};font-size:11px;'
            f'line-height:1.4;color:{BRAND if active else INK_3}">{"✓ " if i < step else ""}{name}</p></td>')
    return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>' + "".join(cells) + '</tr></table>'


def _html(subject, preheader, eyebrow, heading, why, paragraphs, meta, poster, listing, note, button, fineprint,  # noqa: ANN001, PLR0913
          unsubscribe, footer, base, facts, status, progress, image_cid) -> str:
    e = escape
    label = f"font-family:{SANS};font-size:11px;line-height:1.4;font-weight:600;letter-spacing:.12em;text-transform:uppercase"
    body = f"font-family:{SANS};font-size:15px;line-height:1.65;color:{INK_2}"
    small = f"font-family:{SANS};font-size:12px;line-height:1.6;color:{INK_3}"
    symbol, status_colour, light_colour = STATES.get(status, ("", BRAND, LIGHT_INK))
    status_block = (
        f'<p class="lm-status" style="margin:0 0 14px;{label};color:{status_colour}">'
        f'<span aria-hidden="true" style="font-size:16px;vertical-align:middle">{symbol}</span> {e(eyebrow)}</p>')
    title_block = (
        status_block + f'<h1 class="lm-ink lm-h1" style="margin:0;font-family:{SERIF};font-style:italic;font-weight:700;'
        f'font-size:38px;line-height:1.08;letter-spacing:-.015em;color:{INK};overflow-wrap:anywhere;word-break:break-word">{e(heading)}</h1>'
        + (f'<p class="lm-ink3" style="margin:12px 0 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{INK_3}">{e(meta)}</p>' if meta else "")
    )
    if progress is not None:
        title_block += f'<div style="padding-top:24px">{_progress(progress)}</div>'
    if poster:
        title_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed"><tr>'
            f'<td valign="middle" class="lm-media-copy" style="padding-right:24px">{title_block}</td>'
            f'<td width="148" valign="top" class="lm-poster-cell" style="width:148px">'
            f'<img src="{e(poster, quote=True)}" width="148" height="222" alt="{e(heading, quote=True)}" class="lm-poster" '
            f'style="display:block;width:148px;height:222px;border:0;border-radius:12px;object-fit:cover;background:{PAPER_3};'
            f'font-family:{SANS};font-size:13px;line-height:1.5;color:{INK_3}"></td></tr></table>')

    rows = [f'<tr><td style="padding-bottom:22px">{title_block}</td></tr>']
    rows += [f'<tr><td class="lm-ink2" style="padding-bottom:18px;{body}">{e(text)}</td></tr>' for text in paragraphs]
    if image_cid:
        rows.append(f'<tr><td align="center" style="padding:0 0 24px"><img src="cid:{e(image_cid, quote=True)}" width="240" height="240" alt="Mobile connection QR code" style="display:block;width:240px;height:240px;border:0"></td></tr>')
    if facts:
        cells = []
        for i, (name, value) in enumerate(facts):
            cells.append(f'<td width="50%" valign="top" style="padding:0 16px 14px 0"><p class="lm-ink3" style="margin:0 0 5px;{label};color:{INK_3}">{e(name)}</p>'
                         f'<p class="lm-ink" style="margin:0;font-family:{SANS};font-size:16px;line-height:1.5;font-weight:600;color:{INK};overflow-wrap:anywhere">{e(value)}</p></td>')
            if i % 2:
                cells[-1] += '</tr><tr>'
        rows.append('<tr><td style="padding:4px 0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed"><tr>'
                    + "".join(cells).removesuffix('</tr><tr>') + '</tr></table></td></tr>')
    if listing:
        if status == "connected" and len(listing[1]) == 3:
            items = "".join(
                f'<td width="33%" valign="top" style="padding-right:12px"><p class="lm-accent" aria-hidden="true" style="margin:0 0 10px;'
                f'font-family:{SANS};font-size:25px;line-height:1;color:{BRAND}">{symbol}</p>'
                f'<p class="lm-ink" style="margin:0 0 5px;font-family:{SANS};font-size:13px;font-weight:600;line-height:1.5;color:{INK}">{e(item.partition(" · ")[0])}</p>'
                f'<p class="lm-ink3" style="margin:0;{small}">{e(item.partition(" · ")[2])}</p></td>'
                for symbol, item in zip(("↗", "▶", "!"), listing[1], strict=True))
            items = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed"><tr>' + items + '</tr></table>'
        else:
            items = "".join(f'<span class="lm-well lm-ink" style="display:inline-block;background:{PAPER_3};border-radius:8px;padding:7px 12px;'
                            f'margin:0 6px 6px 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{INK}">{e(item)}</span>' for item in listing[1])
        rows.append(f'<tr><td style="padding:2px 0 20px"><p class="lm-ink3" style="margin:0 0 10px;{label};color:{INK_3}">{e(listing[0])}</p>'
                    f'{items}</td></tr>')
    if note:
        rows.append(f'<tr><td style="padding:2px 0 22px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                    f'<td class="lm-well" bgcolor="{PAPER_3}" style="background:{PAPER_3};border-radius:12px;padding:16px 20px">'
                    f'<p class="lm-ink3" style="margin:0 0 6px;{label};color:{INK_3}">{e(note[0])}</p>'
                    f'<p class="lm-ink" style="margin:0;{body};color:{INK}">{e(note[1])}</p></td></tr></table></td></tr>')
    if button:
        rows.append(f'<tr><td style="padding:6px 0 8px">{_button(*button)}</td></tr>')
        rows.append(f'<tr><td style="padding-top:12px;{small}">Button not working? <a class="lm-accent" href="{e(button[1], quote=True)}" '
                    f'style="color:{BRAND};text-decoration:underline">Open this link</a>.</td></tr>')
    rows += [f'<tr><td class="lm-ink3" style="padding-top:14px;{small};overflow-wrap:anywhere">{e(text)}</td></tr>' for text in fineprint]
    footer_html = "".join(f'<p class="lm-ink3" style="margin:0 0 6px;{small}">{e(line)}</p>' for line in (why, unsubscribe, footer) if line)
    art_path = ART_PATHS.get(status, HERO_PATH)
    hero_image = (
        f'<img src="{e(base, quote=True)}{art_path}" width="640" alt="" '
        f'style="display:block;width:100%;max-width:640px;height:auto;border:0;border-radius:12px 12px 0 0;color:{INK_2};font-family:{SANS};font-size:13px">')
    # Modern clients collapse blocked artwork to its alt text; classic Outlook retains explicit image dimensions.
    hero = "" if poster else (
        f'<tr><td bgcolor="{PAPER}" style="background:{PAPER};border-radius:12px 12px 0 0;overflow:hidden">'
        f'<!--[if mso]>{hero_image.replace("width=\"640\"", "width=\"640\" height=\"240\"")}<![endif]-->'
        f'<!--[if !mso]><!-->{hero_image}<!--<![endif]--></td></tr>')
    font = "\n".join(f"@font-face {{ font-family:'Newsreader';font-style:{style};font-weight:200 800;src:url('{e(base)}/fonts/{file}') format('woff2'); }}"
                     for style, file in (("normal", "Newsreader-variable.woff2"), ("italic", "Newsreader-Italic-variable.woff2")))
    return f'''<!doctype html>
<html lang="en" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting"><meta name="format-detection" content="telephone=no, date=no, address=no, email=no">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<title>{e(subject)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>{font}
:root {{ color-scheme:light dark; supported-color-schemes:light dark; }}
body {{ margin:0 !important;padding:0 !important;width:100% !important;-webkit-text-size-adjust:100%; }}
a {{ color:{BRAND}; }}
@media (max-width:600px) {{
.lm-outer {{ padding:20px 12px 28px !important; }}
.lm-pad {{ padding:28px 24px !important; }}
.lm-h1 {{ font-size:30px !important; }}
.lm-poster-cell {{ width:96px !important; }}
.lm-poster {{ width:96px !important;height:144px !important; }}
.lm-media-copy {{ padding-right:16px !important; }}
.lm-btn,.lm-btn a {{ display:block !important;text-align:center !important; }}
.lm-tagline {{ display:none !important; }}
}}{_LIGHT}
@media (prefers-color-scheme: light) {{ .lm-status {{ color:{light_colour} !important; }} }}
</style></head>
<body class="lm-paper" bgcolor="{PAPER}" style="margin:0;padding:0;background:{PAPER}">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all">{e(preheader)}{"&#8199;&#847; " * 40}</div>
<table role="presentation" class="lm-paper" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER}"><tr>
<td align="center" class="lm-outer" style="padding:32px 16px 40px">
<!--[if mso]><table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;margin:0 auto">
<tr><td style="padding:0 4px 20px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td><a href="{e(base, quote=True)}" style="text-decoration:none"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle" style="padding-right:12px"><img src="{e(base, quote=True)}{LOGO_PATH}" width="36" height="28" alt="Lumina" style="display:block;width:36px;height:28px;border:0;color:{INK_3}"></td>
<td class="lm-ink" valign="middle" style="font-family:{SERIF};font-size:21px;font-weight:600;line-height:1;letter-spacing:.1em;color:{INK}">LUMINA</td>
</tr></table></a></td><td align="right" class="lm-ink3 lm-tagline" style="{label};color:{INK_3}">The media vault</td></tr></table></td></tr>
<tr><td class="lm-card" bgcolor="{PAPER_2}" style="background:{PAPER_2};border-radius:12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{hero}
<tr><td class="lm-pad" style="padding:36px 36px 32px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table></td></tr>
</table></td></tr><tr><td style="padding:20px 4px 0">{footer_html}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def _button(label: str, url: str) -> str:
    """Live HTML action, with VML for classic Outlook; no image is needed to act."""
    href, text = escape(url, quote=True), escape(label)
    width = max(200, len(label) * 10 + 64)
    style = (f"display:inline-block;background:{INK};color:{PAPER};font-family:{SANS};font-size:12px;font-weight:700;"
             "line-height:48px;letter-spacing:.1em;text-transform:uppercase;text-decoration:none;padding:0 28px;border-radius:2px;mso-hide:all")
    return (
        f'<div class="lm-btn"><!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" '
        f'href="{href}" style="height:48px;v-text-anchor:middle;width:{width}px;" arcsize="4%" stroke="f" fillcolor="{INK}">'
        f'<w:anchorlock/><center style="color:{PAPER};font-family:Segoe UI,Arial,sans-serif;font-size:12px;font-weight:bold;letter-spacing:1px">'
        f'{escape(label.upper())}</center></v:roundrect><![endif]--><a href="{href}" target="_blank" style="{style}">{text} &nbsp;↗</a></div>')
