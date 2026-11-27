from fpl_manager.config import load_config
from fpl_manager.pipeline import planner_config, projection_config


def test_config_loads_into_model_and_planner_settings():
    cfg = load_config()
    assert int(cfg["team_id"]) > 0
    pc = projection_config(cfg)
    pl = planner_config(cfg)
    assert pc.rate_half_life == float(cfg["projection"]["rate_half_life"])
    assert pl.horizon == int(cfg["planner"]["horizon"])
    assert pl.transfer_weeks >= 1 and 0 < pl.decay <= 1
