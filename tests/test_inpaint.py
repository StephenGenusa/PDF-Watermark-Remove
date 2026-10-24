import numpy as np
import pikepdf
import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import make_pdf, make_scanned_pdf

# make_scanned_pdf's stamp: "CONFIDENTIAL" at x=380, baseline 60 pt from the top.
STAMP_BOX_PDF = (375, 725, 600, 760)


def render(doc, i, dpi=72):
    pix = doc[i].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()


def box_slice(box, height=792, margin=3):
    x0, y0, x1, y1 = box
    return slice(int(height - y1) - margin, int(height - y0) + margin), slice(int(x0) - margin, int(x1) + margin)


def test_stamp_pixels_are_erased_and_nothing_else_changes(tmp_path, cfg):
    doc = pymupdf.open(make_scanned_pdf(tmp_path / "s.pdf", pages=1))
    before = render(doc, 0)
    rows, cols = box_slice(STAMP_BOX_PDF)
    assert before[rows, cols].min() < 100  # stamp ink present

    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) is None
    after = render(doc, 0)
    assert after[rows, cols].min() > 200  # stamp gone
    outside = np.ones(before.shape, dtype=bool)
    outside[rows, cols] = False
    assert np.array_equal(before[outside], after[outside])


def test_text_layer_is_left_alone(tmp_path, cfg):
    doc = pymupdf.open(make_scanned_pdf(tmp_path / "s.pdf", pages=1, text_layer="CONFIDENTIAL"))
    text = doc[0].get_text()
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) is None
    assert doc[0].get_text() == text


def test_no_image_under_stamp_is_refused(tmp_path, cfg):
    doc = pymupdf.open(make_pdf(tmp_path / "a.pdf", wm_name=None, scan_placement=(0, 0, 100, 100)))
    assert pwr.inpaint_stamp(doc, 1, (300, 300, 400, 320), cfg, pwr.image_usage(doc)) == \
        "no image under the stamp"


def test_rotated_image_is_refused(tmp_path, cfg):
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=1)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Contents[0].write(b"q 0 792 -612 0 612 0 cm /Scan Do Q")
        pdf.save(path)
    doc = pymupdf.open(path)
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) == \
        "image under the stamp is rotated or flipped"


def test_image_shared_by_several_pages_is_refused(tmp_path, cfg):
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=2)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[1].Resources.XObject.Scan = pdf.pages[0].Resources.XObject.Scan
        pdf.save(path)
    doc = pymupdf.open(path)
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) == \
        "image under the stamp is shared by several pages"


def test_image_with_soft_mask_is_refused(tmp_path, cfg):
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=1)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        scan = pdf.pages[0].Resources.XObject.Scan
        scan.SMask = pdf.make_indirect(pikepdf.Stream(pdf, b"\xff" * 4, Type=pikepdf.Name.XObject,
                                                      Subtype=pikepdf.Name.Image, Width=2, Height=2,
                                                      ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8))
        pdf.save(path)
    doc = pymupdf.open(path)
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) == \
        "image under the stamp has a soft mask"


def test_image_usage_counts_pages_per_image(tmp_path):
    doc = pymupdf.open(make_pdf(tmp_path / "a.pdf", body_pages=3))
    usage = pwr.image_usage(doc)
    wm_xref = next(i[0] for i in doc[1].get_images(full=True) if i[7] == "Wm")
    scan_xref = next(i[0] for i in doc[1].get_images(full=True) if i[7] == "J2i0")
    assert usage[wm_xref] == 3
    assert usage[scan_xref] == 1


@pytest.mark.parametrize("colorspace", ["rgb", "cmyk"])
def test_colour_scans_are_inpainted(tmp_path, cfg, colorspace):
    doc = pymupdf.open(make_scanned_pdf(tmp_path / "s.pdf", pages=1, colorspace=colorspace))
    before = render(doc, 0)
    rows, cols = box_slice(STAMP_BOX_PDF)
    assert before[rows, cols].min() < 100
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) is None
    after = render(doc, 0)
    assert after[rows, cols].min() > 200
    outside = np.ones(before.shape, dtype=bool)
    outside[rows, cols] = False
    # CMYK is converted to RGB, so allow tiny colour-conversion differences outside the box.
    assert np.abs(before[outside].astype(int) - after[outside].astype(int)).max() <= 3


def test_image_with_colour_key_mask_is_refused(tmp_path, cfg):
    # Dropping the mask would turn transparent areas opaque and could hide content below.
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=1)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Resources.XObject.Scan.Mask = pikepdf.Array([250, 255])
        pdf.save(path)
    doc = pymupdf.open(path)
    assert pwr.inpaint_stamp(doc, 0, STAMP_BOX_PDF, cfg, pwr.image_usage(doc)) == \
        "image under the stamp has a mask"
