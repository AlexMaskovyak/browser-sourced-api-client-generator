"""Validate the public versioned format, hashes and evidence references."""

import json

from jsonschema import Draft202012Validator, FormatChecker

from ..browser import BrowserError
from .model import MAX_ARTIFACT_BYTES, MAX_BUNDLE_BYTES, SCHEMA_PATH, VERSION, encoded, endpoint_url, sha256


def validate_bundle(value):
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != VERSION
    ):
        raise BrowserError("Unsupported discovery bundle version; expected version 1.")
    schema = json.loads(SCHEMA_PATH.read_text())
    if next(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        None,
    ):
        raise BrowserError(
            "Invalid discovery bundle. Check discovery-bundle-v1.schema.json."
        )
    if len(encoded(value).encode()) > MAX_BUNDLE_BYTES:
        raise BrowserError("Discovery bundle exceeds the 64 MiB limit.")
    artifacts = {}
    for item in value["artifacts"]:
        if (
            item["id"] in artifacts
            or len(item["content"].encode()) > MAX_ARTIFACT_BYTES
        ):
            raise BrowserError("Duplicate artifact ID or artifact larger than 32 MiB.")
        if sha256(item["content"]) != item["sha256"]:
            raise BrowserError("Artifact hash mismatch; no generation was performed.")
        artifacts[item["id"]] = item
    ids = set()
    for target in value["services"]:
        if endpoint_url(target["endpoint"]) != target["endpoint"]:
            raise BrowserError(
                "Service endpoints must be canonical HTTPS URLs with '/' for an empty path and no default :443 port."
            )
        if target["id"] in ids:
            raise BrowserError("Duplicate service ID.")
        ids.add(target["id"])
        references = target["operationArtifacts"] + [
            target[key]
            for key in (
                "schemaArtifact",
                "sourceSchemaArtifact",
                "compiledOperationsArtifact",
            )
            if target.get(key)
        ]
        if any(reference not in artifacts for reference in references):
            raise BrowserError("A service references a missing artifact.")
    for item in artifacts.values():
        if any(
            reference not in artifacts
            for reference in item["provenance"]["derivedFrom"]
        ):
            raise BrowserError("An artifact references missing source evidence.")
    complete, active = set(), set()

    def visit(identifier):
        if identifier in active:
            raise BrowserError("Artifact provenance contains a cycle.")
        if identifier in complete:
            return
        active.add(identifier)
        for parent in artifacts[identifier]["provenance"]["derivedFrom"]:
            visit(parent)
        active.remove(identifier)
        complete.add(identifier)

    for identifier in artifacts:
        visit(identifier)
    return value

