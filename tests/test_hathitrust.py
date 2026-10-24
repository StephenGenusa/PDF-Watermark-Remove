import pikepdf

import pdf_watermark_remover as pwr
from conftest import make_hathi_pdf, make_pdf


def scan(path, cfg):
    return pwr.find_hathitrust_watermarks(pikepdf.open(path), cfg)


def test_shared_stamp_found_on_every_page(tmp_path, cfg):
    result = scan(make_hathi_pdf(tmp_path / "h.pdf", pages=3), cfg)
    assert [(w.name, w.pages) for w in result.found] == [("/PxCBA", [0, 1, 2])]
    assert result.found[0].rects[0] == [(10, 10, 110, 30)]


def test_per_page_stamps_are_each_found(tmp_path, cfg):
    result = scan(make_hathi_pdf(tmp_path / "h.pdf", pages=3, shared=False), cfg)
    assert [(w.name, w.pages) for w in result.found] == [("/PxCBA", [0]), ("/PxCBA", [1]), ("/PxCBA", [2])]
    assert len({w.objgen for w in result.found}) == 3


def test_every_configured_key_is_found(tmp_path, cfg):
    result = scan(make_hathi_pdf(tmp_path / "h.pdf", keys=("/PxCBF", "/PxCBG")), cfg)
    assert sorted(w.name for w in result.found) == ["/PxCBF", "/PxCBG"]


def test_oversized_stamp_rejected_by_safety(tmp_path, cfg):
    # 600 x 700 pt = 86.6% of the page.
    result = scan(make_hathi_pdf(tmp_path / "h.pdf", placement=(0, 0, 600, 700)), cfg)
    assert result.found == []
    assert result.rejected[0][0] == "/PxCBA"
    assert "safety" in result.rejected[0][1]


def test_stamp_that_is_the_largest_image_is_protected(tmp_path, cfg):
    # Scan drawn 100 x 100; stamp 300 x 200 (12.4% of page) is larger.
    path = make_hathi_pdf(tmp_path / "h.pdf", placement=(10, 10, 300, 200), scan_placement=(0, 0, 100, 100))
    result = scan(path, cfg)
    assert result.found == []
    assert "largest" in result.rejected[0][1]


def test_non_hathitrust_file_has_no_findings(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf"), cfg)
    assert result.found == [] and result.rejected == []


def test_largest_image_protection_can_be_disabled(tmp_path, cfg):
    cfg["safety"]["protect_largest_image"] = False
    path = make_hathi_pdf(tmp_path / "h.pdf", placement=(10, 10, 300, 200), scan_placement=(0, 0, 100, 100))
    assert [w.name for w in scan(path, cfg).found] == ["/PxCBA"]


def test_pages_being_deleted_do_not_veto_a_stamp(tmp_path, cfg):
    # On the notice page the shared stamp is the only image; that page is being removed.
    path = make_hathi_pdf(tmp_path / "h.pdf", cover=True)
    pdf = pikepdf.open(path)
    assert pwr.find_hathitrust_watermarks(pdf, cfg).found == []
    assert [w.name for w in pwr.find_hathitrust_watermarks(pdf, cfg, exclude_pages=(0,)).found] == ["/PxCBA"]
