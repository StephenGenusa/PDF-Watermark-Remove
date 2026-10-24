import pikepdf
import pymupdf
import pytest

import pdf_watermark_remover as pwr
from conftest import NOTICE_LINES, make_hathi_pdf, make_ia_pdf, make_pdf


@pytest.mark.parametrize("raw, want", [
    ("Digitized  \nby Google", "digitized by google"),
    ("  This\tis\r\na   Book ", "this is a book"),
    ("", ""),
])
def test_normalize_text_collapses_whitespace_and_case(raw, want):
    assert pwr.normalize_text(raw) == want


def test_google_identified_by_producer(tmp_path, cfg):
    doc = pymupdf.open(make_pdf(tmp_path / "a.pdf", cover=None))
    assert pwr.is_google_pdf(doc, cfg)


def test_google_identified_by_link_when_producer_missing(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", producer=None,
                    cover_link="https://books.google.co.uk/books?id=X")
    assert pwr.is_google_pdf(pymupdf.open(path), cfg)


def test_link_on_last_page_identifies_google(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", producer=None, cover="last", body_pages=6)
    assert pwr.is_google_pdf(pymupdf.open(path), cfg)


def test_non_google_pdf_not_identified(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", producer="Adobe Acrobat", cover_link="https://example.com")
    assert not pwr.is_google_pdf(pymupdf.open(path), cfg)


def test_google_link_deep_in_book_does_not_identify(tmp_path, cfg):
    # A bibliography page in the middle citing books.google.com is not a Google wrapper.
    path = make_pdf(tmp_path / "a.pdf", producer=None, cover=None, body_pages=9)
    doc = pymupdf.open(path)
    doc[4].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 72, 200, 90),
                        "uri": "https://books.google.com/books?id=CITED"})
    assert not pwr.is_google_pdf(doc, cfg)


# ---- service identification ----


def service_of(path, cfg):
    return pwr.identify_service(pymupdf.open(path), pikepdf.open(path), cfg)


def test_google_service(tmp_path, cfg):
    assert service_of(make_pdf(tmp_path / "a.pdf"), cfg) == "google"


@pytest.mark.parametrize("keys", [("/PxCBA",), ("/PxCBF", "/PxCBG")])
def test_hathitrust_identified_by_nested_watermark_keys(tmp_path, cfg, keys):
    assert service_of(make_hathi_pdf(tmp_path / "h.pdf", keys=keys), cfg) == "hathitrust"


def test_nested_keys_under_other_form_name_are_not_hathitrust(tmp_path, cfg):
    assert service_of(make_hathi_pdf(tmp_path / "h.pdf", form_name="/Other"), cfg) is None


def test_internet_archive_identified_by_notice_text(tmp_path, cfg):
    assert service_of(make_ia_pdf(tmp_path / "i.pdf", signature=False), cfg) == "internet_archive"


def test_internet_archive_identified_by_signature_image(tmp_path, cfg):
    path = make_ia_pdf(tmp_path / "i.pdf", promo_lines=["Front matter"])
    assert service_of(path, cfg) == "internet_archive"


def test_internet_archive_text_deep_in_book_does_not_identify(tmp_path, cfg):
    path = make_ia_pdf(tmp_path / "i.pdf", pages=12, promo_at=6, signature=False)
    assert service_of(path, cfg) is None


def test_google_wins_over_other_services(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", cover_lines=NOTICE_LINES + ["digitized by the internet archive"])
    assert service_of(path, cfg) == "google"


def test_unknown_pdf_has_no_service(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", producer="Adobe", cover=None)
    assert service_of(path, cfg) is None


def test_hathitrust_identified_by_producer(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Other",),
                          producer="HathiTrust Image Server / PDF::API2")
    assert service_of(path, cfg) == "hathitrust"


def test_hathitrust_identified_by_notice_link(tmp_path, cfg):
    assert service_of(make_hathi_pdf(tmp_path / "h.pdf", keys=("/Other",), cover=True), cfg) == "hathitrust"
