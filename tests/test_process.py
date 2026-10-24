import os

import pikepdf
import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import make_hathi_pdf, make_ia_pdf, make_pdf, make_scanned_pdf


def darkest_in(path, page_index, box):
    """Darkest gray value inside a PDF-space box (x0, y0, x1, y1)."""
    page = pymupdf.open(path)[page_index]
    pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY)
    h = int(page.rect.height)
    x0, y0, x1, y1 = box
    return min(pix.pixel(x, y)[0] for x in range(int(x0), int(x1)) for y in range(h - int(y1), h - int(y0)))


def gray_at(path, page_index, x, y):
    page = pymupdf.open(path)[page_index]
    pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY)
    return pix.pixel(x, int(page.rect.height) - y)[0]


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_google_file_is_cleaned(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", body_pages=3)
    out = str(out_dir / "a.pdf")
    result = pwr.process_pdf(src, out, cfg)
    assert result.status == "cleaned"
    assert result.promo_pages == [0]
    assert result.removed == ["/Wm"]
    doc = pymupdf.open(out)
    assert len(doc) == 3
    assert doc[0].get_text() == "body page 0 text\n"
    assert gray_at(src, 1, 306, 16) < 200   # watermark visible in the original
    assert gray_at(out, 0, 306, 16) == 255  # and gone from the output
    assert os.listdir(out_dir) == ["a.pdf"]


def test_file_with_nothing_matching_is_skipped_and_not_written(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", producer="Adobe Acrobat", cover=None, wm_name=None, scan_width=41)
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "skipped"
    assert result.reasons == ["nothing to remove"]
    assert os.listdir(out_dir) == []


def test_google_file_with_nothing_to_remove_is_skipped(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", cover=None, wm_name=None)
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "skipped"
    assert os.listdir(out_dir) == []


def test_cover_removed_even_without_watermark(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", wm_name=None)
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "cleaned"
    assert result.removed == []
    assert len(pymupdf.open(out_dir / "a.pdf")) == 3


def test_watermark_removed_even_without_cover(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", cover=None)
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "cleaned"
    assert result.promo_pages == []
    assert len(pymupdf.open(out_dir / "a.pdf")) == 3


def test_too_many_promo_pages_fails_without_output(tmp_path, out_dir, cfg):
    cfg["safety"]["max_promo_pages"] = 0
    src = make_pdf(tmp_path / "a.pdf")
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "failed"
    assert "promotional" in result.reasons[0]
    assert os.listdir(out_dir) == []


def test_validation_failure_writes_nothing(tmp_path, out_dir, cfg, monkeypatch):
    # Simulate a remover bug that also wipes the page scans (the Aberdeen failure).
    real = pwr.neutralize_xobject

    def buggy(pdf, objgen):
        real(pdf, objgen)
        for page in list(pdf.pages)[1:]:
            real(pdf, page.Resources.XObject["/J2i0"].objgen)

    monkeypatch.setattr(pwr, "neutralize_xobject", buggy)
    src = make_pdf(tmp_path / "a.pdf")
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.status == "failed"
    assert any("went blank" in r for r in result.reasons)
    assert os.listdir(out_dir) == []


def test_dry_run_reports_plan_and_writes_nothing(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg, dry_run=True)
    assert result.status == "dry-run"
    assert result.promo_pages == [0]
    assert result.removed == ["/Wm"]
    assert os.listdir(out_dir) == []


def test_suspects_and_rejections_are_reported(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", cover=None, wm_height=200,
                   body_text={0: ["this is a digital copy of a book"]})
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.suspects == [0]
    assert result.rejected[0][0] == "/Wm"


def test_unreadable_input_fails(tmp_path, out_dir, cfg):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"not a pdf at all")
    result = pwr.process_pdf(str(src), str(out_dir / "a.pdf"), cfg)
    assert result.status == "failed"
    assert os.listdir(out_dir) == []


def test_output_path_equal_to_input_is_refused(tmp_path, cfg):
    src = make_pdf(tmp_path / "a.pdf")
    before = open(src, "rb").read()
    with pytest.raises(ValueError):
        pwr.process_pdf(src, str(tmp_path / "." / "a.pdf"), cfg)
    assert open(src, "rb").read() == before


# ---- other services, generic rules and stamps ----


def test_hathitrust_stamp_removed_and_scan_kept(tmp_path, out_dir, cfg):
    src = make_hathi_pdf(tmp_path / "h.pdf", pages=3)
    out = str(out_dir / "h.pdf")
    result = pwr.process_pdf(src, out, cfg)
    assert (result.status, result.service, result.removed) == ("cleaned", "hathitrust", ["/PxCBA"])
    # The stamp's ink band (rows 1/4-1/2, inset 1/8 each side) at placement (10, 10, 100, 20).
    ink = (23, 20, 97, 25)
    assert darkest_in(src, 0, ink) < 100    # stamp ink in the original
    assert darkest_in(out, 0, ink) == 255   # gone
    assert gray_at(out, 0, 300, 500) < 100  # scan's dark band still there


def test_internet_archive_promo_page_removed(tmp_path, out_dir, cfg):
    src = make_ia_pdf(tmp_path / "i.pdf", pages=6)
    result = pwr.process_pdf(src, str(out_dir / "i.pdf"), cfg)
    assert (result.status, result.service, result.promo_pages) == ("cleaned", "internet_archive", [2])
    assert len(pymupdf.open(out_dir / "i.pdf")) == 5


def test_unknown_file_gets_generic_rules(tmp_path, out_dir, cfg):
    src = make_pdf(tmp_path / "a.pdf", producer="Some Scanner", cover=None, wm_name="/Form0",
                   wm_kind="form", wm_placement=(273, 10, 1, 1))
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert (result.status, result.service, result.removed) == ("cleaned", None, ["/Form0"])


def test_generic_rules_are_not_applied_to_google_files(tmp_path, out_dir, cfg):
    # A 156-wide image would match the generic fixed-width rule; Google files use fingerprints only.
    src = make_pdf(tmp_path / "a.pdf", extra_images={1: [("/CTi5", 156, 180, (100, 100, 78, 90))]})
    result = pwr.process_pdf(src, str(out_dir / "a.pdf"), cfg)
    assert result.removed == ["/Wm"]


def test_repeated_baked_stamp_is_inpainted(tmp_path, out_dir, cfg):
    src = make_scanned_pdf(tmp_path / "s.pdf", pages=2, text_layer="CONFIDENTIAL")
    out = str(out_dir / "s.pdf")
    result = pwr.process_pdf(src, out, cfg)
    assert (result.status, result.reasons) == ("cleaned", [])
    assert result.stamps_removed == ["page 1: confidential", "page 2: confidential"]
    stamp_box = (375, 725, 600, 760)
    assert darkest_in(src, 0, stamp_box) < 100
    assert darkest_in(out, 0, stamp_box) > 200
    assert pymupdf.open(out)[0].get_text() == pymupdf.open(src)[0].get_text()


def test_stamp_that_cannot_be_inpainted_is_noted_not_fatal(tmp_path, out_dir, cfg):
    # Visible vector text "SAMPLE" on every page, over no image: nothing to inpaint.
    path = make_pdf(tmp_path / "a.pdf", producer="Some Scanner", cover=None, wm_name="/Form0",
                    wm_kind="form", wm_placement=(273, 10, 1, 1), scan_placement=(0, 0, 100, 100),
                    body_text={i: ["SAMPLE"] for i in range(3)})
    result = pwr.process_pdf(path, str(out_dir / "a.pdf"), cfg)
    assert result.status == "cleaned"
    assert result.stamps_removed == []
    assert result.notes[0] == "page 1: sample not removed: no image under the stamp"


def test_dry_run_lists_planned_stamp_removals(tmp_path, out_dir, cfg):
    src = make_scanned_pdf(tmp_path / "s.pdf", pages=2, text_layer="CONFIDENTIAL")
    result = pwr.process_pdf(src, str(out_dir / "s.pdf"), cfg, dry_run=True)
    assert result.status == "dry-run"
    assert result.stamps_removed == ["page 1: confidential", "page 2: confidential"]
    assert os.listdir(out_dir) == []


def test_unrepeated_stamp_is_reported(tmp_path, out_dir, cfg):
    src = make_scanned_pdf(tmp_path / "s.pdf", pages=4, stamp_pages=[1], text_layer="CONFIDENTIAL")
    result = pwr.process_pdf(src, str(out_dir / "s.pdf"), cfg, dry_run=True)
    assert result.stamps_reported == ["page 2: confidential"]


def test_hathitrust_notice_page_and_layer_stamps_removed(tmp_path, out_dir, cfg):
    src = make_hathi_pdf(tmp_path / "h.pdf", pages=3, cover=True, keys=("/PxCBA", "/PxCBB", "/PxCBC"),
                         producer="HathiTrust Image Server / PDF::API2")
    out = str(out_dir / "h.pdf")
    result = pwr.process_pdf(src, out, cfg)
    assert (result.status, result.reasons, result.promo_pages) == ("cleaned", [], [0])
    assert sorted(result.removed) == ["/PxCBA", "/PxCBB", "/PxCBC"]
    doc = pymupdf.open(out)
    assert len(doc) == 3
    assert all(any(img[7] == "J2i0" for img in page.get_images()) for page in doc)


# ---- trimming vacated margins ----
def hathi_with_margins(tmp_path):
    return make_hathi_pdf(tmp_path / "h.pdf", pages=2, cover=True, scan_placement=(21, 31, 591, 761),
                          placement=(40, 5, 150, 20), producer="HathiTrust Image Server / PDF::API2")


def test_vacated_margins_are_cropped_to_the_scan(tmp_path, out_dir, cfg):
    out = str(out_dir / "h.pdf")
    result = pwr.process_pdf(hathi_with_margins(tmp_path), out, cfg)
    assert (result.status, result.reasons) == ("cleaned", [])
    assert result.cropped_pages == [1, 2]  # original page indices; the notice page 0 is deleted
    with pikepdf.open(out) as pdf:
        assert [[float(v) for v in p.obj.CropBox] for p in pdf.pages] == [[21, 31, 612, 792]] * 2
        assert [[float(v) for v in p.mediabox] for p in pdf.pages] == [[0, 0, 612, 792]] * 2


def test_margin_trimming_can_be_switched_off(tmp_path, out_dir, cfg):
    cfg["processing"]["trim_vacated_margins"] = False
    out = str(out_dir / "h.pdf")
    result = pwr.process_pdf(hathi_with_margins(tmp_path), out, cfg)
    assert result.cropped_pages == []
    with pikepdf.open(out) as pdf:
        assert all("/CropBox" not in p.obj for p in pdf.pages)


def test_google_pages_are_not_cropped(tmp_path, out_dir, cfg):
    result = pwr.process_pdf(make_pdf(tmp_path / "a.pdf"), str(out_dir / "a.pdf"), cfg)
    assert result.status == "cleaned"
    assert result.cropped_pages == []


def test_crop_to_a_fractional_page_edge_succeeds(tmp_path, out_dir, cfg):
    # The scan reaches the right edge of a 314.03 pt page; float rounding must not push the
    # crop box outside the media box.
    src = make_hathi_pdf(tmp_path / "h.pdf", pages=2, cover=True, page_size=(314.03, 508),
                         scan_placement=(21.029, 31, 293.001, 477), placement=(40, 5, 100, 20),
                         producer="HathiTrust Image Server / PDF::API2")
    result = pwr.process_pdf(src, str(out_dir / "h.pdf"), cfg)
    assert (result.status, result.reasons, result.cropped_pages) == ("cleaned", [], [1, 2])
