from importlib.metadata import version

from taskq import __version__


def test_version_is_prealpha() -> None:
    assert __version__ == "0.1.0a22"
    assert version("outlabs-taskq") == __version__
