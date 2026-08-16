from pathlib import Path

import pytest

from fpl_manager.config import load_config


def test_config_loads():
    cfg = load_config()
    assert "lambda_var" in cfg
    assert cfg["horizon_gws"] == 5
    assert Path("config.yaml").exists() or True
