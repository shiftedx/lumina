# Historical Newsreader fonts

The native iOS and tvOS apps now use the system SF Pro family app-wide. These historical files are retained for provenance and are not bundled or registered by either native target. The web gallery retains Newsreader. These TrueType files are lossless decompressions of the existing repository assets:

- `frontend/public/fonts/Newsreader-Latin-variable.woff2` → `apple/Fonts/Newsreader-Latin-variable.ttf`
- `frontend/public/fonts/Newsreader-Italic-Latin-variable.woff2` → `apple/Fonts/Newsreader-Italic-Latin-variable.ttf`

The conversion changes the web-font container to an uncompressed TrueType container; it does not redraw outlines, rename the font, or create static weight instances. The original PostScript names remain `Newsreader-16ptRegular` and `Newsreader-16ptItalic`, formerly selected by the native editorial headings.

To reproduce from the repository root with Python and `fontTools[woff]` installed:

```python
from pathlib import Path
from fontTools.ttLib import TTFont

for name in ("Newsreader-Latin-variable", "Newsreader-Italic-Latin-variable"):
    font = TTFont(Path("frontend/public/fonts") / f"{name}.woff2")
    font.flavor = None
    font.save(Path("apple/Fonts") / f"{name}.ttf")
    font.close()
```

SF Pro comes from the operating system; no downloaded font or custom registration is needed. Publisher typography inside books and user-selected subtitle fonts remain content preferences.

`OFL.txt` is included unchanged from `frontend/public/fonts/OFL.txt`. Newsreader remains under the SIL Open Font License recorded there. Keep that license with redistributed font resources. No network font download is required by the app.
