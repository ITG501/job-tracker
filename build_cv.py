"""Render one CV PDF from data/cv_content.json. Adapted from the Windows build script:
layout unchanged, fonts and photo come from the private data folder.

Layout matches the original Word CV: Cambria headings, Georgia body text,
photo top right, experience on the left, skills/education/languages on the right.
"""
import re
from pathlib import Path

import pymupdf as f

W, H = 612, 792
LX, BX, TX = 45.5, 68, 81.5          # left margin, bullet x, bullet text x
LW = 280                             # bullet text width
RX, RW = 370.6, 192                  # right column
BOTTOM = 760                         # last usable baseline
TEAL, GREY, TAG_INK = 0x006786, 0x605D5D, 0x004961
TAG_FILL = (0.914, 0.973, 1.0)

# BaseFont names PyMuPDF gives the embedded fonts, for the ToUnicode fix below
BASEFONT = {"Georgia Regular": "body", "Cambria Bold": "bold", "Cambria Italic": "italic"}


class Overflow(Exception):
    pass


def load_fonts(data):
    d = Path(data) / "fonts"
    return {"body": f.Font(fontfile=str(d / "georgia.ttf")),
            "bold": f.Font(fontfile=str(d / "cambriab.ttf")),
            "italic": f.Font(fontfile=str(d / "cambriai.ttf"))}


def rgb(c):
    return ((c >> 16) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)


def pick(value, version):
    return value.get(version, value["default"]) if isinstance(value, dict) else value


def shown(item, version):
    return version in item.get("only", [version])


def wrap(text, font, size, width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and font.text_length(trial, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


class Page:
    def __init__(self, page, fonts):
        self.page = page
        self.fonts = fonts
        self.used = ""

    def text(self, x, y, s, font="body", size=9.5, color=0x000000):
        tw = f.TextWriter(self.page.rect)
        tw.append((x, y), s, font=self.fonts[font], fontsize=size)
        tw.write_text(self.page, color=rgb(color))
        self.used += s

    def para(self, x, y, s, width, font="body", size=9.5, color=0x000000, lead=12):
        for line in wrap(s, self.fonts[font], size, width):
            self.text(x, y, line, font, size, color)
            y += lead
        return y - lead

    def heading(self, x, y, s):
        self.text(x, y, s, "bold", 12, TEAL)


def build(content, version, overrides, out, data):
    """version = the base version; overrides replace its subtitle, profile and skills."""
    v = {**content["versions"][version], **overrides}
    fonts = load_fonts(data)
    doc = f.open()
    pg = Page(doc.new_page(width=W, height=H), fonts)

    # header
    pg.text(LX, 57.8, content["name"], "bold", 23.04)
    pg.text(LX, 82.1, v["subtitle"], "italic", 13.56, TEAL)
    pg.text(LX, 98.3, content["contact"][0])
    pg.text(LX, 110.3, content["contact"][1])
    photo = next(Path(data).glob("photo.*"), None)
    if photo:
        pg.page.insert_image(f.Rect(470.5, 36, 549.2, 114.7), filename=str(photo))

    # profile, full width
    pg.heading(LX, 137, "PROFILE")
    y = pg.para(LX, 152, v["profile"], W - LX - 49) + 25

    # left column: experience
    top = y
    pg.heading(LX, y, "EXPERIENCE")
    y += 20.4
    for job in content["experience"]:
        if not shown(job, version):
            continue
        pg.text(LX, y, job["title"], "bold", 10.56)
        pg.text(LX, y + 12, job["dates"], size=8, color=GREY)
        y += 26
        for b in job["bullets"]:
            if not shown(b, version):
                continue
            pg.text(BX, y, "•", size=10)
            y = pg.para(TX, y, pick(b["text"], version), LW) + 16
        y += 3.5
    left_end = y - 19.5

    # right column: skills, education, languages, tools
    y = top
    pg.heading(RX, y, "SKILLS")
    y += 16.7
    x = RX
    for skill in v["skills"]:
        label = f" {skill} "
        w = fonts["body"].text_length(label, 8.52)
        if x > RX and x + w > RX + RW:
            x, y = RX, y + 11.2
        pg.page.draw_rect(f.Rect(x, y - 7.8, x + w, y + 1.9), color=None, fill=TAG_FILL)
        pg.text(x, y, label, size=8.52, color=TAG_INK)
        x += w + 5
    y += 24.8

    pg.heading(RX, y, "EDUCATION")
    y += 18
    for e in content["education"]:
        y = pg.para(RX, y, e["degree"], RW, "bold", 9.48, lead=12.9) + 12
        y = pg.para(RX, y, e["school"], RW, size=8.52, color=GREY, lead=11.2) + 16.3
    y += 7.4

    pg.heading(RX, y, "LANGUAGES")
    y += 17.2
    for lang in content["languages"]:
        pg.text(RX, y, lang, size=9)
        y += 13.4

    tools = content.get("tools")
    if tools and shown(tools, version):
        y += 14.8
        pg.heading(RX, y, tools["heading"])
        y += 18
        for item in tools["items"]:
            pg.text(RX, y, item["title"], "bold", 9.48)
            y = pg.para(RX, y + 12, item["body"], RW, size=8.52, color=GREY, lead=11.2) + 16.3
    right_end = y

    if max(left_end, right_end) > BOTTOM:
        raise Overflow("The CV runs off the page. Shorten the profile or remove a skill.")

    doc.set_metadata({"title": f"{content['name']} CV", "author": content["name"], "subject": v["subtitle"]})
    doc.save(out, garbage=4, deflate=True)
    fix_tounicode(Path(out), pg.used, fonts)


def fix_tounicode(path, used, fonts):
    """Georgia and Cambria use one glyph for space/no-break space, hyphen/soft hyphen and
    ;/Greek question mark, and PyMuPDF's ToUnicode map picks the wrong one. Rebuild the maps
    so application tracking systems read plain text."""
    doc = f.open(path)
    for x in range(1, doc.xref_length()):
        name = doc.xref_get_key(x, "BaseFont")[1].lstrip("/")
        name = name.split("+", 1)[-1]
        if name not in BASEFONT or doc.xref_get_key(x, "ToUnicode")[0] != "xref":
            continue
        font = fonts[BASEFONT[name]]
        tu = int(doc.xref_get_key(x, "ToUnicode")[1].split()[0])
        cmap = doc.xref_stream(tu).decode()
        m = {}
        for blk in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
            for a, b in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", blk):
                m[int(a, 16)] = int(b, 16)
        for blk in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
            for a, z, b in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", blk):
                for i in range(int(a, 16), int(z, 16) + 1):
                    m[i] = int(b, 16) + i - int(a, 16)
        for c in set(used):
            g = font.has_glyph(ord(c))
            if g:
                m[g] = ord(c)
        items = sorted(m.items())
        body = "".join(
            f"{min(100, len(items) - i)} beginbfchar\n"
            + "".join(f"<{k:04X}> <{v:04X}>\n" for k, v in items[i:i + 100])
            + "endbfchar\n"
            for i in range(0, len(items), 100)
        )
        head = cmap.split("endcodespacerange", 1)[0] + "endcodespacerange\n"
        tail = "endcmap" + cmap.split("endcmap", 1)[1]
        doc.update_stream(tu, (head + body + tail).encode())
    doc.subset_fonts()
    tmp = path.with_suffix(".tmp")
    doc.save(tmp, garbage=4, deflate=True)
    doc.close()
    tmp.replace(path)
