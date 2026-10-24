# pdf_watermark_remover.py
"""Removes Google Books watermarks and promotional pages from PDFs."""

import argparse
import logging
import math
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import cv2
import numpy as np
import pikepdf
import pymupdf
import pytesseract
import yaml

DEFAULT_CONFIG_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "watermark_config.yaml")

Rect = Tuple[float, float, float, float]
Matrix = Tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict:
    """Load and sanity-check the YAML configuration.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the configuration is malformed or missing a required section.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise yaml.YAMLError("Configuration root must be a dictionary")
    if "google" not in (config.get("services") or {}):
        raise yaml.YAMLError("Configuration must contain 'services.google'")
    if "safety" not in config:
        raise yaml.YAMLError("Configuration must contain a 'safety' section")
    return config


def normalize_text(text: str) -> str:
    """Lower-case text and collapse all runs of whitespace (incl. line breaks) to one space."""
    return re.sub(r"\s+", " ", text).strip().lower()


def search_page_indices(page_count: int, config: Dict, service: str = "google") -> List[int]:
    """Indices of the first/last pages where a service's wrapper page may appear."""
    span = config["services"][service]["promotional_pages"]["search_pages"]
    first = range(min(span["first"], page_count))
    last = range(max(page_count - span["last"], 0), page_count)
    return sorted(set(first) | set(last))


def _page_uris(page: pymupdf.Page) -> List[str]:
    return [link.get("uri", "") for link in page.get_links() if link.get("uri")]


def _matches_identify(doc: pymupdf.Document, config: Dict, service: str) -> bool:
    """True if the Producer or a wrapper-page link matches the service's `identify` block."""
    identify = config["services"][service]["identify"]
    producer = (doc.metadata or {}).get("producer") or ""
    if any(producer.startswith(prefix) for prefix in identify["producer_prefixes"]):
        return True
    for idx in search_page_indices(len(doc), config, service):
        for uri in _page_uris(doc[idx]):
            if any(domain in uri for domain in identify["link_domains"]):
                return True
    return False


def is_google_pdf(doc: pymupdf.Document, config: Dict) -> bool:
    """True if the Producer or a wrapper-page link identifies a Google Books PDF."""
    return _matches_identify(doc, config, "google")


def _multiply(m: Matrix, n: Matrix) -> Matrix:
    """PDF matrix product m x n (apply m first, then n)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + b * c2, a * b2 + b * d2,
            c * a2 + d * c2, c * b2 + d * d2,
            e * a2 + f * c2 + e2, e * b2 + f * d2 + f2)


def _transform_rect(rect: Rect, m: Matrix) -> Rect:
    """Bounding box of rect after transformation by m."""
    a, b, c, d, e, f = m
    x0, y0, x1, y1 = rect
    xs, ys = [], []
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        xs.append(a * x + c * y + e)
        ys.append(b * x + d * y + f)
    return (min(xs), min(ys), max(xs), max(ys))



def _nested_xobjects(page: pikepdf.Page, path: str) -> Optional[pikepdf.Object]:
    """Follow a path like "/CBJ/Resources/XObject" from the page's XObject resources."""
    parts = [p for p in path.split("/") if p]
    obj = page.resources.get("/XObject")
    for part in parts:
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            return None
        obj = obj.get("/" + part)
    return obj


def _hathitrust_pages(pdf: pikepdf.Pdf, config: Dict) -> List[int]:
    """Pages whose nested HathiTrust XObject dictionary holds a configured watermark key."""
    wm = config["services"]["hathitrust"]["watermarks"]
    pages = []
    for i, page in enumerate(pdf.pages):
        nested = _nested_xobjects(page, wm["nested_xobject_path"])
        if nested is not None and any(k in nested for k in wm["watermark_keys"]):
            pages.append(i)
    return pages


def _ia_signature_on_page(doc: pymupdf.Document, page: pymupdf.Page, promo: Dict) -> bool:
    """True if the page draws the configured IA signature image with its exact stored length."""
    name = promo["xobject_signature"].lstrip("/")
    for img in page.get_images(full=True):
        if img[7] == name and doc.xref_get_key(img[0], "Length")[1] == str(promo["signature_length"]):
            return True
    return False


def identify_service(doc: pymupdf.Document, pdf: pikepdf.Pdf, config: Dict) -> Optional[str]:
    """Which digitization service produced the file: google, hathitrust, internet_archive or None."""
    if is_google_pdf(doc, config):
        return "google"
    if _matches_identify(doc, config, "hathitrust") or _hathitrust_pages(pdf, config):
        return "hathitrust"
    promo = config["services"]["internet_archive"]["promotional_pages"]
    patterns = [normalize_text(t) for t in promo["text_signatures"]]
    for idx in search_page_indices(len(doc), config, "internet_archive"):
        page = doc[idx]
        text = normalize_text(page.get_text())
        if any(p in text for p in patterns) or _ia_signature_on_page(doc, page, promo):
            return "internet_archive"
    return None


MAX_FORM_DEPTH = 3


@dataclass
class Draw:
    """One XObject invocation found while walking a page's content."""
    name: str
    objgen: Tuple[int, int]
    rect: Rect                 # page user space
    subtype: str               # /Image or /Form
    depth: int                 # 0 = invoked by the page itself
    parent: Optional[str]      # name of the form that invoked it, if nested
    ancestors: Tuple[Tuple[int, int], ...] = ()  # objgens of the enclosing forms, outermost first


