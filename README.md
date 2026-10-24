# Remove PDF Watermarks from Public Domain Books

Removes the wrapper pages and watermarks that digitization services add to public
domain book scans, while leaving the scanned pages themselves untouched.

Supported sources:

- **Google Books**: the "Digitized by Google" notice page and the small `/Wm`
  watermark drawn on every page.
- **HathiTrust**: the front notice page and the nested `/PxCB*` watermarks
  (the "Original from ..." footer and the "Generated through HathiTrust" margin strip).
- **Internet Archive / Microsoft**: the "Digitized by the Internet Archive" page.
- **Other PDFs**: generic fallback rules (repeated watermark-sized images, transparency
  groups, `Watermark` optional-content layers) and repeated text stamps baked into the scan.

## How it works

Each PDF goes through these steps:

1. **Identify the service** from the PDF Producer, embedded links and known object names.
2. **Find promotional pages** among the first and last few pages. A page is deleted only
   when several independent signals agree (notice text, service links, QR image, missing
   watermark).
3. **Find watermark XObjects** by fingerprint (name, size, bit depth, stencil mask, how many
   pages share the object, how much of the page each placement covers), then neutralize them
   in place rather than editing page content streams.
4. **Find repeated text stamps** in the text layer, or with Tesseract OCR on pages without
   one, and inpaint them out of the scan image with OpenCV.
5. **Trim vacated margins** where a removed watermark left a blank strip (optional).
6. **Validate the output** before writing it. Each page is rendered before and after cleaning
   and compared outside the removed regions. A page that goes blank, too many promotional
   pages, or a large render difference fails the file, and nothing is written for it.

Originals are never modified. Output is written atomically, only after validation passes.

## Requirements

- Python 3.15+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract), only used for stamps on pages
  without a text layer (`sudo apt install tesseract-ocr` on Debian/Ubuntu)

Python dependencies (see `pyproject.toml`): PyMuPDF, pikepdf, OpenCV, NumPy, pytesseract, PyYAML.

## Installation

```bash
git clone https://github.com/StephenGenusa/PDF-Watermark-Remove.git
cd PDF-Watermark-Remove
uv sync
```

## Usage

```bash
# Clean every PDF in ./Books, writing results to ./Books/Clean
uv run pdf_watermark_remover.py

# Clean a specific directory into a chosen output directory
uv run pdf_watermark_remover.py "/Downloaded PDFs" -o "/Downloaded PDFs/cleaned"

# Report what would be removed without writing anything
uv run pdf_watermark_remover.py "/Downloaded PDFs" --dry-run

# List every rejected watermark candidate, not just a summary
uv run pdf_watermark_remover.py "/Downloaded PDFs" -v
```

| Option | Description |
| --- | --- |
| `input_dir` | Directory of PDFs to process (default `./Books`). Only PDFs directly inside it are processed. |
| `-o`, `--output-dir` | Where cleaned PDFs go (default `<input_dir>/Clean`). Must differ from `input_dir`. |
| `--config` | YAML configuration file (default `watermark_config.yaml` next to the script). |
| `--dry-run` | Report findings for each file without writing output. |
| `-v`, `--verbose` | Log every rejected watermark candidate. |

Each file is reported as `CLEANED`, `SKIPPED` (nothing to remove), `DRY-RUN` or `FAILED`
(with the reasons). Possible promotional pages that didn't meet the signal threshold, and
stamp text that wasn't removed, are logged as warnings for manual review.

Exit codes: `0` success, `1` at least one file failed, `2` usage error (missing input
directory, output directory same as input).

## Configuration

All detection rules and safety limits live in `watermark_config.yaml`:

- `services.<name>.identify`: how a file is recognized as coming from that service.
- `services.<name>.promotional_pages`: which pages are searched and which signals count.
- `services.<name>.watermarks`: watermark fingerprints for that service.
- `safety`: hard limits applied to every file (largest image is always protected, maximum
  promotional pages, blank-page check, render comparison DPI and tolerance).
- `general.generic_rules`: fallback rules for files from unrecognized sources.
- `processing`: OCR resolution and confidence, stamp thresholds, inpaint radius, margin trimming.

To support a new watermark, add its fingerprint to the config rather than loosening the
safety limits.

## Testing

```bash
uv run pytest            # unit tests on synthetic PDFs, 100% coverage required
uv run pytest --cov      # with the coverage report
uv run pytest -m corpus  # slow acceptance tests against real PDFs in ./Books
```

The unit tests build small synthetic PDFs laid out like real Google Books and HathiTrust
scans, so they need no sample files. The corpus tests skip any service with no matching
PDFs in `./Books`.

## License

BSD 2-Clause. See [LICENSE.txt](LICENSE.txt).
