import pytest


@pytest.mark.parametrize("body", [{"test": "test"}, {"test": "test"}])
def test_connection_probe(client, monkeypatch, body):
    monkeypatch.setenv("TILDA_WEBHOOK_SECRET", "secret")
    from app.config import clear_settings_cache

    clear_settings_cache()
    response = client.post(
        "/payments/tilda/webhook", data=body, headers={"X-Tilda-Webhook-Key": "secret"}
    )
    assert response.status_code == 200


def test_forged_webhook(client):
    assert client.post("/payments/tilda/webhook", json={"Email": "a@b.com"}).status_code == 403


def test_bounded_intake(client, monkeypatch):
    monkeypatch.setenv("TILDA_WEBHOOK_SECRET", "secret")
    from app.config import clear_settings_cache

    clear_settings_cache()
    headers = {"X-Tilda-Webhook-Key": "secret", "Content-Type": "application/json"}
    assert (
        client.post("/payments/tilda/webhook", content="x" * 65537, headers=headers).status_code
        == 413
    )
    for body in ["[]", '{"test":"test","test":"test"}', "bad"]:
        assert (
            client.post("/payments/tilda/webhook", content=body, headers=headers).status_code == 400
        )