def _walk(content: pikepdf.Object, resources: pikepdf.Object, ctm: Matrix, depth: int,
          parent: Optional[str], out: List[Draw], ancestors: Tuple[Tuple[int, int], ...] = ()) -> None:
    xobjects = resources.get("/XObject") if resources is not None else None
    if not xobjects:
        return
    stack: List[Matrix] = []
    for operands, operator in pikepdf.parse_content_stream(content):
        op = str(operator)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else IDENTITY
        elif op == "cm":
            ctm = _multiply(tuple(float(v) for v in operands), ctm)
        elif op == "Do":
            name = str(operands[0])
            if name not in xobjects:
                continue
            xobj = xobjects[name]
            if xobj.get("/Subtype") == "/Form":
                matrix = tuple(float(v) for v in xobj.get("/Matrix", IDENTITY))
                form_ctm = _multiply(matrix, ctm)
                bbox = tuple(float(v) for v in xobj.BBox)
                out.append(Draw(name, xobj.objgen, _transform_rect(bbox, form_ctm), "/Form", depth, parent,
                                ancestors))
                if depth < MAX_FORM_DEPTH:
                    _walk(xobj, xobj.get("/Resources", resources), form_ctm, depth + 1, name, out,
                          ancestors + (xobj.objgen,))
            else:
                rect = _transform_rect((0.0, 0.0, 1.0, 1.0), ctm)
                out.append(Draw(name, xobj.objgen, rect, "/Image", depth, parent, ancestors))


def xobject_draws(page: pikepdf.Page) -> List[Draw]:
    """Every XObject drawn by the page, following forms up to MAX_FORM_DEPTH levels deep.

    Images occupy the unit square; forms occupy their /BBox mapped through /Matrix.
    The page's content streams are parsed as one stream, as ISO 32000 requires.
    """
    out: List[Draw] = []
    _walk(page, page.resources, IDENTITY, 0, None, out)
    return out


def xobject_placements(page: pikepdf.Page) -> Dict[str, List[Rect]]:
    """Where each XObject invoked directly by the page is drawn, keyed by resource name."""
    placements: Dict[str, List[Rect]] = {}
    for draw in xobject_draws(page):
        if draw.depth == 0:
            placements.setdefault(draw.name, []).append(draw.rect)
    return placements


def _xobject_names(page: pymupdf.Page) -> Set[str]:
    """Names (with leading slash) of the image and form XObjects a page uses."""
    names = {img[7] for img in page.get_images()} | {xo[1] for xo in page.get_xobjects()}
    return {"/" + n.lstrip("/") for n in names if n}


@dataclass
class PromoScan:
    """Promotional-page findings for one document."""
    pages: List[int] = field(default_factory=list)       # pages to delete
    suspects: List[int] = field(default_factory=list)    # partial matches, kept and reported
    signals: Dict[int, Set[str]] = field(default_factory=dict)


def _notice_signals(page: pymupdf.Page, sig: Dict, wm_names: Set[str], file_has_watermark: bool) -> Set[str]:
    """Signals for services whose notice page is described by a `signals` block (Google, HathiTrust)."""
    found: Set[str] = set()
    names = _xobject_names(page)
    if any(d in uri for uri in _page_uris(page) for d in sig.get("link_domains", [])):
        found.add("link")
    if names & set(sig.get("image_names", [])):
        found.add("image")
    text = normalize_text(page.get_text())
    if any(normalize_text(p) in text for p in sig.get("text_patterns", [])):
        found.add("text")
    if sig.get("no_watermark") and file_has_watermark and not names & wm_names:
        found.add("no_watermark")
    return found


def _ia_signals(doc: pymupdf.Document, page: pymupdf.Page, config: Dict) -> Set[str]:
    promo = config["services"]["internet_archive"]["promotional_pages"]
    found: Set[str] = set()
    text = normalize_text(page.get_text())
    if any(normalize_text(p) in text for p in promo["text_signatures"]):
        found.add("text")
    if _ia_signature_on_page(doc, page, promo):
        found.add("image")
    return found


def find_promo_pages(doc: pymupdf.Document, config: Dict, service: str = "google") -> PromoScan:
    """Score the first/last pages against a service's independent wrapper-page signals.

    A page is deleted only when at least min_signals signals agree. Pages with fewer
    signals (other than a lone missing watermark) are reported as suspects so that a
    changed notice page shows up as a warning rather than silently slipping through.
    """
    promo = config["services"][service]["promotional_pages"]
    wm_names: Set[str] = set()
    if service == "google":
        wm_names = {x["name"] for x in config["services"]["google"]["watermarks"]["xobjects"]}
    file_has_watermark = bool(wm_names) and any(_xobject_names(page) & wm_names for page in doc)

    result = PromoScan()
    for idx in search_page_indices(len(doc), config, service):
        page = doc[idx]
        if service == "internet_archive":
            found = _ia_signals(doc, page, config)
        else:
            found = _notice_signals(page, promo["signals"], wm_names, file_has_watermark)
        result.signals[idx] = found
        if len(found) >= promo["min_signals"]:
            result.pages.append(idx)
        elif found - {"no_watermark"}:
            result.suspects.append(idx)
    return result


def _area(rect: Rect) -> float:
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


@dataclass
class Watermark:
    """A shared XObject that matched a watermark fingerprint."""
    name: str
    objgen: Tuple[int, int]
    pages: List[int]                 # pages whose resources reference the object
    rects: Dict[int, List[Rect]]     # where it is drawn, per page


@dataclass
class WatermarkScan:
    found: List[Watermark] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)  # (name, reason)


