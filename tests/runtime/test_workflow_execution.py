"""Native Arazzo scheduling and serialization against independent local contracts."""

import hashlib
from io import BytesIO
import json
import threading

import pytest

from website_client.runtime_profile import ProfileStore
from website_client.workflow_runtime import execute_document, quiet_runner
from website_client.workflow_schema import validate_value

from support.workflow import PAYLOAD, DIGEST, documents
from support.workflow import service as service


def test_native_guarded_sequence_exact_bytes_rotation_and_outputs(service):
    url, server = service
    document, sources = documents(url)
    result = execute_document(
        document, sources, "upload", {"file": PAYLOAD}, {url: "neutral"}
    )

    assert result["status"] == "success", result
    assert result["outputs"] == {"complete": True}
    assert result["requests"] == result["responses"] == 3
    assert [call[1] for call in server.calls] == ["/prepare", "/binary", "/finish"]
    assert [call[2] for call in server.calls] == [
        "sid=INITIAL",
        "sid=ROTATED",
        "sid=ROTATED",
    ]
    assert server.commits == 1
    assert hashlib.sha256(PAYLOAD).hexdigest() == DIGEST
    assert len(PAYLOAD) == 274
    with ProfileStore("neutral", url).locked() as store:
        assert store.load()["storage_state"]["cookies"][0]["value"] == "ROTATED"


@pytest.mark.parametrize(
    "mode,expected_reason,expected_request_count",
    [
        ("missing", "missing_required_output", 1),
        ("null", "null_required_output", 1),
        ("malformed", "malformed_response_json", 1),
        ("integer", "criterion_requires_boolean", 3),
        ("false", "assertion_failed", 3),
        ("disconnect", "transport_failure", 1),
    ],
)
def test_failures_never_dispatch_a_dependent_step_or_retry(
    service, mode, expected_reason, expected_request_count
):
    url, server = service
    server.mode = mode
    document, sources = documents(url)
    result = execute_document(
        document, sources, "upload", {"file": PAYLOAD}, {url: "neutral"}
    )

    assert result["status"] == "failure", result
    assert result["reason"] == expected_reason
    assert len(server.calls) == expected_request_count
    assert server.commits == 1
    if mode == "disconnect":
        assert result["effects"] == "unknown"
        assert result["requests"] == 1
        assert result["responses"] == 0
    assert "SYNTHETIC_TOKEN" not in json.dumps(result)


def test_unknown_file_objects_and_missing_inputs_rejected_before_dispatch(service):
    url, server = service
    document, sources = documents(url)
    for inputs, reason in [
        ({"file": BytesIO(PAYLOAD)}, "unsupported_input_object"),
        ({}, "missing_input"),
    ]:
        result = execute_document(document, sources, "upload", inputs, {url: "neutral"})
        assert result["reason"] == reason

    assert not server.calls


