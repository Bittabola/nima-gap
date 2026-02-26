from src.config import Config, load_config


def test_load_config_returns_config():
    config = load_config()
    assert isinstance(config, Config)


def test_config_has_sources():
    config = load_config()
    assert isinstance(config.sources, list)