def _fingerprint_problem(xobj: pikepdf.Object, spec: Dict) -> Optional[str]:
    """Why xobj fails the structural checks in spec, or None if it passes."""
    if "subtype" in spec and xobj.get("/Subtype") != spec["subtype"]:
        return f"Subtype {xobj.get('/Subtype')} is not {spec['subtype']}"
    if "bits_per_component" in spec and int(xobj.get("/BitsPerComponent", 0)) != spec["bits_per_component"]:
        return f"BitsPerComponent {xobj.get('/BitsPerComponent')} is not {spec['bits_per_component']}"
    if "height" in spec and int(xobj.get("/Height", 0)) != spec["height"]:
        return f"Height {xobj.get('/Height')} is not {spec['height']}"
    if spec.get("requires_stencil_mask"):
        mask = xobj.get("/Mask")
        if not (isinstance(mask, pikepdf.Stream) and mask.get("/ImageMask") is True):
            return "no stencil /Mask"
    return None


def find_watermarks(pdf: pikepdf.Pdf, config: Dict, exclude_pages: Iterable[int] = ()) -> WatermarkScan:
    """Find configured watermark XObjects; every check in the spec and safety limits must pass.

    exclude_pages (promotional pages being deleted) don't count toward the page ratio.
    """
    safety = config["safety"]
    excluded = set(exclude_pages)
    eligible = [i for i in range(len(pdf.pages)) if i not in excluded]
    placements: Dict[int, Dict[str, List[Rect]]] = {}
    claimed: Set[Tuple[int, int]] = set()
    result = WatermarkScan()

    def page_placements(i: int) -> Dict[str, List[Rect]]:
        if i not in placements:
            placements[i] = xobject_placements(pdf.pages[i])
        return placements[i]

    for spec in config["services"]["google"]["watermarks"]["xobjects"]:
        name = spec["name"]
        by_object: Dict[Tuple[int, int], List[int]] = {}
        for i in eligible:
            xobjects = pdf.pages[i].resources.get("/XObject")
            if xobjects and name in xobjects:
                by_object.setdefault(xobjects[name].objgen, []).append(i)
        if not by_object:
            continue

        objgen, pages = max(by_object.items(), key=lambda kv: len(kv[1]))
        ratio = len(pages) / len(eligible)
        problem = None
        if objgen == (0, 0):
            problem = "not a shared indirect object"
        elif ratio < spec.get("min_page_ratio", 1.0):
            problem = f"same object on only {ratio:.0%} of pages"
        elif objgen in claimed:
            problem = "object already matched under another name"
        else:
            problem = _fingerprint_problem(pdf.get_object(objgen), spec)

        rects: Dict[int, List[Rect]] = {}
        for i in pages if problem is None else []:
            drawn = page_placements(i).get(name, [])
            if not drawn:
                continue
            rects[i] = drawn
            box = [float(v) for v in pdf.pages[i].mediabox]
            page_area = _area(tuple(box))
            largest = max(_area(r) for r in drawn)
            if largest / page_area > safety["max_image_page_area_ratio"]:
                problem = f"covers {largest / page_area:.0%} of page {i} (safety limit)"
            elif largest / page_area > spec.get("max_page_area_ratio", 1.0):
                problem = f"covers {largest / page_area:.1%} of page {i}, above area limit"
            elif safety.get("protect_largest_image"):
                others = [_area(r) for n, rs in page_placements(i).items() if n != name for r in rs]
                if not others or largest >= max(others):
                    problem = f"would remove the largest image on page {i}"
            if problem:
                break

        if problem:
            result.rejected.append((name, problem))
            continue
        claimed.add(objgen)
        result.found.append(Watermark(name, objgen, pages, rects))
    return result



class _DrawCache:
    """Lazily computed xobject_draws for each page of a PDF."""

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        self.pdf = pdf
        self._draws: Dict[int, List[Draw]] = {}

    def __call__(self, i: int) -> List[Draw]:
        if i not in self._draws:
            self._draws[i] = xobject_draws(self.pdf.pages[i])
        return self._draws[i]


def _object_safety_problem(pdf: pikepdf.Pdf, objgen: Tuple[int, int], config: Dict, draws: _DrawCache,
                           excluded: Set[int] = frozenset()) -> Tuple[Optional[str], Dict[int, List[Rect]]]:
    """Check every page that draws objgen against the safety limits.

    Pages in `excluded` (promotional pages being deleted) are not checked.

    Returns (problem or None, rects where the object is drawn per page).
    """
    safety = config["safety"]
    rects: Dict[int, List[Rect]] = {}
    for i in range(len(pdf.pages)):
        if i in excluded:
            continue
        page_draws = draws(i)
        mine = [d.rect for d in page_draws if d.objgen == objgen]
        if not mine:
            continue
        rects[i] = mine
        page_area = _area(tuple(float(v) for v in pdf.pages[i].mediabox))
        largest = max(_area(r) for r in mine)
        if largest / page_area > safety["max_image_page_area_ratio"]:
            return f"covers {largest / page_area:.0%} of page {i} (safety limit)", rects
        if safety.get("protect_largest_image"):
            others = [_area(d.rect) for d in page_draws if d.objgen != objgen and d.subtype == "/Image"]
            if not others or largest >= max(others):
                return f"would remove the largest image on page {i}", rects
    return None, rects


def find_hathitrust_watermarks(pdf: pikepdf.Pdf, config: Dict, exclude_pages: Iterable[int] = ()) -> WatermarkScan:
    """Find the configured watermark keys inside each page's nested HathiTrust XObject dict."""
    wm = config["services"]["hathitrust"]["watermarks"]
    candidates: Dict[Tuple[int, int], Tuple[str, List[int]]] = {}
    for i, page in enumerate(pdf.pages):
        nested = _nested_xobjects(page, wm["nested_xobject_path"])
        if nested is None:
            continue
        for key in wm["watermark_keys"]:
            if key in nested:
                candidates.setdefault(nested[key].objgen, (key, []))[1].append(i)

    result = WatermarkScan()
    draws = _DrawCache(pdf)
    excluded = set(exclude_pages)
    for objgen, (name, pages) in candidates.items():
        problem, rects = _object_safety_problem(pdf, objgen, config, draws, excluded)
        if problem:
            result.rejected.append((name, problem))
        else:
            result.found.append(Watermark(name, objgen, pages, rects))
    return result



