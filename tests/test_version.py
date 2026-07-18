from taskq import __version__


def test_version_is_prealpha() -> None:
    assert __version__.startswith("0.1.0")
