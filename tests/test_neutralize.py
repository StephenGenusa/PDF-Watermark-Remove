import pikepdf
import pymupdf

import pdf_watermark_remover as pwr
from conftest import make_pdf

# Watermark drawn at x 273-339, y 10-23 (PDF space); scan ink is in the page's middle band.
WM_CENTER_PDF = (306, 16)


def pixel_at_watermark(path, page_index=1, dpi=72):
    page = pymupdf.open(path)[page_index]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    x, y = WM_CENTER_PDF
    return pix.pixel(x, int(page.rect.height) - y)[0]


def neutralize_all(src, dst, name):
    with pikepdf.open(src) as pdf:
        objgen = pdf.pages[1].Resources.XObject[name].objgen
        before = [bytes(c.read_bytes()) for p in pdf.pages for c in p.Contents]
        pwr.neutralize_xobject(pdf, objgen)
        pdf.save(dst)
    return objgen, before


def test_image_watermark_no_longer_renders(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    assert pixel_at_watermark(src) < 200  # 30% black overlay is visible before
    neutralize_all(src, tmp_path / "b.pdf", "/Wm")
    assert pixel_at_watermark(tmp_path / "b.pdf") == 255


def test_form_watermark_no_longer_renders(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", wm_name="/Form0", wm_kind="form", wm_placement=(273, 10, 1, 1))
    assert pixel_at_watermark(src) < 200
    neutralize_all(src, tmp_path / "b.pdf", "/Form0")
    assert pixel_at_watermark(tmp_path / "b.pdf") == 255


def test_page_content_streams_are_byte_identical(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    _, before = neutralize_all(src, tmp_path / "b.pdf", "/Wm")
    with pikepdf.open(tmp_path / "b.pdf") as pdf:
        after = [bytes(c.read_bytes()) for p in pdf.pages for c in p.Contents]
    assert after == before


def test_neutralized_object_is_a_valid_empty_form(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    objgen, _ = neutralize_all(src, tmp_path / "b.pdf", "/Wm")
    with pikepdf.open(tmp_path / "b.pdf") as pdf:
        obj = pdf.pages[1].Resources.XObject["/Wm"]
        assert obj.Subtype == "/Form"
        assert list(obj.BBox) == [0, 0, 0, 0]
        assert obj.read_bytes() == b""
        for key in ("/Width", "/Height", "/BitsPerComponent", "/ColorSpace", "/Mask", "/DecodeParms"):
            assert key not in obj
        # Still one object shared by every page.
        assert {p.Resources.XObject["/Wm"].objgen for p in list(pdf.pages)[1:]} == {objgen}