def _generic_rule(name: str, xobj: pikepdf.Object, rules: Dict, frequent: Set[int]) -> Optional[str]:
    """Which generic rule (if any) flags this page-level XObject."""
    if name in rules.get("xobject_names", []):
        return "name"
    if xobj.get("/Subtype") == "/Image" and "/Width" in xobj:
        width = int(xobj.Width)
        if width in rules.get("fixed_widths", []):
            return "fixed width"
        if width in frequent:
            return "frequent width"
    if rules.get("transparency_groups") and xobj.get("/Subtype") == "/Form":
        group = xobj.get("/Group")
        if group is not None and group.get("/S") == "/Transparency":
            return "transparency group"
    return None


def _on_layer(xobj: pikepdf.Object, layer_names: Set[str]) -> bool:
    """True if the XObject's /OC (an OCG, or an OCMD listing OCGs) names one of layer_names."""
    oc = xobj.get("/OC")
    if oc is None:
        return False
    groups = oc.get("/OCGs") if oc.get("/Type") == "/OCMD" else oc
    if groups is None:
        return False
    if not isinstance(groups, pikepdf.Array):
        groups = [groups]
    return any(str(g.get("/Name", "")) in layer_names for g in groups)


def find_generic_watermarks(pdf: pikepdf.Pdf, config: Dict, exclude_pages: Iterable[int] = ()) -> WatermarkScan:
    """Fallback rules for non-Google files; every candidate must pass the safety limits.

    An image width is "frequent" when it is drawn on at least frequent_width_ratio of the
    pages (counted once per page). exclude_pages (promo pages) don't count.
    """
    rules = config["general"]["generic_rules"]
    excluded = set(exclude_pages)
    eligible = [i for i in range(len(pdf.pages)) if i not in excluded]
    page_xobjects = {i: pdf.pages[i].resources.get("/XObject") or {} for i in eligible}

    width_pages: Dict[int, Set[int]] = {}
    for i, xobjects in page_xobjects.items():
        for xobj in xobjects.values():
            if xobj.get("/Subtype") == "/Image" and "/Width" in xobj:
                width_pages.setdefault(int(xobj.Width), set()).add(i)
    frequent = {w for w, pages in width_pages.items()
                if len(pages) / len(eligible) >= rules["frequent_width_ratio"]}

    candidates: Dict[Tuple[int, int], Tuple[str, List[int]]] = {}
    for i, xobjects in page_xobjects.items():
        for name, xobj in xobjects.items():
            if _generic_rule(name, xobj, rules, frequent):
                candidates.setdefault(xobj.objgen, (name, []))[1].append(i)

    draws = _DrawCache(pdf)
    layer_names = set(rules.get("ocg_names", []))
    layer_forms: Dict[Tuple[int, int], bool] = {}
    for i in eligible:
        for draw in draws(i):
            if draw.subtype != "/Image" or not draw.ancestors:
                continue
            for objgen in draw.ancestors:
                if objgen not in layer_forms:
                    layer_forms[objgen] = _on_layer(pdf.get_object(objgen), layer_names)
            if any(layer_forms[a] for a in draw.ancestors):
                pages = candidates.setdefault(draw.objgen, (draw.name, []))[1]
                if i not in pages:
                    pages.append(i)

    result = WatermarkScan()
    for objgen, (name, pages) in candidates.items():
        problem, rects = _object_safety_problem(pdf, objgen, config, draws, excluded)
        if problem:
            result.rejected.append((name, problem))
        else:
            result.found.append(Watermark(name, objgen, pages, rects))
    return result



@dataclass
class TextStamp:
    """A stamp phrase found on a page, with its box in PDF user space."""
    phrase: str
    page: int
    rect: Rect


@dataclass
class StampScan:
    found: List[TextStamp] = field(default_factory=list)      # repeated: to be inpainted
    reported: List[TextStamp] = field(default_factory=list)   # not repeated or too big: kept


def _to_pdf_rect(r: pymupdf.Rect, page: pymupdf.Page) -> Rect:
    """PyMuPDF rect (origin top-left) to PDF user space (origin bottom-left)."""
    h = page.rect.height
    return (r.x0, h - r.y1, r.x1, h - r.y0)


def _ocr_phrase_rects(page: pymupdf.Page, phrases: List[str], config: Dict) -> List[Tuple[str, Rect]]:
    """Run Tesseract on a render of the page and return the boxes of each phrase."""
    proc = config["processing"]
    dpi = proc["ocr_dpi"]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    lines: Dict[Tuple[int, int, int], List[Tuple[str, int, int, int, int]]] = {}
    for k, word in enumerate(data["text"]):
        token = re.sub(r"^\W+|\W+$", "", word).lower()
        if not token or float(data["conf"][k]) < proc["ocr_min_confidence"]:
            continue
        key = (data["block_num"][k], data["par_num"][k], data["line_num"][k])
        lines.setdefault(key, []).append((token, data["left"][k], data["top"][k],
                                          data["width"][k], data["height"][k]))

    scale = dpi / 72
    height = page.rect.height
    hits = []
    for words in lines.values():
        tokens = [w[0] for w in words]
        for phrase in phrases:
            target = phrase.split()
            for start in range(len(tokens) - len(target) + 1):
                if tokens[start:start + len(target)] != target:
                    continue
                span = words[start:start + len(target)]
                left = min(w[1] for w in span) / scale
                right = max(w[1] + w[3] for w in span) / scale
                top = min(w[2] for w in span) / scale
                bottom = max(w[2] + w[4] for w in span) / scale
                hits.append((phrase, (left, height - bottom, right, height - top)))
    return hits


