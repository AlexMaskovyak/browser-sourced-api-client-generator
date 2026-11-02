"""File workflows must come from evidence and preserve bytes, order and uncertainty."""

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from website_client.upload_workflows import UploadWorkflow, read_workflows, write_workflows
from website_client.upload_inference import discover_workflows
from website_client.upload_discovery import source_upload_signals
from website_client.upload_runtime import FileWriteError, prepare_file, submit_with_file
from website_client.operation_contracts import compile_operations


WORKFLOW = UploadWorkflow("CreateRecord", "https://app.example.test/graphql",
                          "https://app.example.test/rails/active_storage/direct_uploads",
                          "input", "attachmentId", ("a" * 64,))


def test_workflow_roundtrip_and_untrusted_fields():
    assert read_workflows(write_workflows((WORKFLOW,))) == (WORKFLOW,)
    for changed in ({**WORKFLOW.to_dict(), "metadataEndpoint": "https://user:secret@example.test/upload"},
                    {**WORKFLOW.to_dict(), "maxBytes": True},
                    {**WORKFLOW.to_dict(), "unknown": "value"}):
        with pytest.raises(ValueError):
            UploadWorkflow.from_dict(changed)


def test_source_inference_is_protocol_based_and_rejects_ambiguous_fields():
    signals = source_upload_signals('const url="/rails/active_storage/direct_uploads"; signed_id; direct_upload; checksum; byte_size;', "https://app.example.test/")
    modules = [{"sourceSha256": "a" * 64, "uploadSignals": signals, "operations": []},
               {"sourceSha256": "b" * 64, "uploadSignals": {"attachmentFields": ["attachmentId"]},
                "operations": [{"name": "CreateRecord", "kind": "mutation", "variables": {"input": "RecordInput!"}, "documentExported": True}]}]
    found = discover_workflows(modules, WORKFLOW.apiEndpoint)
    assert len(found) == 1 and found[0].attachmentField == "attachmentId"
    modules[-1]["uploadSignals"]["attachmentFields"].append("receiptId")
    assert not discover_workflows(modules, WORKFLOW.apiEndpoint)


def setup_flow(tmp_path, *, failure=None):
    path = tmp_path / "receipt.pdf"
    path.write_bytes(b"%PDF-1.7\nlocal test bytes\x00\xff")
    file = prepare_file(path, WORKFLOW.maxBytes)
    calls, mutations = [], []
    def metadata(request):
        calls.append("metadata")
        assert request.url == WORKFLOW.metadataEndpoint
        blob = json.loads(request.content)["blob"]
        assert blob["checksum"] == file.checksum and blob["byte_size"] == len(file.data)
        value = {**blob, "signed_id": "private-signed-id",
                 "direct_upload": {"url": "https://app.example.test/storage?signature=private",
                                   "headers": {"Content-Type": file.content_type, "Content-MD5": file.checksum}}}
        if failure == "metadata-mismatch": value["checksum"] = "wrong"
        if failure == "credentials": value["direct_upload"]["headers"]["Authorization"] = "secret"
        if failure == "metadata-network": raise httpx.ReadTimeout("secret body")
        return httpx.Response(200, json=value)
    def storage(request):
        calls.append("storage")
        assert request.content == file.data
        assert request.headers["content-md5"] == file.checksum
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if failure == "storage": return httpx.Response(503)
        if failure == "redirect": return httpx.Response(307, headers={"location": "https://outside.invalid"})
        return httpx.Response(200)
    operation = compile_operations(("mutation CreateRecord($input: RecordInput!) { create(input:$input) { id } }",))[0]
    def create(**arguments):
        calls.append("mutation")
        mutations.append(arguments)
        if failure == "mutation": raise RuntimeError("private body")
        if failure == "rejection": return {"payload": {"errors": [{"message": "private"}]}}
        return {"create": {"id": "record-1", "receipt": {"id": "blob-1"}}}
    client = SimpleNamespace(url=WORKFLOW.apiEndpoint, allow_writes=True,
                             _operations={"CreateRecord": SimpleNamespace(operation=operation)}, create_record=create)
    return path, file, calls, mutations, client, httpx.Client(transport=httpx.MockTransport(metadata)), httpx.Client(transport=httpx.MockTransport(storage))


def test_full_flow_uses_exact_bytes_and_never_changes_original_input(tmp_path):
    path, file, calls, mutations, client, app, storage = setup_flow(tmp_path)
    original = {"input": {"amount": "12.34"}}
    with app, storage:
        result = submit_with_file(client, "CreateRecord", original, WORKFLOW, path, metadata_client=app, storage_client=storage)
    assert calls == ["metadata", "storage", "mutation"]
    assert mutations == [{"input": {"amount": "12.34", "attachmentId": "private-signed-id"}}]
    assert original == {"input": {"amount": "12.34"}}
    assert result["create"]["receipt"]["id"] == "blob-1"
    assert file.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("failure,stage,expected", [
    ("metadata-network", "metadata", ["metadata"]),
    ("metadata-mismatch", "metadata", ["metadata"]),
    ("credentials", "metadata", ["metadata"]),
    ("storage", "storage", ["metadata", "storage"]),
    ("redirect", "storage", ["metadata", "storage"]),
    ("mutation", "mutation", ["metadata", "storage", "mutation"]),
    ("rejection", "mutation", ["metadata", "storage", "mutation"]),
])
def test_failed_stage_stops_without_retry_or_sensitive_errors(tmp_path, failure, stage, expected):
    path, file, calls, mutations, client, app, storage = setup_flow(tmp_path, failure=failure)
    with app, storage, pytest.raises(FileWriteError) as captured:
        submit_with_file(client, "CreateRecord", {"input": {}}, WORKFLOW, path, metadata_client=app, storage_client=storage)
    assert captured.value.stage == stage
    assert captured.value.uploaded == (stage == "mutation")
    assert calls == expected
    assert "private" not in str(captured.value) and "signature" not in str(captured.value)


def test_no_writes_before_permission_and_input_checks(tmp_path):
    path, file, calls, mutations, client, app, storage = setup_flow(tmp_path)
    with app, storage:
        client.allow_writes = False
        with pytest.raises(ValueError):
            submit_with_file(client, "CreateRecord", {"input": {}}, WORKFLOW, path, metadata_client=app, storage_client=storage)
        client.allow_writes = True
        with pytest.raises(ValueError):
            submit_with_file(client, "CreateRecord", {"input": {"attachmentId": "existing"}}, WORKFLOW, path, metadata_client=app, storage_client=storage)
    assert not calls

