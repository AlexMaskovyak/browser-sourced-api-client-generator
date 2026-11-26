"""Fixture checks establish backend behavior, not generator capabilities."""


def test_fixture_denies_messages_before_login_after_password_and_after_wrong_mfa(site, http_client):
    unauthenticated_response = http_client.get("/message?message_id=m1")
    assert unauthenticated_response.status_code == 401
    assert site.message_requests[-1]["authenticated"] is False

    password_response = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password_response.status_code == 303
    assert password_response.headers["location"] == "/mfa"
    assert http_client.get("/message?message_id=m1").status_code == 401

    rejected_response = http_client.post("/mfa", data={"code": "incorrect"})
    assert rejected_response.status_code == 401
    assert http_client.get("/message?message_id=m1").status_code == 401
    assert site.counts == {"password": 1, "mfa": 0, "app": 0}


def test_fixture_controls_and_schema_absence(site, signed_in_client):
    client = signed_in_client
    original_first_message = dict(site.messages["m1"])
    site.messages["m2"]["subject"] = "Updated second fixture message"
    assert client.get("/message?message_id=m2").json() == site.messages["m2"]
    assert client.get("/message?message_id=m1").json() == original_first_message
    assert client.get("/message?message_id=unknown").status_code == 404
    for path in ("/openapi.json", "/swagger.json", "/api-docs", "/graphql", "/unknown"):
        missing_response = client.get(path)
        assert missing_response.status_code == 404
        assert missing_response.json() == {"error": "Not found"}
    assert client.post("/graphql", json={"query": "{ __typename }"}).status_code == 404
    assert client.post("/items", json={"name": "Unused"}).status_code == 404
    site.sessions_active = False
    assert client.get("/message?message_id=m2").status_code == 401
