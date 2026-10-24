import copy

import pikepdf
import pytest

import pdf_watermark_remover as pwr
from conftest import FULL_PAGE, WM_PLACEMENT, _do, _wm_do, make_pdf


def scan(path, config, exclude=(0,)):
    pdf = pikepdf.open(path)
    return pwr.find_watermarks(pdf, config, exclude_pages=exclude)


def loosened(cfg, **wm_overrides):
    """Copy of cfg whose /Wm spec has the given overrides."""
    config = copy.deepcopy(cfg)
    config["services"]["google"]["watermarks"]["xobjects"][0].update(wm_overrides)
    return config


def found_names(result):
    return [w.name for w in result.found]


def test_google_wm_found_on_every_body_page(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf", body_pages=3), cfg)
    assert found_names(result) == ["/Wm"]
    wm = result.found[0]
    assert wm.pages == [1, 2, 3]
    assert wm.rects[1] == [(273, 10, 339, 23)]


def test_scan_images_sharing_one_width_are_never_candidates(tmp_path, cfg):
    # Aberdeen regression: every page scan was 3938 wide, colour plates were 720 wide.
    path = make_pdf(tmp_path / "a.pdf", body_pages=4, scan_width=3938 // 50,
                    extra_images={1: [("/CTi0", 720, 1056, (100, 100, 300, 400))]})
    result = scan(path, cfg)
    assert found_names(result) == ["/Wm"]


def test_promo_pages_are_excluded_from_page_ratio(tmp_path, cfg):
    # 9 of 10 body pages = 90% passes; counting the cover too would give 9/11 = 82%.
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_pages=range(9))
    assert found_names(scan(path, cfg, exclude=(0,))) == ["/Wm"]
    assert found_names(scan(path, cfg, exclude=())) == []


def test_watermark_on_too_few_pages_rejected(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_pages=range(8))
    result = scan(path, cfg)
    assert result.found == []
    assert result.rejected[0][0] == "/Wm"
    assert "80%" in result.rejected[0][1]


def test_unshared_watermark_objects_rejected(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf", body_pages=4, wm_shared=False), cfg)
    assert result.found == []
    assert result.rejected[0][0] == "/Wm"


@pytest.mark.parametrize("kwargs, reason", [
    ({"wm_height": 200}, "Height"),
    ({"wm_bpc": 8}, "BitsPerComponent"),
    ({"wm_stencil": False}, "stencil"),
    ({"wm_kind": "form"}, "Subtype"),
])
def test_fingerprint_mismatch_rejected(tmp_path, cfg, kwargs, reason):
    result = scan(make_pdf(tmp_path / "a.pdf", **kwargs), cfg)
    assert result.found == []
    assert reason in result.rejected[0][1]


def test_placement_larger_than_spec_area_rejected(tmp_path, cfg):
    # 400 x 300 pt = 24.8% of a 612 x 792 page, above the 5% limit.
    result = scan(make_pdf(tmp_path / "a.pdf", wm_placement=(100, 100, 400, 300)), cfg)
    assert result.found == []
    assert "area" in result.rejected[0][1]


def test_safety_area_limit_overrides_loose_spec(tmp_path, cfg):
    # 500 x 600 pt = 61.9% of the page: allowed by the loosened spec, blocked by safety (50%).
    config = loosened(cfg, max_page_area_ratio=1.0)
    result = scan(make_pdf(tmp_path / "a.pdf", wm_placement=(50, 50, 500, 600)), config)
    assert result.found == []
    assert "safety" in result.rejected[0][1]


def test_largest_image_on_a_page_is_protected(tmp_path, cfg):
    # Scan drawn 100 x 100, watermark 200 x 150 (6.2% of page): watermark is the largest image.
    config = loosened(cfg, max_page_area_ratio=1.0)
    path = make_pdf(tmp_path / "a.pdf", scan_placement=(0, 0, 100, 100),
                    wm_placement=(200, 200, 200, 150))
    result = scan(path, config)
    assert result.found == []
    assert "largest" in result.rejected[0][1]


def test_largest_image_protection_can_be_disabled(tmp_path, cfg):
    config = loosened(cfg, max_page_area_ratio=1.0)
    config["safety"]["protect_largest_image"] = False
    path = make_pdf(tmp_path / "a.pdf", scan_placement=(0, 0, 100, 100),
                    wm_placement=(200, 200, 200, 150))
    assert found_names(scan(path, config)) == ["/Wm"]


def test_form0_found_with_looser_checks(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", wm_name="/Form0", wm_kind="form",
                    wm_placement=(273, 10, 1, 1))
    result = scan(path, cfg)
    assert found_names(result) == ["/Form0"]
    assert result.found[0].rects[1] == [(273, 10, 373, 30)]


def test_same_object_under_two_names_is_one_watermark(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", wm_extra_name="/Form0")
    result = scan(path, cfg)
    assert found_names(result) == ["/Wm"]
    assert [r[0] for r in result.rejected] == ["/Form0"]


def test_file_without_configured_names_has_no_findings(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf", wm_name="/Logo"), cfg)
    assert result.found == []
    assert result.rejected == []


def test_registered_but_undrawn_watermark_is_still_removable(tmp_path, cfg):
    # Present in /Resources on every page but drawn on only one: removing it is harmless.
    path = make_pdf(tmp_path / "a.pdf", body_pages=3)
    pdf = pikepdf.open(path)
    for page in list(pdf.pages)[2:]:
        page.Contents[0].write(_do("/J2i0", FULL_PAGE).encode())
    result = pwr.find_watermarks(pdf, cfg, exclude_pages=(0,))
    assert found_names(result) == ["/Wm"]
    assert result.found[0].rects == {1: [(273, 10, 339, 23)]}


def test_watermark_that_is_the_only_image_on_a_page_is_protected(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=3)
    pdf = pikepdf.open(path)
    page = pdf.pages[2]
    del page.Resources.XObject["/J2i0"]
    page.Contents[0].write(_wm_do("/Wm", WM_PLACEMENT).encode())
    result = pwr.find_watermarks(pdf, cfg, exclude_pages=(0,))
    assert result.found == []
    assert "largest" in result.rejected[0][1]


def test_malformed_direct_object_watermark_rejected(tmp_path, cfg):
    # XObjects must be indirect streams; a direct dictionary under /Form0 can't be shared.
    path = make_pdf(tmp_path / "a.pdf", wm_name=None, body_pages=3)
    pdf = pikepdf.open(path)
    for page in list(pdf.pages)[1:]:
        page.Resources.XObject["/Form0"] = pikepdf.Dictionary(Subtype=pikepdf.Name.Form)
    result = pwr.find_watermarks(pdf, cfg, exclude_pages=(0,))
    assert result.found == []
    assert result.rejected == [("/Form0", "not a shared indirect object")]


def test_all_pages_excluded_finds_nothing_instead_of_crashing(tmp_path, cfg):
    result = scan(make_pdf(tmp_path / "a.pdf", body_pages=0), cfg, exclude=(0,))
    assert result.found == [] and result.rejected == []
