import pikepdf

import pdf_watermark_remover as pwr
from conftest import make_hathi_pdf, make_pdf

OTHER = dict(producer="Some Scanner", cover=None)


def scan(path, cfg, exclude=()):
    return pwr.find_generic_watermarks(pikepdf.open(path), cfg, exclude_pages=exclude)


def names(result):
    return sorted(w.name for w in result.found)


def test_frequent_width_finds_stamp_but_protects_same_width_scans(tmp_path, cfg):
    # Aberdeen in generic mode: every scan shares one width, so the rule flags them too,
    # but the safety limits must reject every one of them.
    path = make_pdf(tmp_path / "a.pdf", body_pages=4, wm_name="/Stamp", wm_width=60, **OTHER)
    result = scan(path, cfg)
    assert names(result) == ["/Stamp"]
    assert [name for name, _ in result.rejected] == ["/J2i0"] * 4
    assert all("safety limit" in reason for _, reason in result.rejected)


def test_width_is_counted_once_per_page(tmp_path, cfg):
    # /Stamp (width 60) on 7 of 10 pages = 70%. Two extra width-60 images on page 0 would push
    # an occurrence count to 9/10, over the 80% threshold.
    extras = {0: [("/X1", 60, 5, (10, 400, 20, 5)), ("/X2", 60, 5, (10, 420, 20, 5))]}
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_name="/Stamp", wm_width=60,
                    wm_pages=range(7), scan_width=41, extra_images=extras, **OTHER)
    result = scan(path, cfg)
    assert "/Stamp" not in names(result)


def test_fixed_width_rule(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_name="/Logo", wm_width=156, wm_pages=[3],
                    **OTHER)
    assert names(scan(path, cfg)) == ["/Logo"]


def test_transparency_group_rule(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_name="/Fx", wm_kind="form", wm_group=True,
                    wm_pages=[2], wm_placement=(10, 10, 1, 1), **OTHER)
    assert names(scan(path, cfg)) == ["/Fx"]


def test_form_without_transparency_group_is_not_a_candidate(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_name="/Fx", wm_kind="form",
                    wm_pages=[2], wm_placement=(10, 10, 1, 1), **OTHER)
    result = scan(path, cfg)
    assert "/Fx" not in names(result) + [name for name, _ in result.rejected]


def test_name_rule(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=10, wm_name="/Form0", wm_kind="form",
                    wm_pages=[5], wm_placement=(10, 10, 1, 1), **OTHER)
    assert names(scan(path, cfg)) == ["/Form0"]


def test_excluded_pages_do_not_count_toward_frequency(tmp_path, cfg):
    # Stamp on 8 of 10 pages: 80% passes; counting 2 more (excluded) pages gives 8/12 = 67%.
    path = make_pdf(tmp_path / "a.pdf", body_pages=12, wm_name="/Stamp", wm_width=60,
                    wm_pages=range(2, 10), scan_width=41, **OTHER)
    assert names(scan(path, cfg, exclude=(10, 11))) == ["/Stamp"]
    assert names(scan(path, cfg)) == []


def test_rules_can_be_switched_off(tmp_path, cfg):
    cfg["general"]["generic_rules"].update(frequent_width_ratio=1.1, fixed_widths=[],
                                           transparency_groups=False, xobject_names=[])
    path = make_pdf(tmp_path / "a.pdf", body_pages=4, wm_name="/Form0", wm_kind="form", wm_group=True,
                    wm_placement=(10, 10, 1, 1), **OTHER)
    result = scan(path, cfg)
    assert result.found == [] and result.rejected == []


def test_all_pages_excluded_finds_nothing_instead_of_crashing(tmp_path, cfg):
    path = make_pdf(tmp_path / "a.pdf", body_pages=1, wm_name="/Form0", **OTHER)
    result = scan(path, cfg, exclude=(0,))
    assert result.found == [] and result.rejected == []


# ---- optional-content "Watermark" layer ----


def test_images_on_watermark_layer_found_whatever_their_names(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1", "/Zz2"), producer="Anything")
    assert names(scan(path, cfg)) == ["/Zz1", "/Zz2"]  # the images, not the /CBJ form


def test_form_without_layer_contributes_nothing(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",), ocg=None)
    assert names(scan(path, cfg)) == []


def test_layer_with_other_name_contributes_nothing(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",), ocg="Background")
    assert names(scan(path, cfg)) == []


def test_layer_referenced_directly_by_oc_is_recognised(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",))
    pdf = pikepdf.open(path)
    for page in pdf.pages:
        form = page.Resources.XObject.CBJ
        form.OC = form.OC.OCGs  # /OC -> the OCG itself instead of an OCMD
    assert names(pwr.find_generic_watermarks(pdf, cfg)) == ["/Zz1"]


def test_layer_images_still_obey_safety_limits(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",), placement=(0, 0, 600, 700))
    result = scan(path, cfg)
    assert names(result) == []
    assert ("/Zz1", "covers 87% of page 0 (safety limit)") in result.rejected


def test_layer_listed_in_ocmd_array_is_recognised(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",))
    pdf = pikepdf.open(path)
    for page in pdf.pages:
        form = page.Resources.XObject.CBJ
        form.OC.OCGs = pikepdf.Array([form.OC.OCGs])
    assert names(pwr.find_generic_watermarks(pdf, cfg)) == ["/Zz1"]


def test_ocmd_with_only_visibility_expression_is_not_treated_as_watermark(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",))
    pdf = pikepdf.open(path)
    for page in pdf.pages:
        form = page.Resources.XObject.CBJ
        layer = form.OC.OCGs
        form.OC = pikepdf.Dictionary(Type=pikepdf.Name.OCMD, VE=pikepdf.Array([pikepdf.Name.Not, layer]))
    assert names(pwr.find_generic_watermarks(pdf, cfg)) == []


def test_stamp_drawn_twice_on_a_page_counts_that_page_once(tmp_path, cfg):
    result = scan(make_hathi_pdf(tmp_path / "h.pdf", pages=3, keys=("/Zz1", "/Zz1")), cfg)
    assert [(w.name, w.pages) for w in result.found] == [("/Zz1", [0, 1, 2])]


def test_generic_safety_ignores_pages_being_deleted(tmp_path, cfg):
    path = make_hathi_pdf(tmp_path / "h.pdf", keys=("/Zz1",), cover=True)
    assert names(scan(path, cfg)) == []
    assert names(scan(path, cfg, exclude=(0,))) == ["/Zz1"]
