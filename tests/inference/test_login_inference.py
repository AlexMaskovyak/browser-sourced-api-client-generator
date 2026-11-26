"""The inferred public names and wire query argument names are deterministic."""

from website_client.login_capture import _public_definition


def definition(paths):
    return {
        "openapi": "3.1.0",
        "info": {"title": "Observed", "version": "1"},
        "paths": {
            path: {
                "get": {
                    "operationId": "upstreamName",
                    "parameters": [
                        {
                            "in": "query",
                            "name": "record_id",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Observed",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"title": {"type": "string"}},
                                    },
                                }
                            },
                        }
                    },
                }
            }
            for path in paths
        },
    }


def test_names_preserve_route_and_query_string_wire_contract():
    spec = _public_definition(definition(["/records"]))
    operation = spec["paths"]["/records"]["get"]
    assert operation["operationId"] == "get_records"
    assert operation["parameters"] == [
        {
            "in": "query",
            "name": "record_id",
            "required": False,
            "schema": {"type": "string"},
        }
    ]


def test_colliding_route_names_are_unique_and_independent_of_input_order():
    first = _public_definition(definition(["/some-records", "/some_records"]))
    second = _public_definition(definition(["/some_records", "/some-records"]))
    assert first == second
    assert len({item["get"]["operationId"] for item in first["paths"].values()}) == 2


def test_observed_json_and_selected_html_link_survive_the_same_generation(
    monkeypatch, tmp_path
):
    import json

    from website_client.login_generation import generate_login_client
    from website_client.page_link import PageLink

    generated = []

    def generate(output, name, document, **kwargs):
        generated.append(json.loads(document))
        return {"operations": []}

    monkeypatch.setattr(
        "website_client.login_generation.generate_openapi_client", generate
    )
    generate_login_client(
        "https://example.test",
        tmp_path / "client",
        definition=_public_definition(definition(["/records"])),
        link=PageLink("/help"),
    )
    [specification] = generated
    assert specification["paths"]["/records"]["get"]["operationId"] == "get_records"
    assert specification["paths"]["/help"]["get"]["operationId"] == "read_linked_page"


def test_identifier_link_is_skipped_and_next_ordinary_link_is_retained():
    from website_client.page_link import PageLink, first_page_link

    website = "https://example.test"
    dashboard = website + "/app"
    identifier_link = website + "/records/0123456789abcdef01234567"
    assert first_page_link(website, dashboard, [identifier_link]) is None
    assert first_page_link(
        website, dashboard, [identifier_link, website + "/help"]
    ) == PageLink("/help")
