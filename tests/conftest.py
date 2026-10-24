"""Test utilities: builds small synthetic PDFs laid out like Google Books scans.

Real Google Books PDFs (Producer "Google Books PDF Converter (rel 3 12/12/14)") have:
  * page 0: a notice page with visible text, a link to books.google.com and a
    29x29 QR image named /FM:pod:QRCode, and no watermark;
  * every other page: a full-page scan image, an invisible OCR text layer, and
    one shared 1-bit /Wm image (stencil /Mask, height 204) drawn small at the
    bottom with 30% opacity.
"""

import os
import sys
import zlib

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, Pdf, Stream

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAGE_W, PAGE_H = 612, 792
GOOGLE_PRODUCER = "Google Books PDF Converter (rel 3 12/12/14)"
NOTICE_LINES = [
    "This is a reproduction of a library book that was digitized",
    "by Google as part of an ongoing effort to preserve the",
    "information in books and make it universally accessible.",
    "https://books.google.com",
]
GOOGLE_URI = "https://books.google.com/books?id=TESTID"
WM_PLACEMENT = (273, 10, 66, 13)  # x, y, width, height in points
FULL_PAGE = (0, 0, PAGE_W, PAGE_H)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "watermark_config.yaml")


def _gray_image(pdf, w, h, ink=True, stripes=False):
    """8-bit gray image; a dark band across the middle gives it visible ink.

    stripes: alternate black/white columns this many pixels wide instead (a stand-in for a
             detailed scan); 0/False for the default band.
    """
    data = bytearray(b"\xff" * (w * h))
    if stripes:
        for y in range(h):
            for x in range(w):
                if (x // stripes) % 2 == 0:
                    data[y * w + x] = 0
    elif ink:
        for y in range(h // 4, h // 2):
            for x in range(w // 8, w - w // 8):
                data[y * w + x] = 0
    return Stream(pdf, zlib.compress(bytes(data)), Type=Name.XObject, Subtype=Name.Image,
                  Width=w, Height=h, ColorSpace=Name.DeviceGray, BitsPerComponent=8,
                  Filter=Name.FlateDecode)


def _wm_image(pdf, w, h, stencil_mask=True, bpc=1):
    """Google-style watermark: black 1-bit image with an explicit stencil /Mask."""
    row = (w * bpc + 7) // 8
    img = Stream(pdf, zlib.compress(b"\x00" * row * h), Type=Name.XObject, Subtype=Name.Image,
                 Width=w, Height=h, ColorSpace=Name.DeviceGray, BitsPerComponent=bpc,
                 Filter=Name.FlateDecode)
    if stencil_mask:
        mrow = (w + 7) // 8
        img.Mask = Stream(pdf, zlib.compress(b"\x00" * mrow * h), Type=Name.XObject,
                          Subtype=Name.Image, Width=w, Height=h, ImageMask=True,
                          BitsPerComponent=1, Filter=Name.FlateDecode)
    return img


def _form(pdf, bbox=(0, 0, 100, 20)):
    """A small Form XObject that paints a black rectangle."""
    x0, y0, x1, y1 = bbox
    return Stream(pdf, f"0 g {x0} {y0} {x1 - x0} {y1 - y0} re f".encode(), Type=Name.XObject,
                  Subtype=Name.Form, BBox=Array(list(bbox)))


def _do(name, rect):
    x, y, w, h = rect
    return f"q {w} 0 0 {h} {x} {y} cm {name} Do Q\n"


def _wm_do(name, rect):
    x, y, w, h = rect
    return f"q /GS2 gs {w} 0 0 {h} {x} {y} cm {name} Do Q\n"


def _font(pdf):
    return Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)


def _text(lines, invisible, at=(72, 700)):
    out = ["BT", "3 Tr" if invisible else "0 Tr", "/F1 10 Tf", f"{at[0]} {at[1]} Td", "12 TL"]
    for line in lines:
        out.append("(" + line.replace("(", r"\(").replace(")", r"\)") + ") Tj T*")
    out.append("ET")
    return "\n".join(out).encode()


def make_pdf(path, *, body_pages=3, cover="first", cover_lines=NOTICE_LINES,
             cover_link=GOOGLE_URI, cover_qr=True, producer=GOOGLE_PRODUCER,
             wm_name="/Wm", wm_pages=None, wm_placement=WM_PLACEMENT, wm_height=204,
             wm_width=100, wm_stencil=True, wm_bpc=1, wm_kind="image",
             wm_extra_name=None, wm_shared=True, wm_group=False, scan_width=40, scan_placement=FULL_PAGE,
             body_text=None, extra_images=None):
    """Write a synthetic Google-Books-like PDF and return its path.

    cover:        "first", "last" or None — where the notice page goes.
    wm_pages:     body-page indices (0-based, cover excluded) that draw the watermark;
                  default all. None of the body pages draw it if wm_name is None.
    wm_kind:      "image" (Google-style /Wm) or "form" (a Form XObject).
    wm_group:     give a form watermark a /Group /S /Transparency entry.
    wm_shared:    False gives every page its own copy of the watermark object.
    wm_extra_name: also register the same watermark object under this second name on
                  every body page (used to test that one object found under two names).
    body_text:    {body_index: [lines]} invisible OCR text per page.
    extra_images: {body_index: [(name, width, height, rect)]} additional images.
    """
    pdf = Pdf.new()
    if producer is not None:
        pdf.docinfo[Name.Producer] = producer
    font = _font(pdf)
    gs = Dictionary(Type=Name.ExtGState, ca=0.3)

    def new_wm():
        if wm_kind == "form":
            form = _form(pdf)
            if wm_group:
                form.Group = Dictionary(S=Name.Transparency)
            return pdf.make_indirect(form)
        return pdf.make_indirect(_wm_image(pdf, wm_width, wm_height, wm_stencil, wm_bpc))

    wm_obj = new_wm() if wm_name is not None else None
    wm_pages = set(range(body_pages)) if wm_pages is None else set(wm_pages)
    body_text = body_text or {}
    extra_images = extra_images or {}

    def add_page(resources, streams, annots=None):
        page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, PAGE_W, PAGE_H]),
                          Resources=resources,
                          Contents=Array([pdf.make_indirect(Stream(pdf, s)) for s in streams]))
        if annots:
            page.Annots = Array(annots)
        pdf.pages.append(pikepdf.Page(page))

    def add_cover():
        xobjects = Dictionary()
        content = _text(cover_lines, invisible=False) + b"\n"
        if cover_qr:
            xobjects[Name("/FM:pod:QRCode")] = _gray_image(pdf, 29, 29)
            content += _do("/FM:pod:QRCode", (500, 700, 29, 29)).encode()
        annots = []
        if cover_link:
            annots.append(pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Link, Rect=Array([72, 640, 300, 652]),
                A=Dictionary(S=Name.URI, URI=pikepdf.String(cover_link)))))
        add_page(Dictionary(Font=Dictionary(F1=font), XObject=xobjects), [content], annots)

    if cover == "first":
        add_cover()
    for i in range(body_pages):
        xobjects = Dictionary(J2i0=_gray_image(pdf, scan_width, int(scan_width * 1.3)))
        image_stream = _do("/J2i0", scan_placement)
        for name, w, h, rect in extra_images.get(i, []):
            xobjects[Name(name)] = _gray_image(pdf, w, h)
            image_stream += _do(name, rect)
        if wm_obj is not None and i in wm_pages:
            if not wm_shared:
                wm_obj = new_wm()
            xobjects[Name(wm_name)] = wm_obj
            if wm_extra_name:
                xobjects[Name(wm_extra_name)] = wm_obj
            image_stream += _wm_do(wm_name, wm_placement)
        # As in real files: [scan + watermark stream, invisible OCR text stream]
        streams = [image_stream.encode(),
                   _text(body_text.get(i, [f"body page {i} text"]), invisible=True)]
        add_page(Dictionary(Font=Dictionary(F1=font), XObject=xobjects,
                            ExtGState=Dictionary(GS2=gs)), streams)
    if cover == "last":
        add_cover()
    pdf.save(path)
    return str(path)


