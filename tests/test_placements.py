import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, Pdf, Stream

import pdf_watermark_remover as pwr
from conftest import _form, _wm_image


_open_pdfs = []  # keep each Pdf alive while its page is in use


def page_with(contents, form_matrix=None):
    """One page with an image /Wm and a form /F (bbox 0 0 100 20); contents is a list of streams."""
    pdf = Pdf.new()
    form = _form(pdf)
    if form_matrix:
        form.Matrix = Array(form_matrix)
    resources = Dictionary(XObject=Dictionary(Wm=_wm_image(pdf, 10, 204), F=form))
    page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=resources,
                      Contents=Array([pdf.make_indirect(Stream(pdf, c.encode())) for c in contents]))
    pdf.pages.append(pikepdf.Page(page))
    _open_pdfs.append(pdf)
    return pdf.pages[0]


def rects(page, name):
    return [tuple(round(v, 6) for v in r) for r in pwr.xobject_placements(page).get(name, [])]


def test_image_placed_by_cm_maps_unit_square():
    page = page_with(["q 66 0 0 13 273 10 cm /Wm Do Q"])
    assert rects(page, "/Wm") == [(273, 10, 339, 23)]


def test_nested_cm_composes_inner_then_outer():
    page = page_with(["q 2 0 0 2 10 10 cm q 30 0 0 6 0 0 cm /Wm Do Q Q"])
    assert rects(page, "/Wm") == [(10, 10, 70, 22)]


def test_Q_restores_previous_matrix():
    page = page_with(["q 2 0 0 2 0 0 cm q 10 0 0 10 5 5 cm Q 10 0 0 10 0 0 cm /Wm Do Q"])
    assert rects(page, "/Wm") == [(0, 0, 20, 20)]


def test_mirrored_placement_is_normalized():
    page = page_with(["q -66 0 0 13 339 10 cm /Wm Do Q"])
    assert rects(page, "/Wm") == [(273, 10, 339, 23)]


def test_each_invocation_is_reported():
    page = page_with(["q 10 0 0 10 0 0 cm /Wm Do Q q 10 0 0 10 100 100 cm /Wm Do Q"])
    assert rects(page, "/Wm") == [(0, 0, 10, 10), (100, 100, 110, 110)]


def test_form_uses_bbox_and_ctm():
    page = page_with(["q 1 0 0 1 50 60 cm /F Do Q"])
    assert rects(page, "/F") == [(50, 60, 150, 80)]


def test_form_matrix_applies_before_ctm():
    page = page_with(["q 1 0 0 1 50 60 cm /F Do Q"], form_matrix=[2, 0, 0, 2, 0, 0])
    assert rects(page, "/F") == [(50, 60, 250, 100)]


def test_operator_split_across_content_streams():
    page = page_with(["q 66 0 0 13 273 10", "cm /Wm Do Q"])
    assert rects(page, "/Wm") == [(273, 10, 339, 23)]


def test_unknown_xobject_name_is_ignored():
    page = page_with(["q 10 0 0 10 0 0 cm /Missing Do Q /Wm Do"])
    assert pwr.xobject_placements(page) == {"/Wm": [(0, 0, 1, 1)]}


def test_page_without_xobjects_has_no_placements():
    pdf = Pdf.new()
    pdf.add_blank_page()
    _open_pdfs.append(pdf)
    assert pwr.xobject_placements(pdf.pages[0]) == {}


def test_unbalanced_Q_resets_to_identity_instead_of_crashing():
    page = page_with(["Q 10 0 0 10 5 5 cm /Wm Do"])
    assert rects(page, "/Wm") == [(5, 5, 15, 15)]


# ---- nested draws (forms containing XObjects) ----

def nested_page(page_content, form_content, form_matrix=None, form_has_resources=True,
                self_reference=False):
    """Page with form /CBJ (bbox = page) whose content draws image /PxCBA."""
    pdf = Pdf.new()
    inner = pdf.make_indirect(_wm_image(pdf, 10, 10))
    form = pdf.make_indirect(Stream(pdf, form_content.encode(), Type=Name.XObject, Subtype=Name.Form,
                                    BBox=Array([0, 0, 612, 792])))
    if form_matrix:
        form.Matrix = Array(form_matrix)
    if form_has_resources:
        form.Resources = Dictionary(XObject=Dictionary(PxCBA=inner))
        if self_reference:
            form.Resources.XObject.CBJ = form
    page_xobjects = Dictionary(CBJ=form)
    if not form_has_resources:
        page_xobjects.PxCBA = inner
    page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                      Resources=Dictionary(XObject=page_xobjects),
                      Contents=pdf.make_indirect(Stream(pdf, page_content.encode())))
    pdf.pages.append(pikepdf.Page(page))
    _open_pdfs.append(pdf)
    return pdf.pages[0], inner.objgen, form.objgen


def draws_by_name(page):
    return {(d.name, d.parent, d.depth): tuple(round(v, 6) for v in d.rect)
            for d in pwr.xobject_draws(page)}


def test_nested_image_inside_form_is_placed_through_outer_ctm():
    page, inner, form = nested_page("q 1 0 0 1 100 200 cm /CBJ Do Q", "q 50 0 0 10 5 5 cm /PxCBA Do Q")
    draws = pwr.xobject_draws(page)
    assert draws_by_name(page) == {("/CBJ", None, 0): (100, 200, 712, 992),
                                   ("/PxCBA", "/CBJ", 1): (105, 205, 155, 215)}
    assert {d.name: d.objgen for d in draws} == {"/CBJ": form, "/PxCBA": inner}
    assert {d.name: d.subtype for d in draws} == {"/CBJ": "/Form", "/PxCBA": "/Image"}


def test_form_matrix_applies_to_nested_content():
    page, _, _ = nested_page("/CBJ Do", "q 50 0 0 10 5 5 cm /PxCBA Do Q", form_matrix=[2, 0, 0, 2, 0, 0])
    assert draws_by_name(page)[("/PxCBA", "/CBJ", 1)] == (10, 10, 110, 30)


def test_form_without_resources_uses_page_resources():
    page, _, _ = nested_page("/CBJ Do", "q 50 0 0 10 5 5 cm /PxCBA Do Q", form_has_resources=False)
    assert draws_by_name(page)[("/PxCBA", "/CBJ", 1)] == (5, 5, 55, 15)


def test_self_referencing_form_does_not_recurse_forever():
    page, _, _ = nested_page("/CBJ Do", "/CBJ Do q 50 0 0 10 5 5 cm /PxCBA Do Q", self_reference=True)
    draws = pwr.xobject_draws(page)
    assert ("/PxCBA", "/CBJ", 1) in draws_by_name(page)
    assert max(d.depth for d in draws) <= pwr.MAX_FORM_DEPTH


def test_placements_report_only_page_level_names():
    page, _, _ = nested_page("/CBJ Do", "q 50 0 0 10 5 5 cm /PxCBA Do Q")
    assert set(pwr.xobject_placements(page)) == {"/CBJ"}
