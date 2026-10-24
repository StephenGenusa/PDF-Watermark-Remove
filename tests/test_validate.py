import pikepdf
import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import WM_PLACEMENT, make_hathi_pdf, make_pdf

x, y, w, h = WM_PLACEMENT
WM_RECTS = {i: [(x, y, x + w, y + h)] for i in (1, 2, 3)}


def build_output(src, dst, neutralize=("/Wm",), delete_pages=(0,), edit=None):
    """Produce an output file the way the pipeline would, optionally sabotaged by `edit`."""
    tmp = str(dst) + ".stage"
    with pikepdf.open(src) as pdf:
        for name in neutralize:
            pwr.neutralize_xobject(pdf, pdf.pages[1].Resources.XObject[name].objgen)
        pdf.save(tmp)
    doc = pymupdf.open(tmp)
    doc.delete_pages(list(delete_pages))
    if edit:
        edit(doc)
    doc.save(dst)
    return str(dst)


def test_correct_output_has_no_problems(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    out = build_output(src, tmp_path / "b.pdf")
    assert pwr.validate_output(src, out, [0], WM_RECTS, cfg) == []


def test_blanked_page_scans_are_reported(tmp_path, cfg):
    # The Aberdeen failure: page scans removed, invisible OCR text left behind.
    src = make_pdf(tmp_path / "a.pdf", body_pages=3, wm_name=None)
    tmp = tmp_path / "scanless.pdf"
    with pikepdf.open(src) as pdf:
        for page in list(pdf.pages)[1:]:
            pwr.neutralize_xobject(pdf, page.Resources.XObject["/J2i0"].objgen)
        pdf.save(tmp)
    out = build_output(tmp, tmp_path / "b.pdf", neutralize=())
    problems = pwr.validate_output(src, out, [0], {}, cfg)
    assert sum("blank" in p for p in problems) == 3


def test_changed_text_is_reported(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")

    def edit(doc):
        doc[1].insert_text((72, 300), "inserted words", render_mode=3)

    out = build_output(src, tmp_path / "b.pdf", edit=edit)
    problems = pwr.validate_output(src, out, [0], WM_RECTS, cfg)
    assert problems == ["text differs on page 3"]  # 1-based, original numbering


def test_wrong_page_count_is_reported(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    out = build_output(src, tmp_path / "b.pdf", delete_pages=(0, 1))
    problems = pwr.validate_output(src, out, [0], WM_RECTS, cfg)
    assert problems == ["expected 3 pages, found 2"]


def test_visible_change_outside_watermark_is_reported(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")

    def edit(doc):
        doc[0].draw_rect(pymupdf.Rect(100, 100, 300, 300), color=(0, 0, 0), fill=(0, 0, 0))

    out = build_output(src, tmp_path / "b.pdf", edit=edit)
    problems = pwr.validate_output(src, out, [0], WM_RECTS, cfg)
    assert len(problems) == 1 and problems[0].startswith("page 2 rendering changed")


def test_watermark_removal_without_its_rects_is_reported(tmp_path, cfg):
    # The diff is only excused inside rects of watermarks that were actually removed.
    src = make_pdf(tmp_path / "a.pdf", wm_placement=(100, 300, 200, 200))
    out = build_output(src, tmp_path / "b.pdf")
    problems = pwr.validate_output(src, out, [0], {}, cfg)
    assert [p.split(" rendering")[0] for p in problems] == ["page 2", "page 3", "page 4"]


def test_blank_check_can_be_disabled(tmp_path, cfg):
    cfg["safety"]["fail_on_blank_page"] = False
    cfg["safety"]["max_render_diff"] = 255
    src = make_pdf(tmp_path / "a.pdf", body_pages=1, wm_name=None)
    tmp = tmp_path / "scanless.pdf"
    with pikepdf.open(src) as pdf:
        pwr.neutralize_xobject(pdf, pdf.pages[1].Resources.XObject["/J2i0"].objgen)
        pdf.save(tmp)
    out = build_output(tmp, tmp_path / "b.pdf", neutralize=())
    assert pwr.validate_output(src, out, [0], {}, cfg) == []


def test_structural_damage_is_reported(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    out = build_output(src, tmp_path / "b.pdf")
    with pikepdf.open(out, allow_overwriting_input=True) as pdf:
        broken = pikepdf.Stream(pdf, b"this is not zlib data")
        broken.Filter = pikepdf.Name.FlateDecode
        pdf.Root.Broken = pdf.make_indirect(broken)
        pdf.save(out, compress_streams=False)
    problems = pwr.validate_output(src, out, [0], WM_RECTS, cfg)
    assert problems and all(p.startswith("structure:") for p in problems)


def test_unreadable_output_is_reported(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    out = tmp_path / "b.pdf"
    out.write_bytes(b"not a pdf")
    problems = pwr.validate_output(src, str(out), [0], WM_RECTS, cfg)
    assert len(problems) == 1 and problems[0].startswith("output unreadable")


# ---- cropped output ----


def hathi_output(tmp_path, crop_pdf, page_size=(612, 792), scan=(21, 31, 591, 761), stripes=False):
    """Original HathiTrust-like file, and an output with stamps emptied and the given crop box."""
    src = make_hathi_pdf(tmp_path / "h.pdf", pages=2, scan_placement=scan, placement=(40, 5, 150, 20),
                         page_size=page_size, scan_stripes=stripes)
    with pikepdf.open(src) as pdf:
        found = pwr.find_hathitrust_watermarks(pdf, pwr.load_config(pwr.DEFAULT_CONFIG_PATH)).found
        for wm in found:
            pwr.neutralize_xobject(pdf, wm.objgen)
        pdf.save(tmp_path / "stage.pdf")
    doc = pymupdf.open(tmp_path / "stage.pdf")
    x0, y0, x1, y1 = crop_pdf
    for page in doc:
        height = page.mediabox.height
        page.set_cropbox(pymupdf.Rect(x0, height - y1, x1, height - y0) & page.mediabox)
    doc.save(tmp_path / "out.pdf")
    rects = {}
    for wm in found:
        for page, page_rects in wm.rects.items():
            rects.setdefault(page, []).extend(page_rects)
    return src, str(tmp_path / "out.pdf"), rects


def test_crop_to_the_scan_validates(tmp_path, cfg):
    src, out, rects = hathi_output(tmp_path, (21, 31, 612, 792))
    assert pwr.validate_output(src, out, [], rects, cfg) == []


def test_crop_that_hides_scan_content_is_reported(tmp_path, cfg):
    # Cuts off the right half of the scan, where its dark band is.
    src, out, rects = hathi_output(tmp_path, (21, 31, 300, 792))
    problems = pwr.validate_output(src, out, [], rects, cfg)
    assert "page 1 crop hides content" in problems
    assert "page 2 crop hides content" in problems


def test_crop_with_fractional_coordinates_validates(tmp_path, cfg):
    # "I appeal unto Caesar": 314.03 x 508 pages, scan from x = 21.029. The clipped original and
    # the cropped output render one pixel apart in width.
    src, out, rects = hathi_output(tmp_path, (21.029, 31, 314.03, 508), page_size=(314.03, 508),
                                   scan=(21.029, 31, 293.001, 477))
    assert pwr.validate_output(src, out, [], rects, cfg) == []


def test_fractional_crop_of_a_detailed_scan_validates(tmp_path, cfg):
    # Rendering the clipped original and the cropped output puts them on pixel grids a fraction
    # of a pixel apart; on a detailed scan that alone looked like a rendering change.
    src, out, rects = hathi_output(tmp_path, (21.029, 31, 314.03, 508), page_size=(314.03, 508),
                                   scan=(21.029, 31, 293.001, 477), stripes=3)
    assert pwr.validate_output(src, out, [], rects, cfg) == []


def test_crop_that_hides_invisible_text_is_reported(tmp_path, cfg):
    src, out, rects = hathi_output(tmp_path, (21, 31, 612, 792))
    doc = pymupdf.open(src)
    for page in doc:
        page.insert_text((3, 700), "margin", fontsize=6, render_mode=3)
    doc.save(tmp_path / "h2.pdf")
    out2 = pymupdf.open(out)
    for page in out2:
        crop = page.cropbox
        page.set_cropbox(page.mediabox)
        page.insert_text((3, 700), "margin", fontsize=6, render_mode=3)
        page.set_cropbox(crop)
    out2.save(tmp_path / "out2.pdf")
    problems = pwr.validate_output(str(tmp_path / "h2.pdf"), str(tmp_path / "out2.pdf"), [], rects, cfg)
    assert "page 1 crop hides text" in problems


def test_word_past_the_page_edge_is_not_hidden_by_the_crop(tmp_path, cfg):
    src, out, rects = hathi_output(tmp_path, (21, 31, 612, 792))
    for path in (src, out):
        doc = pymupdf.open(path)
        for page in doc:
            crop = page.cropbox
            page.set_cropbox(page.mediabox)
            page.insert_text((100, 8), "......", fontsize=24, render_mode=3)
            page.set_cropbox(crop)
        doc.save(path + ".2.pdf")
    assert pwr.validate_output(src + ".2.pdf", out + ".2.pdf", [], rects, cfg) == []