@pytest.fixture
def cfg():
    import pdf_watermark_remover as pwr
    return pwr.load_config(CONFIG_PATH)


HATHI_PLACEMENT = (10, 10, 100, 20)
IA_NOTICE = ["Digitized by the Internet Archive", "in 2008 with funding from", "https://archive.org"]


HATHI_NOTICE = ["Find this Book Online: https://hdl.handle.net/2027/uc2.ark:/13960/t9k35pp92",
                "Public Domain in the United States",
                "Generated through HathiTrust on 2026-10-11 18:07 GMT"]
HATHI_LINKS = ["https://www.hathitrust.org/the-collection/search-access/access-use-policy/#pd-us",
               "https://hdl.handle.net/2027/uc2.ark:/13960/t9k35pp92"]


def make_hathi_pdf(path, *, pages=3, keys=("/PxCBA",), placement=HATHI_PLACEMENT, shared=True,
                   form_name="/CBJ", producer="Some PDF Library", scan_placement=FULL_PAGE,
                   cover=False, cover_lines=HATHI_NOTICE, cover_links=HATHI_LINKS, ocg="Watermark",
                   page_size=(PAGE_W, PAGE_H), scan_stripes=False):
    """HathiTrust-like PDF, mirroring real files ("I appeal unto Caesar", "The Hymody of ..."):

    every body page draws a full-page scan plus form /CBJ, whose resources hold the stamp
    image(s) and which sits on an optional-content layer named `ocg` (None: no layer).
    cover: prepend a notice page (text, links, and a layer-less form drawing the same stamps).
    page_size: body page size; real files use fractional sizes such as 314.03 x 508.
    """
    pdf = Pdf.new()
    pdf.docinfo[Name.Producer] = producer
    font = _font(pdf)
    layer = None
    if ocg:
        layer = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=pikepdf.String(ocg)))
        pdf.Root.OCProperties = Dictionary(OCGs=Array([layer]), D=Dictionary(ON=Array([layer])))

    def stamp():
        return pdf.make_indirect(_gray_image(pdf, 60, 12))

    def stamp_form(stamps, on_layer):
        form = pdf.make_indirect(Stream(pdf, "".join(_do(k, placement) for k in keys).encode(),
                                        Type=Name.XObject, Subtype=Name.Form,
                                        BBox=Array([0, 0, PAGE_W, PAGE_H]),
                                        Resources=Dictionary(XObject=Dictionary(dict(stamps)))))
        if on_layer and layer is not None:
            form.OC = Dictionary(Type=Name.OCMD, OCGs=layer)
        return form

    shared_stamps = {k: stamp() for k in keys}
    if cover:
        annots = [pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=Array([72, 700 - 20 * n, 400, 712 - 20 * n]),
            A=Dictionary(S=Name.URI, URI=pikepdf.String(uri)))) for n, uri in enumerate(cover_links)]
        page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, PAGE_W, PAGE_H]),
                          Resources=Dictionary(Font=Dictionary(F1=font),
                                               XObject=Dictionary(CFW=stamp_form(shared_stamps, False))),
                          Contents=Array([pdf.make_indirect(Stream(pdf, _text(cover_lines, False) + b"\n/CFW Do"))]))
        if annots:
            page.Annots = Array(annots)
        pdf.pages.append(pikepdf.Page(page))
    for i in range(pages):
        stamps = shared_stamps if shared else {k: stamp() for k in keys}
        xobjects = Dictionary(J2i0=_gray_image(pdf, 40, 52) if not scan_stripes
                              else _gray_image(pdf, 293, 477, stripes=scan_stripes))
        xobjects[Name(form_name)] = stamp_form(stamps, True)
        content = _do("/J2i0", scan_placement) + f"{form_name} Do\n"
        page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, *page_size]),
                          Resources=Dictionary(Font=Dictionary(F1=font), XObject=xobjects),
                          Contents=Array([pdf.make_indirect(Stream(pdf, content.encode())),
                                          pdf.make_indirect(Stream(pdf, _text([f"hathi page {i}"], True,
                                                                              at=(72, page_size[1] / 2))))]))
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(path)
    return str(path)