def _page_phrase_rects(page: pymupdf.Page, phrases: List[str], config: Dict) -> List[Tuple[str, Rect]]:
    """Boxes of each phrase on the page: from the text layer if it has one, else from OCR."""
    if not page.get_text().strip():
        return _ocr_phrase_rects(page, phrases, config)
    hits = []
    for phrase in phrases:
        for r in page.search_for(phrase):
            hits.append((phrase, _to_pdf_rect(r, page)))
    return hits


def find_text_stamps(doc: pymupdf.Document, config: Dict, service: Optional[str],
                     exclude_pages: Iterable[int] = ()) -> StampScan:
    """Find configured stamp phrases; only phrases repeated on enough pages are to be removed.

    Phrases come from general.text_signatures, plus internet_archive text_patterns for IA
    files. A phrase must appear on at least stamp_min_page_ratio of the (non-excluded)
    pages, in a box no larger than max_stamp_page_area_ratio of the page; anything else is
    only reported, so that ordinary words in the book's text are never painted out.
    """
    proc = config["processing"]
    result = StampScan()
    if not proc.get("enable_ocr_fallback"):
        return result
    phrases = list(config["general"]["text_signatures"])
    if service == "internet_archive":
        phrases += config["services"]["internet_archive"]["watermarks"]["text_patterns"]
    phrases = [normalize_text(p) for p in phrases]

    excluded = set(exclude_pages)
    eligible = [i for i in range(len(doc)) if i not in excluded]
    candidates: List[TextStamp] = []
    for i in eligible:
        page = doc[i]
        page_area = page.rect.width * page.rect.height
        for phrase, rect in _page_phrase_rects(page, phrases, config):
            stamp = TextStamp(phrase, i, rect)
            if _area(rect) / page_area > proc["max_stamp_page_area_ratio"]:
                result.reported.append(stamp)
            else:
                candidates.append(stamp)

    pages_with: Dict[str, Set[int]] = {}
    for stamp in candidates:
        pages_with.setdefault(stamp.phrase, set()).add(stamp.page)
    for stamp in candidates:
        if len(pages_with[stamp.phrase]) / len(eligible) >= proc["stamp_min_page_ratio"]:
            result.found.append(stamp)
        else:
            result.reported.append(stamp)
    return result



def image_usage(doc: pymupdf.Document) -> Dict[int, int]:
    """Number of pages that use each image xref."""
    usage: Dict[int, int] = {}
    for page in doc:
        for xref in {img[0] for img in page.get_images(full=True)}:
            usage[xref] = usage.get(xref, 0) + 1
    return usage


def inpaint_stamp(doc: pymupdf.Document, page_index: int, rect: Rect, config: Dict,
                  usage: Dict[int, int]) -> Optional[str]:
    """Paint a baked-in stamp out of the page's scan image(s); return a problem or None.

    Only pixels of the image(s) under rect (PDF user space) change: they are filled from
    their surroundings with cv2.inpaint, and the image is stored losslessly. The page's
    text layer and all other content are untouched. Refuses rotated, shared or masked
    images rather than risk damage.
    """
    page = doc[page_index]
    height = page.rect.height
    target = pymupdf.Rect(rect[0], height - rect[3], rect[2], height - rect[1])
    jobs = []
    for img in page.get_images(full=True):
        xref, smask = img[0], img[1]
        for bbox, m in page.get_image_rects(xref, transform=True):
            if not bbox.intersects(target):
                continue
            if abs(m.b) > 1e-6 or abs(m.c) > 1e-6 or m.a <= 0 or m.d <= 0:
                return "image under the stamp is rotated or flipped"
            if usage.get(xref, 0) > 1:
                return "image under the stamp is shared by several pages"
            if smask:
                return "image under the stamp has a soft mask"
            if doc.xref_get_key(xref, "Mask")[0] != "null":
                return "image under the stamp has a mask"
            jobs.append((xref, bbox))
    if not jobs:
        return "no image under the stamp"

    radius = config["processing"]["inpaint_radius"]
    for xref, bbox in jobs:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n not in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        w, h, n = pix.width, pix.height, pix.n
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, pix.stride)[:, :w * n].reshape(h, w, n)
        clip = target & bbox
        x0 = max(math.floor((clip.x0 - bbox.x0) / bbox.width * w), 0)
        x1 = min(math.ceil((clip.x1 - bbox.x0) / bbox.width * w), w)
        y0 = max(math.floor((clip.y0 - bbox.y0) / bbox.height * h), 0)
        y1 = min(math.ceil((clip.y1 - bbox.y0) / bbox.height * h), h)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        source = arr[:, :, 0] if n == 1 else arr
        filled = cv2.inpaint(np.ascontiguousarray(source), mask, radius, cv2.INPAINT_TELEA)
        colorspace = pymupdf.csGRAY if n == 1 else pymupdf.csRGB
        page.replace_image(xref, pixmap=pymupdf.Pixmap(colorspace, w, h, filled.tobytes(), False))
    return None



_CONTENT_TYPES = {"fill-path", "stroke-path", "fill-text", "stroke-text", "ignore-text",
                  "fill-image", "fill-imgmask", "fill-shade"}
_TOLERANCE = 0.5  # points
MAX_WATERMARK_INSIDE_SCAN = 0.2  # a watermark more than 20% over the scan didn't sit in a margin


