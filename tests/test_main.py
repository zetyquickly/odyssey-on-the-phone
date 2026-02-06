"""Basic tests for the Odyssey video streaming app."""


def test_basic():
    """Basic test to ensure pytest works."""
    assert True


def test_imports():
    """Test that we can import the main modules."""
    import flask
    import PIL
    from odyssey import Odyssey

    assert flask is not None
    assert PIL is not None
    assert Odyssey is not None