def make_ia_pdf(path, *, pages=6, promo_at=2, promo_lines=IA_NOTICE, signature=True,
                signature_length=8420, producer="Internet Archive PDF"):
    """Internet-Archive-like PDF; page `promo_at` has the notice text and /Im001 signature image."""
    pdf = Pdf.new()
    pdf.docinfo[Name.Producer] = producer
    font = _font(pdf)
    for i in range(pages):
        xobjects = Dictionary(J2i0=_gray_image(pdf, 40, 52))
        content = _do("/J2i0", FULL_PAGE).encode()
        streams = [content, _text([f"ia page {i}"], True)]
        if i == promo_at:
            xobjects = Dictionary()
            streams = [_text(promo_lines, invisible=False)]
            if signature:
                w = 20
                raw = bytes(range(256)) * (signature_length // 256) + b"\x80" * (signature_length % 256)
                xobjects.Im001 = Stream(pdf, raw, Type=Name.XObject, Subtype=Name.Image, Width=w,
                                        Height=signature_length // w, ColorSpace=Name.DeviceGray,
                                        BitsPerComponent=8)
                streams.append(_do("/Im001", (400, 600, 40, 40)).encode())
        page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, PAGE_W, PAGE_H]),
                          Resources=Dictionary(Font=Dictionary(F1=font), XObject=xobjects),
                          Contents=Array([pdf.make_indirect(Stream(pdf, s)) for s in streams]))
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(path, compress_streams=False)  # keep /Im001's stored length exact
    return str(path)


