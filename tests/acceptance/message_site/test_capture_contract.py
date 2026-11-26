"""Capture stage: infer the naturally observed request and JSON response."""

import json

from .support import resolve_local_schema


def test_capture_infers_the_observed_message_argument_and_response(generated_application):
    """Milestone: the generator discovers the operation without endpoint hints."""
    specification = json.loads((generated_application.project / "openapi.json").read_text())
    message_path = specification["paths"].get("/message", {})
    assert "get" in message_path, (
        "Browser and backend verified GET /message, but generated OpenAPI has no "
        "GET /message operation. Login success is not operation discovery."
    )
    operation = message_path["get"]
    parameters = message_path.get("parameters", []) + operation.get("parameters", [])
    query_parameters = [resolve_local_schema(specification, parameter) for parameter in parameters]
    message_id_parameter = next(
        (parameter for parameter in query_parameters
         if parameter.get("in") == "query" and parameter.get("name") == "message_id"),
        None,
    )
    assert message_id_parameter is not None, "Observed message_id argument was lost."
    assert resolve_local_schema(specification, message_id_parameter["schema"])["type"] == "string"

    response = resolve_local_schema(specification, operation["responses"]["200"])
    response_schema = resolve_local_schema(
        specification, response["content"]["application/json"]["schema"],
    )
    assert response_schema["type"] == "object"
    for field in ("id", "subject"):
        field_schema = resolve_local_schema(specification, response_schema["properties"][field])
        assert field_schema["type"] == "string", f"Observed {field} response field was lost."
