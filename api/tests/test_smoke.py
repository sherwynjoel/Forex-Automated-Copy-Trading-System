def test_api_package_importable():
    import api
    assert api.__version__ == "0.1.0"
