import pytest
import yaml

import pdf_watermark_remover as pwr
from conftest import CONFIG_PATH


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return str(p)


def test_shipped_config_loads_google_watermark_specs():
    config = pwr.load_config(CONFIG_PATH)
    names = [x["name"] for x in config["services"]["google"]["watermarks"]["xobjects"]]
    assert names == ["/Wm", "/Form0"]


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        pwr.load_config("/nonexistent/config.yaml")


@pytest.mark.parametrize("text", ["", "- a list\n", "services: {}\n"])
def test_malformed_config_raises(tmp_path, text):
    with pytest.raises(yaml.YAMLError):
        pwr.load_config(write(tmp_path, text))


def test_config_without_safety_section_raises(tmp_path):
    text = open(CONFIG_PATH).read().replace("\nsafety:", "\nnot_safety:")
    with pytest.raises(yaml.YAMLError, match="safety"):
        pwr.load_config(write(tmp_path, text))
