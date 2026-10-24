import logging
import os

import pytest

import pdf_watermark_remover as pwr
from conftest import CONFIG_PATH, make_hathi_pdf, make_pdf, make_scanned_pdf


@pytest.fixture
def books(tmp_path):
    d = tmp_path / "Books"
    d.mkdir()
    make_pdf(d / "good.pdf")
    make_pdf(d / "SHOUTY.PDF")
    make_pdf(d / "other.pdf", producer="Adobe Acrobat", cover=None, wm_name=None)
    (d / "notes.txt").write_text("not a pdf")
    return d


def test_batch_processes_only_pdfs_in_name_order(books, tmp_path, cfg):
    results = pwr.batch_process(str(books), str(tmp_path / "out"), cfg)
    assert list(results) == ["SHOUTY.PDF", "good.pdf", "other.pdf"]
    assert [r.status for r in results.values()] == ["cleaned", "cleaned", "skipped"]
    assert sorted(os.listdir(tmp_path / "out")) == ["SHOUTY.PDF", "good.pdf"]


def test_batch_refuses_output_dir_equal_to_input(books, cfg):
    before = sorted(os.listdir(books))
    with pytest.raises(ValueError):
        pwr.batch_process(str(books), str(books / ".." / "Books"), cfg)
    assert sorted(os.listdir(books)) == before


def test_batch_missing_input_dir_raises(tmp_path, cfg):
    with pytest.raises(FileNotFoundError):
        pwr.batch_process(str(tmp_path / "nope"), str(tmp_path / "out"), cfg)


def test_batch_dry_run_creates_nothing(books, tmp_path, cfg):
    results = pwr.batch_process(str(books), str(tmp_path / "out"), cfg, dry_run=True)
    assert results["good.pdf"].status == "dry-run"
    assert not (tmp_path / "out").exists()


def test_main_defaults_output_to_clean_subdir(books):
    assert pwr.main([str(books)]) == 0
    assert sorted(os.listdir(books / "Clean")) == ["SHOUTY.PDF", "good.pdf"]


def test_main_uses_config_next_to_script_from_any_cwd(books, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert pwr.main([str(books), "-o", str(tmp_path / "out")]) == 0


def test_main_accepts_explicit_config(books, tmp_path):
    assert pwr.main([str(books), "-o", str(tmp_path / "out"), "--config", CONFIG_PATH]) == 0


def test_main_returns_1_when_a_file_fails(tmp_path):
    d = tmp_path / "Books"
    d.mkdir()
    (d / "broken.pdf").write_bytes(b"not a pdf")
    assert pwr.main([str(d)]) == 1


def test_main_returns_2_for_same_input_and_output(books, caplog):
    assert pwr.main([str(books), "-o", str(books)]) == 2
    assert "input file" in caplog.text or "input directory" in caplog.text


def test_main_returns_2_for_missing_input(tmp_path, caplog):
    assert pwr.main([str(tmp_path / "nope")]) == 2
    assert "not found" in caplog.text


def test_main_logs_suspects_rejections_and_failures(tmp_path, caplog):
    d = tmp_path / "Books"
    d.mkdir()
    make_pdf(d / "odd.pdf", cover=None, wm_height=200,
             body_text={0: ["this is a digital copy of a book"]})
    (d / "broken.pdf").write_bytes(b"not a pdf")
    with caplog.at_level(logging.INFO):
        pwr.main([str(d), "--dry-run"])
    assert "odd.pdf: possible promotional page(s) kept for review: 1" in caplog.text
    assert "odd.pdf: /Wm not removed: Height 200 is not 204" in caplog.text
    assert "FAILED: broken.pdf" in caplog.text
    assert "1 failed" in caplog.text


def test_main_logs_service_stamps_and_notes(tmp_path, caplog):
    d = tmp_path / "Books"
    d.mkdir()
    make_scanned_pdf(d / "s.pdf", pages=4, stamp_pages=[0, 1, 2], text_layer="CONFIDENTIAL")
    make_pdf(d / "v.pdf", producer="Some Scanner", cover=None, wm_name="/Form0", wm_kind="form",
             wm_placement=(273, 10, 1, 1), scan_placement=(0, 0, 100, 100),
             body_text={i: ["SAMPLE"] for i in range(3)}, body_pages=3)
    with caplog.at_level(logging.INFO):
        assert pwr.main([str(d)]) == 0
    text = caplog.text
    assert "CLEANED: s.pdf [unknown service]" in text
    assert "s.pdf: stamps removed: page 1: confidential, page 2: confidential, page 3: confidential" in text
    assert "v.pdf: page 1: sample not removed: no image under the stamp" in text


def test_main_logs_unrepeated_stamps_as_kept(tmp_path, caplog):
    d = tmp_path / "Books"
    d.mkdir()
    make_scanned_pdf(d / "s.pdf", pages=4, stamp_pages=[1], text_layer="CONFIDENTIAL")
    with caplog.at_level(logging.INFO):
        pwr.main([str(d), "--dry-run"])
    assert "s.pdf: stamp text kept for review (not repeated or too large): page 2: confidential" in caplog.text


def many_rejections(tmp_path):
    d = tmp_path / "Books"
    d.mkdir()
    # Ten same-width page scans: all flagged by the frequent-width rule, all rejected by safety.
    make_pdf(d / "scans.pdf", producer="Some Scanner", cover=None, wm_name=None, body_pages=10)
    return d


def test_long_rejection_lists_are_summarised(tmp_path, caplog):
    d = many_rejections(tmp_path)
    with caplog.at_level(logging.INFO):
        pwr.main([str(d), "--dry-run"])
    assert caplog.text.count("/J2i0 not removed") == 3
    assert "scans.pdf: ... and 7 more candidates not removed (use --verbose to list them)" in caplog.text


def test_verbose_lists_every_rejection(tmp_path, caplog):
    d = many_rejections(tmp_path)
    with caplog.at_level(logging.INFO):
        pwr.main([str(d), "--dry-run", "--verbose"])
    assert caplog.text.count("/J2i0 not removed") == 10
    assert "more candidates not removed" not in caplog.text


def test_main_logs_trimmed_margins(tmp_path, caplog):
    d = tmp_path / "Books"
    d.mkdir()
    make_hathi_pdf(d / "h.pdf", pages=2, cover=True, scan_placement=(21, 31, 591, 761),
                   placement=(40, 5, 150, 20), producer="HathiTrust Image Server / PDF::API2")
    with caplog.at_level(logging.INFO):
        assert pwr.main([str(d)]) == 0
    assert "h.pdf: trimmed blank watermark margins on 2 page(s)" in caplog.text
