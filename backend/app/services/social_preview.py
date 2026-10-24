"""Public, account-free social metadata for the SPA shell."""

from __future__ import annotations

from html import escape


GENERIC_TITLE = "Lumina — Your household media vault"
GENERIC_DESCRIPTION = "Movies, music, and favorites. One household. One media vault."
GENERIC_IMAGE = "/social/lumina-share-v3.jpg"
INVITE_TITLE = "You’re invited to a Lumina media vault"
INVITE_DESCRIPTION = "You’re invited to explore a Lumina media vault. Movies, music, and favorites, ready to discover."
INVITE_IMAGE = "/social/lumina-invite-v2.jpg"
IMAGE_PATHS = frozenset({GENERIC_IMAGE.removeprefix("/"), INVITE_IMAGE.removeprefix("/")})


def render(index_html: str, *, origin: str, invite: bool) -> str:
    """Insert fixed metadata into the public shell; no request or account values enter the tags."""
    base = origin.rstrip("/")
    title = INVITE_TITLE if invite else GENERIC_TITLE
    description = INVITE_DESCRIPTION if invite else GENERIC_DESCRIPTION
    image = INVITE_IMAGE if invite else GENERIC_IMAGE
    title = escape(title, quote=True)
    description = escape(description, quote=True)
    image_url = escape(f"{base}{image}?v=brand-dbb688", quote=True)
    image_alt = escape("Lumina’s horizon mark beside an ivory-and-gold drawing of a media vault holding film reels and records.", quote=True)
    canonical_url = escape(f"{base}/invite" if invite else f"{base}/", quote=True)
    tags = f"""
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Lumina" />
    <meta property="og:title" content="{title}" />
    <meta property="og:description" content="{description}" />
    <meta property="og:url" content="{canonical_url}" />
    <meta property="og:image" content="{image_url}" />
    <meta property="og:image:type" content="image/jpeg" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content="{image_alt}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{title}" />
    <meta name="twitter:description" content="{description}" />
    <meta name="twitter:image" content="{image_url}" />
    <meta name="twitter:image:alt" content="{image_alt}" />
  """.rstrip()
    return index_html.replace("</head>", f"{tags}\n</head>", 1)
