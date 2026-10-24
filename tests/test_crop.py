import pikepdf
import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import make_hathi_pdf, make_pdf

SCAN = (21, 31, 591, 761)          # x, y, w, h: margins 21 pt left, 31 pt bottom, as in real files
STAMP = (40, 5, 150, 20)           # in the bottom margin
SCAN_PDF = (21, 31, 612, 792)


def staged(tmp_path, *, scan=SCAN, stamp=STAMP, edit=None, **kwargs):
    """HathiTrust-like page with its stamps already emptied, as the pipeline stages it.

    Returns (pymupdf page, removed stamp rects in PDF space).
    """
    path = make_hathi_pdf(tmp_path / "h.pdf", pages=1, scan_placement=scan, placement=stamp, **kwargs)
    with pikepdf.open(path) as pdf:
        found = pwr.find_hathitrust_watermarks(pdf, pwr.load_config(pwr.DEFAULT_CONFIG_PATH)).found
        for w in found:
            pwr.neutralize_xobject(pdf, w.objgen)
        if edit:
            edit(pdf)
        pdf.save(tmp_path / "staged.pdf")
    rects = [r for w in found for r in w.rects.get(0, [])]
    return pymupdf.open(tmp_path / "staged.pdf")[0], rects


def test_crop_is_the_scan_rectangle(tmp_path, cfg):
    page, removed = staged(tmp_path)
    assert pwr.margin_crop(page, removed, cfg) == (SCAN_PDF, None)


def test_watermark_overlapping_the_scan_prevents_crop(tmp_path, cfg):
    # Google-style: the watermark sits on top of the scan, so there is no vacated margin.
    page, removed = staged(tmp_path, stamp=(100, 100, 100, 20))
    assert pwr.margin_crop(page, removed, cfg) == (None, "a removed watermark overlapped the scan")


def test_second_image_prevents_crop(tmp_path, cfg):
    def add_image(pdf):
        page = pdf.pages[0]
        page.Resources.XObject.Extra = page.Resources.XObject.J2i0
        page.Contents.append(pdf.make_indirect(pikepdf.Stream(pdf, b"q 10 0 0 10 300 300 cm /Extra Do Q")))
    page, removed = staged(tmp_path, edit=add_image)
    assert pwr.margin_crop(page, removed, cfg) == (None, "page does not draw exactly one image")


@pytest.mark.parametrize("render_mode", [0, 3])  # visible text, invisible (OCR layer) text
def test_text_in_the_margin_prevents_crop(tmp_path, cfg, render_mode):
    page, removed = staged(tmp_path)
    page.insert_text((2, 785), "margin note", fontsize=6, render_mode=render_mode)
    assert pwr.margin_crop(page, removed, cfg) == (None, "content outside the scan")


def test_margin_larger_than_limit_prevents_crop(tmp_path, cfg):
    # Bottom margin 200 pt = 25% of the page height, above the 10% limit.
    page, removed = staged(tmp_path, scan=(0, 200, 612, 592), stamp=(40, 50, 150, 20))
    assert pwr.margin_crop(page, removed, cfg) == (None, "margin too large to be a watermark strip")


@pytest.mark.parametrize("change", ["cropbox", "rotation"])
def test_existing_cropbox_or_rotation_prevents_crop(tmp_path, cfg, change):
    page, removed = staged(tmp_path)
    if change == "cropbox":
        page.set_cropbox(pymupdf.Rect(0, 0, 600, 780))
    else:
        page.set_rotation(90)
    assert pwr.margin_crop(page, removed, cfg) == (None, "page already has a crop box or rotation")


def test_scan_filling_the_page_needs_no_crop(tmp_path, cfg):
    page = pymupdf.open(make_pdf(tmp_path / "g.pdf", cover=None, wm_name=None))[0]
    assert pwr.margin_crop(page, [], cfg) == (None, None)


def test_watermark_mostly_outside_the_scan_still_allows_crop(tmp_path, cfg):
    # "The Hymody of Christadelphians": the margin strip overlaps the scan's edge by ~10% of its width.
    page, removed = staged(tmp_path, stamp=(3, 40, 20, 200))  # x 3-23; scan starts at 21
    assert pwr.margin_crop(page, removed, cfg) == (SCAN_PDF, None)


def test_watermark_mostly_inside_the_scan_prevents_crop(tmp_path, cfg):
    page, removed = staged(tmp_path, stamp=(15, 40, 20, 200))  # x 15-35: 70% inside the scan
    assert pwr.margin_crop(page, removed, cfg) == (None, "a removed watermark overlapped the scan")


def test_text_run_whose_words_are_all_inside_the_scan_allows_crop(tmp_path, cfg):
    # "I appeal unto Caesar" p.115: an OCR text run's box starts in the margin, but no word does.
    page, removed = staged(tmp_path)
    page.insert_text((5, 400), "        inside", fontsize=10, render_mode=3)  # 8 spaces ~ 22 pt
    assert pwr.margin_crop(page, removed, cfg) == (SCAN_PDF, None)


def test_word_box_sticking_out_past_the_page_edge_allows_crop(tmp_path, cfg):
    # "I appeal unto Caesar" pp. 2, 43, 81: junk OCR words whose boxes extend above the page top.
    page, removed = staged(tmp_path)
    page.insert_text((100, 8), "......", fontsize=24, render_mode=3)  # glyph box reaches y < 0
    assert any(w[1] < 0 for w in page.get_text("words"))
    assert pwr.margin_crop(page, removed, cfg) == (SCAN_PDF, None)