def margin_crop(page: pymupdf.Page, removed: List[Rect], config: Dict) -> Tuple[Optional[Rect], Optional[str]]:
    """Crop box (PDF user space) that trims margins vacated by removed watermarks.

    Call on the staged page, after the watermarks have been emptied. Returns
    (rect, None) to crop, (None, None) if there is nothing to trim, or (None, reason)
    when a guard refuses: the crop must be exactly the page's single image, every
    removed watermark must have lain outside it, nothing else may be drawn outside
    it, and no margin may exceed max_trim_margin_ratio of the page.
    """
    if page.rotation or page.cropbox != page.mediabox:
        return None, "page already has a crop box or rotation"
    log = [(kind, pymupdf.Rect(r)) for kind, r in page.get_bboxlog() if kind in _CONTENT_TYPES]
    images = [r for kind, r in log if kind in ("fill-image", "fill-imgmask")]
    if len(images) != 1:
        return None, "page does not draw exactly one image"
    scan = images[0]
    mb = page.mediabox
    height = mb.height
    scan_pdf = (scan.x0, height - scan.y1, scan.x1, height - scan.y0)

    def inside_fraction(r: Rect) -> float:
        w = min(r[2], scan_pdf[2]) - max(r[0], scan_pdf[0])
        h = min(r[3], scan_pdf[3]) - max(r[1], scan_pdf[1])
        return max(w, 0) * max(h, 0) / _area(r) if _area(r) else 0.0

    if any(inside_fraction(r) > MAX_WATERMARK_INSIDE_SCAN for r in removed):
        return None, "a removed watermark overlapped the scan"
    grown = pymupdf.Rect(scan.x0 - _TOLERANCE, scan.y0 - _TOLERANCE, scan.x1 + _TOLERANCE, scan.y1 + _TOLERANCE)
    # Text is judged by its words (what extraction can lose); runs' boxes include leading spaces.
    # Only the part of each item that is on the page matters: beyond the edge it is never shown.
    drawn = [r & mb for kind, r in log if r is not scan and "text" not in kind]
    drawn += [pymupdf.Rect(w[:4]) & mb for w in page.get_text("words")]
    if any(not r.is_empty and not grown.contains(r) for r in drawn):
        return None, "content outside the scan"
    margins = (scan.x0 - mb.x0, mb.x1 - scan.x1, scan.y0 - mb.y0, mb.y1 - scan.y1)
    if max(margins) <= _TOLERANCE:
        return None, None
    limit = config["processing"]["max_trim_margin_ratio"]
    if max(margins[:2]) > limit * mb.width or max(margins[2:]) > limit * height:
        return None, "margin too large to be a watermark strip"
    return scan_pdf, None


def neutralize_xobject(pdf: pikepdf.Pdf, objgen: Tuple[int, int]) -> None:
    """Turn a shared XObject into an empty Form XObject, in place.

    Every page that invokes it now draws nothing. Page content streams are left
    byte-identical, so no content-stream parsing or rewriting can go wrong.
    """
    obj = pdf.get_object(objgen)
    for key in list(obj.keys()):
        if key not in ("/Type", "/Length"):
            del obj[key]
    obj.write(b"")
    obj.Type = pikepdf.Name.XObject
    obj.Subtype = pikepdf.Name.Form
    obj.BBox = pikepdf.Array([0, 0, 0, 0])


INK_LEVEL = 200          # gray value below which a pixel counts as ink
MIN_INK_FRACTION = 0.005  # a page with this much ink is not blank
MASK_MARGIN_PX = 2


def _render_gray(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]


def _ink_fraction(img: np.ndarray) -> float:
    return float((img < INK_LEVEL).mean())


def _excuse_mask(shape: Tuple[int, int], rects: List[Rect], page_height: float, dpi: int) -> np.ndarray:
    """Boolean mask that is False inside the given PDF-space rects (plus a margin)."""
    keep = np.ones(shape, dtype=bool)
    scale = dpi / 72
    for x0, y0, x1, y1 in rects:
        left = max(int(x0 * scale) - MASK_MARGIN_PX, 0)
        right = int(x1 * scale) + MASK_MARGIN_PX + 1
        top = max(int((page_height - y1) * scale) - MASK_MARGIN_PX, 0)
        bottom = int((page_height - y0) * scale) + MASK_MARGIN_PX + 1
        keep[top:bottom, left:right] = False
    return keep


def _crop_hides_content(page: pymupdf.Page, crop: pymupdf.Rect, rects: List[Rect], dpi: int) -> bool:
    """True if the original page has ink outside crop that isn't a removed watermark."""
    img = _render_gray(page, dpi)
    hidden = _excuse_mask(img.shape, rects, page.rect.height, dpi)
    scale = dpi / 72
    top = max(int(crop.y0 * scale) - MASK_MARGIN_PX, 0)
    left = max(int(crop.x0 * scale) - MASK_MARGIN_PX, 0)
    hidden[top:int(crop.y1 * scale) + MASK_MARGIN_PX + 1, left:int(crop.x1 * scale) + MASK_MARGIN_PX + 1] = False
    return bool(((img < INK_LEVEL) & hidden).any())


def _crop_hides_text(page: pymupdf.Page, crop: pymupdf.Rect) -> bool:
    """True if any word of the original page lies on the page but outside crop (top-left coordinates)."""
    grown = pymupdf.Rect(crop.x0 - _TOLERANCE, crop.y0 - _TOLERANCE, crop.x1 + _TOLERANCE, crop.y1 + _TOLERANCE)
    visible = [pymupdf.Rect(w[:4]) & page.mediabox for w in page.get_text("words")]
    return any(not r.is_empty and not grown.contains(r) for r in visible)


