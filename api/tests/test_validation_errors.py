"""A 422 keeps FastAPI's error shape but never echoes the request body:
the default "input" field carried passwords and MPINs back."""


def test_a_422_does_not_echo_the_body(app_client):
    r = app_client.post("/api/login", json={"password": "s3cret-Value", "mpin": "123456"})
    assert r.status_code == 422
    assert "s3cret-Value" not in r.text and "123456" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", "email"]
