#!/usr/bin/env python3
"""Render presentation and size-check sheets from the actual SVG masters.

Requires Pillow and Inkscape on PATH. The default fonts are common URW fonts;
override LUMINA_FONT_REGULAR, LUMINA_FONT_BOLD and LUMINA_FONT_SERIF if needed.
No changes to the SVG artwork are made by this script.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json, math, subprocess, os, hashlib, tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
ICONS = MANIFEST["icons"]
LOOKUP = {i["id"]: i for i in ICONS}
PREVIEW = ROOT / "preview"
PREVIEW.mkdir(exist_ok=True)
CACHE = Path(tempfile.gettempdir()) / "lumina-media-render-cache"
CACHE.mkdir(exist_ok=True)
INK = "#15130F"
CREAM = "#EFE9DD"
PAPER = "#F4F0E8"
CHARCOAL = "#151517"
GOLD = "#DBB688"
MUTED = "#77716A"
FONT_PATHS = {
    "regular": os.getenv("LUMINA_FONT_REGULAR", "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf"),
    "bold": os.getenv("LUMINA_FONT_BOLD", "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf"),
    "serif": os.getenv("LUMINA_FONT_SERIF", "/usr/share/fonts/opentype/urw-base35/P052-Roman.otf"),
}

def font(size, kind="regular"):
    return ImageFont.truetype(FONT_PATHS[kind], size)

def tracked(draw, xy, text, size=13, color=MUTED, tracking=2.5):
    x, y = xy
    face = font(size, "bold")
    for char in text:
        draw.text((x, y), char, font=face, fill=color)
        x += draw.textlength(char, font=face) + tracking

def center(draw, x, y, text, size=18, color=INK, kind="regular"):
    draw.text((x, y), text, anchor="mt", font=font(size, kind), fill=color)

def raster_path(item, variant, size):
    source = (ROOT / item["variants"][variant]["svg"]).read_text().replace("currentColor", "#000000")
    key = hashlib.sha256((source + str(size)).encode()).hexdigest()[:24]
    return CACHE / (key + ".svg"), CACHE / (key + ".png"), source

def warm_cache():
    pending = []
    for item in ICONS:
        for variant in item["variants"]:
            for size in [20, 24, 28, 96]:
                source, output, content = raster_path(item, variant, size)
                if not output.exists() or not output.stat().st_size:
                    source.write_text(content)
                    pending.append((source, output, size))
    # Each Inkscape invocation handles several exports. Separate profiles avoid locks.
    batches = [pending[i:i+24] for i in range(0, len(pending), 24)]
    def run_batch(pair):
        number, batch = pair
        actions = []
        for source, output, size in batch:
            actions += [f"file-open:{source}", "export-type:png", f"export-filename:{output}", f"export-width:{size}", f"export-height:{size}", "export-do", "file-close"]
        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(CACHE / f"profile-{number}")
        result = subprocess.run(["inkscape", "--batch-process", "--actions=" + ";".join(actions)], env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        for _, output, _ in batch:
            if not output.is_file() or not output.stat().st_size:
                raise RuntimeError(f"No rendered output: {output}")
        return len(batch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count in pool.map(run_batch, enumerate(batches)):
            print(f"Rendered {count} SVG size samples", flush=True)

def icon(slug, variant="outline", size=64, color=INK):
    item = LOOKUP[slug]
    if variant not in item["variants"]:
        variant = "outline"
    sample_size = size if size in [20, 24, 28] else 96
    _, output, _ = raster_path(item, variant, sample_size)
    with Image.open(output) as raw:
        alpha = raw.convert("RGBA").getchannel("A")
    if alpha.size != (size, size):
        alpha = alpha.resize((size, size), Image.Resampling.LANCZOS)
    shape = Image.new("RGBA", (size, size), color)
    shape.putalpha(alpha)
    return shape

def put(board, slug, xy, size=64, variant="outline", color=INK):
    board.alpha_composite(icon(slug, variant, size, color), tuple(map(int, xy)))

def save(board, filename):
    board.convert("RGB").save(PREVIEW / filename, optimize=True)
    print(filename, board.size, flush=True)

def overview():
    board = Image.new("RGBA", (1800, 1490), PAPER)
    draw = ImageDraw.Draw(board)
    draw.text((76, 48), "lumina", font=font(91, "serif"), fill=INK)
    tracked(draw, (82, 160), "MEDIA ICONS / 01", 14)
    draw.text((1065, 67), "The whole media experience.", font=font(31), fill=INK)
    draw.text((1065, 112), "One familiar design language.", font=font(31), fill=INK)
    draw.line((80, 218, 1720, 218), fill="#D5CEC2", width=1)
    # A playback specimen: layout and surfaces belong to the host control.
    draw.rounded_rectangle((66, 251, 1734, 640), radius=30, fill=CHARCOAL)
    tracked(draw, (104, 281), "PLAYBACK / SOLID", 13, "#AAA49C")
    centers = [465, 665, 900, 1135, 1335]
    ids = ["shuffle", "previous", "play", "next", "repeat"]
    for x, slug in zip(centers, ids):
        if slug == "play":
            draw.ellipse((x-72, 343, x+72, 487), fill=GOLD)
            put(board, slug, (x-35, 380), 70, "solid", INK)
        else:
            size = 56 if slug in ["previous", "next"] else 40
            put(board, slug, (x-size/2, 415-size/2), size, "solid", CREAM)
    draw.rounded_rectangle((406, 548, 1394, 552), radius=2, fill="#454144")
    draw.rounded_rectangle((406, 548, 797, 552), radius=2, fill=GOLD)
    draw.ellipse((791, 544, 803, 556), fill=GOLD)
    draw.text((406, 573), "1:42", font=font(17), fill="#B5AEA5")
    draw.text((1394, 573), "4:18", anchor="rt", font=font(17), fill="#B5AEA5")
    # Twenty-four signature samples; all rendered from exported masters.
    featured = ["home", "library", "requests", "user-settings", "search", "discover", "pause", "seek-back-10", "seek-forward-10", "volume-high", "subtitles", "queue", "movies", "music", "photos", "podcasts", "favorite", "download", "camera", "backup", "server", "cast", "settings", "headphones"]
    tracked(draw, (82, 679), "BUILT TO BELONG TOGETHER", 13)
    for index, slug in enumerate(featured):
        row, col = divmod(index, 8)
        x = 78 + col * 207
        y = 725 + row * 182
        draw.rounded_rectangle((x, y, x+195, y+166), radius=14, fill="#FCFAF6")
        put(board, slug, (x+65, y+26), 64)
        center(draw, x+97, y+118, LOOKUP[slug]["label"], 16)
    draw.line((80, 1307, 1720, 1307), fill="#D5CEC2", width=1)
    draw.text((82, 1342), "104 icons", font=font(38, "serif"), fill=INK)
    draw.text((380, 1342), "40 solid alternates", font=font(38, "serif"), fill=INK)
    draw.text((875, 1349), "24 × 24 grid  /  1.75 stroke  /  Transparent SVG", font=font(24), fill=MUTED)
    draw.text((82, 1415), "Production artwork shown here. SVG · SwiftUI · React · Searchable catalog", font=font(20), fill=MUTED)
    save(board, "lumina-media-icons-overview.png")

def full_catalog(dark=False):
    cell = 184
    row_height = 139
    groups = [(family, [i for i in ICONS if i["category"] == family]) for family in MANIFEST["categories"]]
    height = 236 + sum(53 + math.ceil(len(items)/8)*row_height + 30 for _, items in groups) + 57
    bg, fg, card, muted = (CHARCOAL, CREAM, "#1F1F22", "#AAA49C") if dark else (PAPER, INK, "#FCFAF6", MUTED)
    board = Image.new("RGBA", (1600, height), bg)
    draw = ImageDraw.Draw(board)
    draw.text((64, 40), "lumina", font=font(72, "serif"), fill=fg)
    tracked(draw, (69, 130), "MEDIA ICONS / COMPLETE OUTLINE SET", 12, muted)
    draw.text((1534, 68), "104 designs / 8 families", font=font(24), fill=fg, anchor="rt")
    draw.text((1534, 108), "24 × 24  ·  1.75 stroke", font=font(17), fill=muted, anchor="rt")
    y = 207
    for family, members in groups:
        tracked(draw, (68, y), family.upper(), 14, muted)
        y += 43
        for index, item in enumerate(members):
            row, col = divmod(index, 8)
            x = 64 + col*cell
            yy = y + row*row_height
            draw.rounded_rectangle((x, yy, x+172, yy+127), radius=12, fill=card)
            put(board, item["id"], (x+62, yy+20), 48, color=fg)
            center(draw, x+86, yy+88, item["id"], 13, fg)
            if item["hasSolid"]:
                draw.ellipse((x+153, yy+11, x+157, yy+15), fill=GOLD)
        y += math.ceil(len(members)/8)*row_height + 40
    draw.text((68, height-53), "Warm dot = solid alternate available.   Geometry and labels correspond to the exported SVG filenames.", font=font(16), fill=muted)
    save(board, "lumina-media-icons-catalog-dark.png" if dark else "lumina-media-icons-catalog-light.png")

def solid_sheet():
    members = [i for i in ICONS if i["hasSolid"]]
    board = Image.new("RGBA", (1600, 1430), CHARCOAL)
    draw = ImageDraw.Draw(board)
    draw.text((64, 42), "lumina", font=font(72, "serif"), fill=CREAM)
    tracked(draw, (69, 133), "SOLID ALTERNATES / 40 DESIGNS", 13, "#AAA49C")
    draw.text((1534, 70), "Outline → solid", font=font(25), fill=CREAM, anchor="rt")
    draw.text((1534, 109), "Style is separate from interaction state.", font=font(18), fill="#AAA49C", anchor="rt")
    for index, item in enumerate(members):
        row, col = divmod(index, 5)
        x, y = 62 + col*297, 205 + row*146
        draw.rounded_rectangle((x, y, x+283, y+130), radius=14, fill="#202023")
        put(board, item["id"], (x+61, y+20), 48, color="#B4AFA6")
        put(board, item["id"], (x+172, y+20), 48, "solid", CREAM)
        draw.line((x+138, y+27, x+138, y+62), fill="#474144", width=1)
        center(draw, x+142, y+91, item["id"], 15, CREAM)
    save(board, "lumina-media-icons-solid-variants.png")

def size_checks():
    # Full inventory at real render sizes; the 24px sample also has a 3× pixel view.
    width, columns = 1680, 4
    height = 173 + math.ceil(len(ICONS)/columns)*134 + 44
    board = Image.new("RGBA", (width, height), PAPER)
    draw = ImageDraw.Draw(board)
    draw.text((42, 31), "lumina / size checks", font=font(45, "serif"), fill=INK)
    draw.text((44, 97), "Every outline at 20, 24 and 28 px. The last sample is a 3× nearest-neighbor enlargement of the 24px render.", font=font(18), fill=MUTED)
    for index, item in enumerate(ICONS):
        row, col = divmod(index, columns)
        x, y = 40 + col*405, 158 + row*134
        draw.line((x, y-8, x+380, y-8), fill="#DDD5C9", width=1)
        draw.text((x, y), item["id"], font=font(15, "bold"), fill=INK)
        for off, size in [(0, 20), (67, 24), (137, 28)]:
            put(board, item["id"], (x+off, y+47), size)
            draw.text((x+off, y+84), str(size), font=font(11), fill=MUTED)
        sample = icon(item["id"], size=24).resize((72,72), Image.Resampling.NEAREST)
        board.alpha_composite(sample, (x+255, y+31))
    save(board, "lumina-media-icons-size-check.png")
    # Compact category sheets are handy for visual review without scaling a long sheet.
    for family in MANIFEST["categories"]:
        members = [i for i in ICONS if i["category"] == family]
        board = Image.new("RGBA", (1440, 140 + math.ceil(len(members)/6)*190), PAPER)
        draw = ImageDraw.Draw(board)
        draw.text((30, 24), family + " / outline and solid", font=font(35, "serif"), fill=INK)
        for index, item in enumerate(members):
            row,col = divmod(index,6)
            x,y = 28+col*236,110+row*190
            draw.text((x,y),item["id"],font=font(15),fill=MUTED)
            put(board,item["id"],(x,y+32),64)
            if item["hasSolid"]: put(board,item["id"],(x+95,y+32),64,"solid")
            put(board,item["id"],(x+8,y+115),24)
            if item["hasSolid"]: put(board,item["id"],(x+104,y+115),24,"solid")
        board.convert("RGB").save(CACHE/(family.lower()+"-qa.png"))

def audit_rasters():
    # A nonempty alpha box strictly inside the viewport catches actual clipping.
    checks = []
    for item in ICONS:
        for variant in item["variants"]:
            _, file, _ = raster_path(item, variant, 96)
            with Image.open(file) as raw:
                box = raw.getchannel("A").getbbox()
                if not box or box[0] <= 0 or box[1] <= 0 or box[2] >= 96 or box[3] >= 96:
                    checks.append({"id":item["id"], "variant":variant, "bounds":box})
    print(json.dumps({"boundary_warnings":checks}), flush=True)
    if checks:
        raise RuntimeError("Inspect boundary warnings before packaging")

if __name__ == "__main__":
    warm_cache()
    audit_rasters()
    overview()
    full_catalog(False)
    full_catalog(True)
    solid_sheet()
    size_checks()
