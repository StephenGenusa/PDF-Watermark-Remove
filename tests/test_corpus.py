"""Acceptance tests against the real PDFs in ./Books (opt-in: pytest -m corpus)."""

import os

import pikepdf
import pytest

import pdf_watermark_remover as pwr

BOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Books")


def books_by_producer(prefix):
    if not os.path.isdir(BOOKS):
        return []
    names = []
    for f in sorted(os.listdir(BOOKS)):
        if f.lower().endswith(".pdf"):
            with pikepdf.open(os.path.join(BOOKS, f)) as pdf:
                if str(pdf.docinfo.get("/Producer", "")).startswith(prefix):
                    names.append(f)
    return names


GOOGLE = books_by_producer("Google Books PDF Converter")
HATHITRUST = books_by_producer("HathiTrust")

pytestmark = pytest.mark.corpus


def scan_images_per_page(pdf, pages):
    """Names of page-level images on each page (the scans; stamps live inside forms)."""
    return [sorted(n for n, x in p.Resources.XObject.items() if x.get("/Subtype") == "/Image")
            for p in pages]


@pytest.mark.skipif(not GOOGLE, reason="no Google Books PDFs in ./Books")
@pytest.mark.parametrize("name", GOOGLE)
def test_google_book_loses_only_cover_and_watermark(name, tmp_path, cfg):
    out = str(tmp_path / name)
    result = pwr.process_pdf(os.path.join(BOOKS, name), out, cfg)
    assert (result.status, result.reasons) == ("cleaned", [])
    assert result.service == "google"
    assert result.promo_pages == [0]
    assert result.removed == ["/Wm"]
    assert result.suspects == []
    with pikepdf.open(os.path.join(BOOKS, name)) as before, pikepdf.open(out) as after:
        assert len(after.pages) == len(before.pages) - 1
        scans_before = sum("/J2i0" in p.Resources.XObject for p in list(before.pages)[1:])
        scans_after = sum("/J2i0" in p.Resources.XObject for p in after.pages)
        assert scans_after == scans_before


@pytest.mark.skipif(not HATHITRUST, reason="no HathiTrust PDFs in ./Books")
@pytest.mark.parametrize("name", HATHITRUST)
def test_hathitrust_book_loses_only_notice_page_and_stamps(name, tmp_path, cfg):
    out = str(tmp_path / name)
    result = pwr.process_pdf(os.path.join(BOOKS, name), out, cfg)
    assert (result.status, result.reasons) == ("cleaned", [])
    assert result.service == "hathitrust"
    assert result.promo_pages == [0]
    assert sorted(result.removed) == ["/PxCBA", "/PxCBB", "/PxCBC"]
    with pikepdf.open(os.path.join(BOOKS, name)) as before, pikepdf.open(out) as after:
        assert len(after.pages) == len(before.pages) - 1
        assert scan_images_per_page(after, after.pages) == \
            scan_images_per_page(before, list(before.pages)[1:])
        # The stamps no longer draw anything on any page.
        drawn = {d.name for p in after.pages for d in pwr.xobject_draws(p)
                 if d.subtype == "/Image" and d.depth > 0}
        assert drawn == set()
        # Every page is trimmed to exactly its scan: the vacated stamp margins are cropped away.
        assert result.cropped_pages == list(range(1, len(before.pages)))
        for page in after.pages:
            scan = next(d.rect for d in pwr.xobject_draws(page) if d.depth == 0 and d.subtype == "/Image")
            assert [round(float(v), 2) for v in page.obj.CropBox] == [round(v, 2) for v in scan]