def test_versioned_native_json_null_omitted_nested_and_schema_rejection(service):
    import requests
    import importlib.metadata
    from arazzo_runner import ArazzoRunner

    assert importlib.metadata.version("arazzo-runner") == "0.9.6+websiteclient.1"
    url, server = service
    server.mode = "whole-null"
    source = {
        "openapi": "3.0.3",
        "info": {"title": "Nullable", "version": "1"},
        "servers": [{"url": url}],
        "paths": {
            "/nullable": {
                "post": {
                    "operationId": "nullable",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "string", "nullable": True}
                            }
                        },
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    document = {
        "arazzo": "1.0.0",
        "info": {"title": "Nullable", "version": "1"},
        "sourceDescriptions": [
            {"name": "api", "type": "openapi", "url": "openapi.json"}
        ],
        "workflows": [
            {
                "workflowId": "nullable",
                "steps": [
                    {
                        "stepId": "send",
                        "operationId": "nullable",
                        "requestBody": {
                            "contentType": "application/json",
                            "payload": None,
                        },
                        "successCriteria": [{"condition": "$statusCode == 200"}],
                    }
                ],
            }
        ],
    }
    validate_value(None, {"type": "string", "nullable": True}, dialect="3.0.3")
    with requests.Session() as session, quiet_runner():
        session.trust_env = False
        runner = ArazzoRunner(
            arazzo_doc=document,
            source_descriptions={"api": source},
            http_client=session,
        )
        execution = runner.start_workflow("nullable", {})
        event = runner.execute_next_step(execution)
    assert server.calls[-1][3] == b"null"
    assert event.get("success") is True
    server.calls.clear()
    result = execute_document(document, {"api": source}, "nullable")
    assert result["status"] == "success", result
    assert server.calls[-1][3] == b"null"
    source["paths"]["/nullable"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] = {"type": "string"}
    before = len(server.calls)
    result = execute_document(document, {"api": source}, "nullable")
    assert result["reason"] == "invalid_request"
    assert len(server.calls) == before
    source["paths"]["/nullable"]["post"]["requestBody"]["required"] = False
    document["workflows"][0]["steps"][0]["requestBody"] = {
        "contentType": "application/json"
    }
    server.mode = "omitted"
    result = execute_document(document, {"api": source}, "nullable")
    assert result["status"] == "success", result
    assert server.calls[-1][3] == b""
    source["paths"]["/nullable"]["post"]["requestBody"]["required"] = True
    server.mode = "nested-null"
    source["paths"]["/nullable"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string", "nullable": True}},
    }
    document["workflows"][0]["steps"][0]["requestBody"]["payload"] = {"value": None}
    result = execute_document(document, {"api": source}, "nullable")
    assert result["status"] == "success", result
    assert server.calls[-1][3] == b'{"value": null}'


def test_native_flat_multipart_retains_filename_media_type_and_bytes(service):
    url, server = service
    document, sources = documents(url)
    sources["api"]["paths"]["/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["encoding"] = {"file": {"contentType": "application/x-neutral"}}
    document["workflows"] = [
        {
            "workflowId": "multipart",
            "steps": [
                {
                    "stepId": "send",
                    "operationId": "upload_file",
                    "requestBody": {
                        "contentType": "multipart/form-data",
                        "payload": {
                            "file": {
                                "content": "$inputs.file",
                                "file_name": "payload.bin",
                                "contentType": "application/x-neutral",
                            },
                            "note": "neutral",
                        },
                    },
                    "successCriteria": [{"condition": "$statusCode == 200"}],
                }
            ],
        }
    ]
    result = execute_document(
        document, sources, "multipart", {"file": PAYLOAD}, {url: "neutral"}
    )

    assert result["status"] == "success", result
    assert len(server.calls) == 1


@pytest.mark.parametrize("kind", ["literal", "input", "step-output"])
def test_multipart_wrong_scalar_is_rejected_before_native_string_coercion(
    service, kind
):
    url, server = service
    document, sources = documents(url)
    multipart = {
        "stepId": "send",
        "operationId": "upload_file",
        "requestBody": {
            "contentType": "multipart/form-data",
            "payload": {
                "file": {
                    "content": "$inputs.file",
                    "file_name": "payload.bin",
                    "contentType": "application/x-neutral",
                },
                "note": {"nested": "object"},
            },
        },
    }
    sources["api"]["paths"]["/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["encoding"] = {"file": {"contentType": "application/x-neutral"}}
    inputs = {"file": PAYLOAD}
    if kind == "input":
        multipart["requestBody"]["payload"]["note"] = "$inputs.note"
        inputs["note"] = {"nested": "object"}
    elif kind == "step-output":
        server.mode = "object"
        multipart["requestBody"]["payload"]["note"] = "$steps.prepare.ticket"
    steps = (
        [document["workflows"][0]["steps"][0]] if kind == "step-output" else []
    ) + [multipart]
    document["workflows"] = [{"workflowId": "invalid", "steps": steps}]
    result = execute_document(document, sources, "invalid", inputs, {url: "neutral"})

    assert result["reason"] == "invalid_request", result
    assert [call[1] for call in server.calls] == (
        ["/prepare"] if kind == "step-output" else []
    )
    assert not any(call[1] == "/upload" for call in server.calls)


def test_quiet_runner_overlapping_threads_do_not_leak_and_restore_state():
    import io
    import logging
    import sys

    first_entered, second_attempted, second_entered, release_first = (
        threading.Event() for _ in range(4)
    )
    logger = logging.getLogger("arazzo-runner")
    original_handlers, original_propagate = logger.handlers, logger.propagate
    original_stdout, original_stderr = sys.stdout, sys.stderr
    captured = io.StringIO()
    observer = logging.StreamHandler(captured)
    logger.handlers, logger.propagate = [observer], False
    try:

        def first():
            with quiet_runner():
                first_entered.set()
                assert release_first.wait(5)
                logger.error("CREDENTIAL_CANARY_FIRST")

        def second():
            assert first_entered.wait(5)
            second_attempted.set()
            with quiet_runner():
                second_entered.set()
                logger.error("CREDENTIAL_CANARY_SECOND")

        a, b = threading.Thread(target=first), threading.Thread(target=second)
        a.start()
        b.start()
        assert second_attempted.wait(5)
        assert not second_entered.wait(0.1)
        release_first.set()
        a.join(5)
        b.join(5)
        assert not a.is_alive()
        assert not b.is_alive()
        assert second_entered.is_set()
        assert "CREDENTIAL_CANARY" not in captured.getvalue()
        assert logger.handlers == [observer]
        assert logger.propagate is False
        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr
    finally:
        release_first.set()
        logger.handlers, logger.propagate = original_handlers, original_propagate


@pytest.mark.parametrize(
    "identifier",
    [
        "part/other",
        "part?command=create",
        "part#fragment",
        "%2F",
        "..",
        "has space",
        "good_1",
    ],
)
def test_path_values_needing_unproven_encoding_are_rejected_before_dispatch(
    service, identifier
):
    url, server = service
    document, sources = documents(url)
    sources["api"]["paths"]["/resource/{id}"] = {
        "get": {
            "operationId": "resource",
            "parameters": [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {"200": {"description": "ok"}},
        }
    }
    document["workflows"] = [
        {
            "workflowId": "resource",
            "steps": [
                {
                    "stepId": "read",
                    "operationId": "resource",
                    "parameters": [{"name": "id", "in": "path", "value": "$inputs.id"}],
                    "outputs": {"value": "$response.body#/value"},
                }
            ],
            "outputs": {"value": "$steps.read.value"},
        }
    ]
    result = execute_document(
        document, sources, "resource", {"id": identifier}, {url: "neutral"}
    )
    if identifier == "good_1":
        assert result["status"] == "success", result
        assert result["outputs"] == {"value": "good_1"}
        assert len(server.calls) == 1
    else:
        assert result["reason"] == "unsupported_path_value", result
        assert not server.calls