def _syntax_problems(path: str) -> List[str]:
    with pikepdf.open(path) as pdf, warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="pikepdf is missing some specialized decoders")
        return [str(p) for p in pdf.check_pdf_syntax()]


def validate_output(original: str, output: str, promo_pages: List[int],
                    watermark_rects: Dict[int, List[Rect]], config: Dict) -> List[str]:
    """Compare output against original; return a list of problems (empty means OK).

    Every kept page must have identical text and render identically except inside the
    rects of removed watermarks. Cropped pages are compared with their crop box removed
    (same page, same pixel grid), and the crop itself must hide no ink or words other
    than removed watermarks. Page numbers in messages are 1-based, original numbering.
    """
    safety = config["safety"]
    dpi = safety["render_dpi"]
    try:
        out_doc = pymupdf.open(output)
        syntax = _syntax_problems(output)
    except Exception as e:
        return [f"output unreadable: {e}"]
    problems = [f"structure: {p}" for p in syntax]

    orig_doc = pymupdf.open(original)
    kept = [i for i in range(len(orig_doc)) if i not in set(promo_pages)]
    if len(out_doc) != len(kept):
        return problems + [f"expected {len(kept)} pages, found {len(out_doc)}"]

    for j, i in enumerate(kept):
        before, after = orig_doc[i], out_doc[j]
        label = f"page {i + 1}"
        crop = after.cropbox
        if crop != after.mediabox:
            if _crop_hides_content(before, crop, watermark_rects.get(i, []), dpi):
                problems.append(f"{label} crop hides content")
            if _crop_hides_text(before, crop):
                problems.append(f"{label} crop hides text")
            after.set_cropbox(after.mediabox)  # in memory only: compare the full page
        if before.get_text() != after.get_text():
            problems.append(f"text differs on {label}")
        img_before, img_after = _render_gray(before, dpi), _render_gray(after, dpi)
        if safety.get("fail_on_blank_page") and _ink_fraction(img_before) >= MIN_INK_FRACTION \
                and _ink_fraction(img_after) < MIN_INK_FRACTION / 5:
            problems.append(f"{label} went blank")
            continue
        keep = _excuse_mask(img_before.shape, watermark_rects.get(i, []), before.rect.height, dpi)
        diff = np.abs(img_before.astype(np.int16) - img_after.astype(np.int16))[keep]
        if diff.size and diff.mean() > safety["max_render_diff"]:
            problems.append(f"{label} rendering changed (mean diff {diff.mean():.1f})")
    return problems


@dataclass
class FileResult:
    """Outcome of processing one PDF."""
    status: str                                   # cleaned | skipped | failed | dry-run
    reasons: List[str] = field(default_factory=list)
    promo_pages: List[int] = field(default_factory=list)
    suspects: List[int] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)            # watermark names
    rejected: List[Tuple[str, str]] = field(default_factory=list)
    service: Optional[str] = None
    stamps_removed: List[str] = field(default_factory=list)     # "page N: phrase"
    stamps_reported: List[str] = field(default_factory=list)    # found but not repeated / too big
    notes: List[str] = field(default_factory=list)              # non-fatal problems
    cropped_pages: List[int] = field(default_factory=list)      # original indices trimmed to their scan