SCAN_DPI = 144  # scan image resolution for make_scanned_pdf (2 px per point)


def scan_with_stamp(stamp_text=None, stamp_at=(380, 60), font_scale=2.0, body=True):
    """Gray page-sized scan (numpy, SCAN_DPI) with body text lines and an optional baked-in stamp.

    stamp_at is the stamp's bottom-left in PDF points from the top-left corner of the page.
    """
    import cv2
    import numpy as np
    s = SCAN_DPI / 72
    img = np.full((int(PAGE_H * s), int(PAGE_W * s)), 255, dtype=np.uint8)
    if body:
        for n, line in enumerate(["The history of the parish church", "and its ministers in the city"]):
            cv2.putText(img, line, (int(72 * s), int((200 + 40 * n) * s)), cv2.FONT_HERSHEY_SIMPLEX,
                        1.6, 0, 3, cv2.LINE_AA)
    if stamp_text:
        x, y = stamp_at
        cv2.putText(img, stamp_text, (int(x * s), int(y * s)), cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale, 0, 4, cv2.LINE_AA)
    return img


def make_scanned_pdf(path, *, pages=2, stamp_text="CONFIDENTIAL", stamp_pages=None, text_layer=None,
                     producer="Some Scanner", colorspace="gray", **stamp_kwargs):
    """Image-only PDF: each page is one full-page 8-bit gray scan, optionally with a baked stamp.

    text_layer: invisible text drawn over the stamp position, as an OCR layer would be.
    colorspace: "gray", "rgb" or "cmyk" for the scan image.
    """
    import numpy as np
    pdf = Pdf.new()
    pdf.docinfo[Name.Producer] = producer
    stamp_pages = set(range(pages)) if stamp_pages is None else set(stamp_pages)
    for i in range(pages):
        img = scan_with_stamp(stamp_text if i in stamp_pages else None, **stamp_kwargs)
        h, w = img.shape
        if colorspace == "rgb":
            data, cs = np.dstack([img, img, img]).tobytes(), Name.DeviceRGB
        elif colorspace == "cmyk":
            k = 255 - img
            zero = np.zeros_like(img)
            data, cs = np.dstack([zero, zero, zero, k]).tobytes(), Name.DeviceCMYK
        else:
            data, cs = img.tobytes(), Name.DeviceGray
        image = Stream(pdf, zlib.compress(data), Type=Name.XObject, Subtype=Name.Image,
                       Width=w, Height=h, ColorSpace=cs, BitsPerComponent=8, Filter=Name.FlateDecode)
        streams = [_do("/Scan", FULL_PAGE).encode()]
        resources = Dictionary(XObject=Dictionary(Scan=image), Font=Dictionary(F1=_font(pdf)))
        if text_layer and i in stamp_pages:
            # Like a real OCR layer, stretch the invisible text to the baked stamp's width.
            import cv2
            import pymupdf
            x, y = stamp_kwargs.get("stamp_at", (380, 60))
            scale = stamp_kwargs.get("font_scale", 2.0)
            baked_pt = cv2.getTextSize(stamp_text, cv2.FONT_HERSHEY_SIMPLEX, scale, 4)[0][0] * 72 / SCAN_DPI
            tz = 100 * baked_pt / pymupdf.get_text_length(text_layer, fontname="helv", fontsize=24)
            streams.append(f"BT 3 Tr /F1 24 Tf {tz:.1f} Tz {x} {PAGE_H - y} Td ({text_layer}) Tj ET".encode())
        page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, PAGE_W, PAGE_H]), Resources=resources,
                          Contents=Array([pdf.make_indirect(Stream(pdf, s)) for s in streams]))
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(path)
    return str(path)
