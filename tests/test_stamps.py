import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import make_pdf, make_scanned_pdf

OTHER = dict(producer="Some Scanner", cover=None, wm_name=None)


def with_text(path, pages, text, at=(400, 760), size=10, render_mode=3):
    """Add (invisible by default) text to the given pages, as an OCR layer over a baked stamp would be."""
    doc = pymupdf.open(path)
    for i in pages:
        doc[i].insert_text(at, text, fontsize=size, render_mode=render_mode)
    out = str(path).replace(".pdf", "-t.pdf")
    doc.save(out)
    return out


def found(result):
    return sorted((s.page, s.phrase) for s in result.found)


def test_repeated_phrase_in_text_layer_is_found_with_pdf_coordinates(tmp_path, cfg):
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=4, **OTHER), range(4), "CONFIDENTIAL")
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert found(result) == [(0, "confidential"), (1, "confidential"), (2, "confidential"), (3, "confidential")]
    x0, y0, x1, y1 = result.found[0].rect
    # A 10 pt line with its baseline 760 pt from the top of a 792 pt page occupies roughly
    # PDF y 29-42 (PDF y runs upward from the bottom); x starts at 400.
    assert x0 == pytest.approx(400, abs=1)
    assert 25 < y0 < 35 and 35 < y1 < 45
    assert x1 > x0 + 50


def test_phrase_on_too_few_pages_is_reported_not_removed(tmp_path, cfg):
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=4, **OTHER), [1], "do not copy")
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert result.found == []
    assert [(s.page, s.phrase) for s in result.reported] == [(1, "do not copy")]


def test_oversized_phrase_box_is_reported_not_removed(tmp_path, cfg):
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=2, **OTHER), range(2), "DRAFT",
                     at=(20, 500), size=200)
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert result.found == []
    assert len(result.reported) == 2


def test_ia_patterns_only_apply_to_ia_files(tmp_path, cfg):
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=2, **OTHER), range(2), "Digital Library")
    doc = pymupdf.open(path)
    assert pwr.find_text_stamps(doc, cfg, service=None).found == []
    assert found(pwr.find_text_stamps(doc, cfg, service="internet_archive")) == [
        (0, "digital library"), (1, "digital library")]


def test_excluded_pages_are_not_searched_or_counted(tmp_path, cfg):
    # On pages 1-2 of 3; excluding page 0 makes that 2/2 = 100%, and page 0 is never searched.
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=3, **OTHER), [0, 1, 2], "SAMPLE")
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None, exclude_pages=(0,))
    assert found(result) == [(1, "sample"), (2, "sample")]


def test_disabled_ocr_setting_finds_nothing(tmp_path, cfg):
    cfg["processing"]["enable_ocr_fallback"] = False
    path = with_text(make_pdf(tmp_path / "a.pdf", body_pages=2, **OTHER), range(2), "CONFIDENTIAL")
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert result.found == [] and result.reported == []


# ---- OCR (pages without a text layer) ----


def test_ocr_finds_baked_stamp_on_image_only_pages(tmp_path, cfg):
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=2, stamp_text="CONFIDENTIAL", stamp_at=(380, 60))
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert found(result) == [(0, "confidential"), (1, "confidential")]
    x0, y0, x1, y1 = result.found[0].rect
    # Stamp baseline 60 pt from the top at x=380: PDF y about 732 (baseline) to ~755 (cap height).
    assert x0 == pytest.approx(380, abs=6)
    assert 725 < y0 < 740 and 745 < y1 < 770


def test_ocr_joins_multi_word_phrases(tmp_path, cfg):
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=2, stamp_text="DO NOT COPY", stamp_at=(380, 60))
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert found(result) == [(0, "do not copy"), (1, "do not copy")]
    x0, _, x1, _ = result.found[0].rect
    assert x1 - x0 > 120  # spans all three words, not just one


def test_ocr_respects_minimum_confidence(tmp_path, cfg):
    cfg["processing"]["ocr_min_confidence"] = 101  # Tesseract confidence never exceeds 100
    path = make_scanned_pdf(tmp_path / "s.pdf", pages=2)
    result = pwr.find_text_stamps(pymupdf.open(path), cfg, service=None)
    assert result.found == [] and result.reported == []
