import pymupdf

import pdf_watermark_remover as pwr
from conftest import make_hathi_pdf, make_ia_pdf, make_pdf


def scan(path, cfg):
    return pwr.find_promo_pages(pymupdf.open(path), cfg)


def test_standard_cover_detected_with_all_signals(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf"), cfg)
    assert result.pages == [0]
    assert result.signals[0] == {"link", "image", "text", "no_watermark"}
    assert result.suspects == []


def test_cover_at_end_detected(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf", cover="last", body_pages=5), cfg)
    assert result.pages == [5]


def test_reworded_notice_still_detected_by_link_and_qr(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", cover_lines=["Brand new wording from Google, 2031 edition"])
    result = scan(path, cfg)
    assert result.pages == [0]
    assert result.signals[0] == {"link", "image", "no_watermark"}


def test_phrase_split_by_line_break_matches(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", cover_link=None, cover_qr=False,
                    cover_lines=["This copy was digitized", "by Google long ago."])
    result = scan(path, cfg)
    assert result.signals[0] == {"text", "no_watermark"}
    assert result.pages == [0]


def test_single_signal_page_is_kept_and_reported(tmp_path, cfg):
    # Text matches, but the page carries the watermark and has no link or QR code.
    path = make_pdf(tmp_path / "a.pdf", cover=None,
                    body_text={0: ["this is a digital copy of a book"]})
    result = scan(path, cfg)
    assert result.pages == []
    assert result.suspects == [0]
    assert result.signals[0] == {"text"}


def test_google_page_outside_search_window_is_never_a_candidate(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", cover=None, body_pages=9, wm_pages=[0, 1, 2, 3, 5, 6, 7, 8],
                    body_text={4: ["digitized by google", "books.google.com"]})
    result = scan(path, cfg)
    assert result.pages == []
    assert result.suspects == []


def test_missing_watermark_alone_is_not_a_suspect(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", cover=None, body_pages=5, wm_pages=[1, 2, 3, 4])
    result = scan(path, cfg)
    assert result.pages == []
    assert result.suspects == []
    assert result.signals[0] == {"no_watermark"}


def test_no_watermark_signal_ignored_when_file_has_no_watermarks(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", wm_name=None, cover_link=None, cover_qr=False)
    result = scan(path, cfg)
    assert result.signals[0] == {"text"}
    assert result.pages == []
    assert result.suspects == [0]


def test_form_watermark_counts_as_watermark_present(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", wm_name="/Form0", wm_kind="form",
                    cover_link=None, cover_qr=False)
    result = scan(path, cfg)
    assert result.signals[0] == {"text", "no_watermark"}
    assert result.signals[1] == set()


# ---- Internet Archive ----


def ia_scan(path, cfg):
    return pwr.find_promo_pages(pymupdf.open(path), cfg, service="internet_archive")


def test_ia_promo_page_with_text_and_signature_detected(tmp_path, cfg):
    result = ia_scan(make_ia_pdf(tmp_path / "i.pdf"), cfg)
    assert result.pages == [2]
    assert result.signals[2] == {"text", "image"}
    assert result.signals[0] == set()


def test_ia_text_without_signature_is_only_a_suspect(tmp_path, cfg):
    result = ia_scan(make_ia_pdf(tmp_path / "i.pdf", signature=False), cfg)
    assert result.pages == []
    assert result.suspects == [2]


def test_ia_signature_with_wrong_length_does_not_count(tmp_path, cfg):
    result = ia_scan(make_ia_pdf(tmp_path / "i.pdf", signature_length=8419), cfg)
    assert result.signals[2] == {"text"}
    assert result.pages == []


# ---- HathiTrust ----
def hathi_scan(path, cfg):
    return pwr.find_promo_pages(pymupdf.open(path), cfg, service="hathitrust")


def test_hathitrust_notice_page_detected(tmp_path, cfg):
    result = hathi_scan(make_hathi_pdf(tmp_path / "h.pdf", cover=True), cfg)
    assert result.pages == [0]
    assert result.signals[0] == {"link", "text"}
    assert result.signals[1] == set()


def test_hathitrust_licence_variant_still_detected(tmp_path, cfg):
    # "The Hymody of Christadelphians": CC licence instead of the public-domain lines.
    lines = ["Find this Book Online: https://hdl.handle.net/2027/mdp.39015040453469",
             "Creative Commons Attribution-NonCommercial-NoDerivatives"]
    result = hathi_scan(make_hathi_pdf(tmp_path / "h.pdf", cover=True, cover_lines=lines), cfg)
    assert result.pages == [0]


def test_hathitrust_notice_text_alone_is_only_a_suspect(tmp_path, cfg):
    result = hathi_scan(make_hathi_pdf(tmp_path / "h.pdf", cover=True, cover_links=[]), cfg)
    assert result.pages == []
    assert result.suspects == [0]


def test_non_hathitrust_handle_link_is_not_a_signal(tmp_path, cfg):
    result = hathi_scan(make_hathi_pdf(tmp_path / "h.pdf", cover=True, cover_lines=["Some notes"],
                                       cover_links=["https://hdl.handle.net/1811/12345"]), cfg)
    assert result.signals[0] == set()
