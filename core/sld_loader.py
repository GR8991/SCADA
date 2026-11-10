"""Turn an uploaded SLD (PDF / PNG / JPG / DXF) into images + text for the AI.

* PDF  -> each page rendered at high DPI with PyMuPDF; embedded vector text is
          also extracted (far more reliable than reading text from pixels).
* IMG  -> used as-is.
* DXF  -> TEXT/MTEXT/ATTRIB values extracted with ezdxf, and the modelspace
          rendered to PNG with matplotlib when possible.

Large drawings are split into overlapping tiles so small ratings stay legible:
the API downsizes any image whose long edge exceeds ~1568 px.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from PIL import Image

Image.MAX_IMAGE_PIXELS = 400_000_000  # big A0 sheets at 200 dpi

MAX_EDGE = 1568                 # API's effective max long edge
MAX_IMAGE_BYTES = 4_800_000     # API limit is 5 MB per image (base64 source)
DETAIL_GRID = {"Standard": 1, "High": 2, "Very high": 3}


@dataclass
class SLDPayload:
    filename: str
    pages: list[Image.Image] = field(default_factory=list)   # full-resolution page renders
    text: str = ""                                            # vector / CAD text
    notes: list[str] = field(default_factory=list)


def load_sld(filename: str, data: bytes, dpi: int = 200, max_pages: int = 6) -> SLDPayload:
    name = filename.lower()
    if name.endswith(".pdf"):
        return _load_pdf(filename, data, dpi, max_pages)
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        img = Image.open(io.BytesIO(data))
        img.load()
        return SLDPayload(filename, pages=[_to_rgb(img)])
    if name.endswith(".dxf"):
        return _load_dxf(filename, data)
    raise ValueError("Unsupported file type. Upload PDF, PNG, JPG, TIFF or DXF. "
                     "Export DWG files to DXF from AutoCAD first.")


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _load_pdf(filename: str, data: bytes, dpi: int, max_pages: int) -> SLDPayload:
    import fitz  # PyMuPDF

    out = SLDPayload(filename)
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count > max_pages:
        out.notes.append(f"PDF has {doc.page_count} pages; only the first {max_pages} were analysed.")
    texts = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        # keep render below ~40 MP to bound memory on Streamlit Cloud
        area_in2 = (page.rect.width / 72) * (page.rect.height / 72)
        eff_dpi = min(dpi, int((40_000_000 / max(area_in2, 1)) ** 0.5))
        pix = page.get_pixmap(dpi=eff_dpi)
        out.pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        t = page.get_text("text").strip()
        if t:
            texts.append(f"--- Page {i + 1} vector text ---\n{t}")
    out.text = "\n".join(texts)
    if not out.text:
        out.notes.append("No embedded text found (scanned PDF) - AI will read labels from the image only.")
    return out


def _load_dxf(filename: str, data: bytes) -> SLDPayload:
    import ezdxf
    from ezdxf import recover

    out = SLDPayload(filename)
    try:
        doc, _ = recover.read(io.BytesIO(data))
    except Exception:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="ignore")))
    msp = doc.modelspace()

    lines = []
    for e in msp.query("TEXT MTEXT"):
        txt = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        if txt and txt.strip():
            x, y = e.dxf.insert.x, e.dxf.insert.y
            lines.append(f"({x:.0f},{y:.0f}) {txt.strip()}")
    for ins in msp.query("INSERT"):
        attrs = [f"{a.dxf.tag}={a.dxf.text}" for a in ins.attribs if a.dxf.text]
        x, y = ins.dxf.insert.x, ins.dxf.insert.y
        lines.append(f"({x:.0f},{y:.0f}) BLOCK {ins.dxf.name}" + (f" [{'; '.join(attrs)}]" if attrs else ""))
    out.text = "--- DXF text, block and attribute entities as (x,y) value ---\n" + "\n".join(lines)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        fig = plt.figure(figsize=(24, 17), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        plt.close(fig)
        out.pages.append(_to_rgb(Image.open(buf)))
    except Exception as exc:  # rendering is best-effort; text is the primary source
        out.notes.append(f"DXF could not be rendered to an image ({exc}); using CAD text only.")
    return out


def _fit(img: Image.Image, max_edge: int = MAX_EDGE) -> Image.Image:
    w, h = img.size
    s = max_edge / max(w, h)
    return img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS) if s < 1 else img


def tiles(img: Image.Image, grid: int, overlap: float = 0.10) -> list[tuple[str, Image.Image]]:
    """Split into grid x grid overlapping tiles, labelled by row/column."""
    if grid <= 1:
        return []
    w, h = img.size
    tw, th = w / grid, h / grid
    ox, oy = tw * overlap, th * overlap
    rows = ["top", "middle", "bottom"] if grid == 3 else ["top", "bottom"]
    cols = ["left", "centre", "right"] if grid == 3 else ["left", "right"]
    out = []
    for r in range(grid):
        for c in range(grid):
            box = (int(max(0, c * tw - ox)), int(max(0, r * th - oy)),
                   int(min(w, (c + 1) * tw + ox)), int(min(h, (r + 1) * th + oy)))
            out.append((f"{rows[r]}-{cols[c]}", img.crop(box)))
    return out


def encode(img: Image.Image) -> tuple[str, str]:
    """Return (media_type, base64) under the API size limit."""
    img = _fit(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= MAX_IMAGE_BYTES:
        return "image/png", base64.b64encode(buf.getvalue()).decode()
    for q in (90, 80, 70):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q)
        if buf.tell() <= MAX_IMAGE_BYTES:
            break
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()


def build_image_set(payload: SLDPayload, detail: str = "High") -> list[tuple[str, Image.Image]]:
    """Overview of every page plus detail tiles. Capped at 20 images per request."""
    grid = DETAIL_GRID.get(detail, 2)
    images: list[tuple[str, Image.Image]] = []
    for i, page in enumerate(payload.pages, start=1):
        images.append((f"Page {i} - full overview", page))
        for label, t in tiles(page, grid):
            images.append((f"Page {i} - detail tile {label}", t))
    return images[:20]