def _safe_delete(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def _find_xobject_watermarks(pdf: pikepdf.Pdf, config: Dict, service: Optional[str],
                             promo_pages: List[int]) -> WatermarkScan:
    """Google files use fingerprints only; everything else gets service rules plus generic rules."""
    if service == "google":
        return find_watermarks(pdf, config, exclude_pages=promo_pages)
    scan = find_hathitrust_watermarks(pdf, config, promo_pages) if service == "hathitrust" else WatermarkScan()
    claimed = {w.objgen for w in scan.found}
    generic = find_generic_watermarks(pdf, config, exclude_pages=promo_pages)
    scan.found += [w for w in generic.found if w.objgen not in claimed]
    scan.rejected += generic.rejected
    return scan


def _label(stamp: TextStamp) -> str:
    return f"page {stamp.page + 1}: {stamp.phrase}"


def process_pdf(input_path: str, output_path: str, config: Dict, dry_run: bool = False) -> FileResult:
    """Remove wrapper pages, watermark XObjects and repeated baked-in stamps from one PDF.

    Work happens in temp files next to output_path. The output is only written, via
    an atomic replace, after validate_output finds no problems; otherwise nothing is
    written. Raises ValueError if output_path is the input file.
    """
    if os.path.exists(output_path) and os.path.samefile(input_path, output_path):
        raise ValueError(f"Output path is the input file: {output_path}")
    try:
        doc = pymupdf.open(input_path)
        with pikepdf.open(input_path) as probe:
            service = identify_service(doc, probe, config)
    except Exception as e:
        return FileResult("failed", [f"unreadable: {e}"])

    promo = find_promo_pages(doc, config, service) if service else PromoScan()
    result = FileResult("dry-run" if dry_run else "cleaned", promo_pages=promo.pages,
                        suspects=promo.suspects, service=service)
    if len(promo.pages) > config["safety"]["max_promo_pages"]:
        result.status = "failed"
        result.reasons.append(f"{len(promo.pages)} promotional pages found, more than the "
                              f"safety limit of {config['safety']['max_promo_pages']}")
        return result

    stamps = find_text_stamps(doc, config, service, exclude_pages=promo.pages)
    result.stamps_reported = [_label(s) for s in stamps.reported]
    stage1, stage2 = output_path + ".stage1.tmp", output_path + ".stage2.tmp"
    try:
        with pikepdf.open(input_path) as pdf:
            scan = _find_xobject_watermarks(pdf, config, service, promo.pages)
            result.removed = list(dict.fromkeys(w.name for w in scan.found))
            result.rejected = scan.rejected
            if not promo.pages and not scan.found and not stamps.found:
                result.status = "skipped"
                result.reasons.append("nothing to remove")
                return result
            if dry_run:
                result.stamps_removed = [_label(s) for s in stamps.found]
                return result
            for wm in scan.found:
                neutralize_xobject(pdf, wm.objgen)
            pdf.save(stage1)

        rects: Dict[int, List[Rect]] = {}
        for wm in scan.found:
            for page, page_rects in wm.rects.items():
                rects.setdefault(page, []).extend(page_rects)
        watermark_rects = {page: list(page_rects) for page, page_rects in rects.items()}

        staged = pymupdf.open(stage1)
        usage = image_usage(staged)
        for stamp in stamps.found:
            problem = inpaint_stamp(staged, stamp.page, stamp.rect, config, usage)
            if problem:
                result.notes.append(f"{_label(stamp)} not removed: {problem}")
            else:
                result.stamps_removed.append(_label(stamp))
                rects.setdefault(stamp.page, []).append(stamp.rect)
        if config["processing"].get("trim_vacated_margins"):
            for page_index in sorted(set(watermark_rects) - set(promo.pages)):
                page = staged[page_index]
                crop, _ = margin_crop(page, watermark_rects[page_index], config)
                if crop:
                    height = page.mediabox.height
                    page.set_cropbox(pymupdf.Rect(crop[0], height - crop[3], crop[2], height - crop[1])
                                     & page.mediabox)
                    result.cropped_pages.append(page_index)
        if promo.pages:
            staged.delete_pages(promo.pages)
        staged.save(stage2, garbage=1)
        staged.close()

        problems = validate_output(input_path, stage2, promo.pages, rects, config)
        if problems:
            result.status = "failed"
            result.reasons.extend(problems)
            return result
        os.replace(stage2, output_path)
        return result
    finally:
        _safe_delete(stage1)
        _safe_delete(stage2)


MAX_LOGGED_REJECTIONS = 3


def _log_result(name: str, result: FileResult, verbose: bool = False) -> None:
    if result.suspects:
        pages = ", ".join(str(i + 1) for i in result.suspects)
        logging.warning(f"{name}: possible promotional page(s) kept for review: {pages}")
    shown = result.rejected if verbose else result.rejected[:MAX_LOGGED_REJECTIONS]
    for wm_name, reason in shown:
        logging.info(f"{name}: {wm_name} not removed: {reason}")
    if len(result.rejected) > len(shown):
        logging.info(f"{name}: ... and {len(result.rejected) - len(shown)} more candidates not removed "
                     f"(use --verbose to list them)")
    if result.stamps_removed:
        logging.info(f"{name}: stamps removed: {', '.join(result.stamps_removed)}")
    if result.stamps_reported:
        logging.warning(f"{name}: stamp text kept for review (not repeated or too large): "
                        f"{', '.join(result.stamps_reported)}")
    for note in result.notes:
        logging.warning(f"{name}: {note}")
    if result.cropped_pages:
        logging.info(f"{name}: trimmed blank watermark margins on {len(result.cropped_pages)} page(s)")
    service = f" [{result.service or 'unknown service'}]"
    detail = f" (promo pages {[i + 1 for i in result.promo_pages]}, watermarks {result.removed})"
    level = logging.ERROR if result.status == "failed" else logging.INFO
    logging.log(level, f"{result.status.upper()}: {name}{service}{detail if result.status != 'skipped' else ''}")
    for reason in result.reasons:
        logging.log(level, f"    {reason}")


def batch_process(input_dir: str, output_dir: str, config: Dict,
                  dry_run: bool = False, verbose: bool = False) -> Dict[str, FileResult]:
    """Process every PDF directly inside input_dir, in name order.

    Raises:
        FileNotFoundError: If input_dir doesn't exist.
        ValueError: If output_dir is input_dir (originals must never be overwritten).
    """
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    if os.path.realpath(input_dir) == os.path.realpath(output_dir):
        raise ValueError(f"Output directory is the input directory: {output_dir}")
    if not dry_run:
        os.makedirs(output_dir, exist_ok=True)

    results: Dict[str, FileResult] = {}
    for name in sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".pdf")):
        result = process_pdf(os.path.join(input_dir, name), os.path.join(output_dir, name),
                             config, dry_run=dry_run)
        _log_result(name, result, verbose)
        results[name] = result

    counts = {s: sum(r.status == s for r in results.values())
              for s in ("cleaned", "dry-run", "skipped", "failed")}
    logging.info("Done: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n or s == "failed"))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point. Returns 0 on success, 1 if any file failed, 2 on usage errors."""
    parser = argparse.ArgumentParser(description="Remove Google Books watermarks and promotional pages.")
    parser.add_argument("input_dir", nargs="?", default="./Books",
                        help="Directory containing PDFs to process (default: ./Books)")
    parser.add_argument("-o", "--output-dir",
                        help="Directory for cleaned PDFs (default: <input_dir>/Clean)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help="YAML configuration (default: watermark_config.yaml next to this script)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be removed from each file without writing anything")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="List every rejected watermark candidate instead of a summary")
    args = parser.parse_args(argv)
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

    output_dir = args.output_dir or os.path.join(args.input_dir, "Clean")
    try:
        results = batch_process(args.input_dir, output_dir, load_config(args.config), args.dry_run,
                                args.verbose)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 2
    return 1 if any(r.status == "failed" for r in results.values()) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
