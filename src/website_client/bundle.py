"""Portable producer contract, bounded transport and integrity validation."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker

from .browser import BrowserError, origin

FORMAT = "website-client-discovery"
VERSION = 1
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
PRODUCER = {"name": "website-client", "version": "0.1.0"}
SCHEMA_PATH = Path(__file__).with_name("schemas") / "discovery-bundle-v1.schema.json"


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(bundle):
    return sha256(encoded(bundle))


def endpoint_url(url):
    origin(url)
    parsed = urlsplit(url)
    if parsed.query:
        raise BrowserError("Service endpoints cannot include query values.")
    authority = parsed.hostname.lower()
    if ":" in authority:
        authority = "[" + authority + "]"
    if parsed.port and parsed.port != 443:
        authority += ":" + str(parsed.port)
    return urlunsplit(("https", authority, parsed.path or "/", "", ""))


def observed_endpoint(url):
    from .discovery import route

    parsed = urlsplit(url)
    base, path, _parameters = route(urlunsplit(parsed._replace(query="", fragment="")))
    return endpoint_url(base + path)


def artifact(
    name, kind, content, source, *, locations=(), derived_from=(), producer=None
):
    return {
        "id": "artifact-" + sha256(kind + "\0" + name + "\0" + content),
        "name": name,
        "kind": kind,
        "content": content,
        "sha256": sha256(content),
        "provenance": {
            "source": source,
            "locations": sorted(set(locations)),
            "derivedFrom": sorted(set(derived_from)),
            "producers": [producer or PRODUCER],
        },
    }


def service(
    protocol,
    endpoint,
    schema=None,
    operations=(),
    confidence="external",
    identifier=None,
):
    endpoint = endpoint_url(endpoint)
    return {
        "id": identifier or "service-" + sha256(protocol + "\0" + endpoint)[:16],
        "protocol": protocol,
        "endpoint": endpoint,
        "schemaArtifact": schema,
        "operationArtifacts": list(dict.fromkeys(operations)),
        "confidence": confidence,
    }


def new_bundle(artifacts=(), services=(), producer=None):
    return {
        "format": FORMAT,
        "version": VERSION,
        "stage": "discovery",
        "producer": producer or PRODUCER,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "artifacts": list(artifacts),
        "services": list(services),
    }


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


def read_text(source, limit=MAX_BUNDLE_BYTES):
    try:
        if str(source) == "-":
            if sys.stdin.isatty():
                raise BrowserError("Pipe input to stdin or provide a file path.")
            data = sys.stdin.buffer.read(limit + 1)
        else:
            path = Path(source).expanduser()
            if path.is_dir():
                path /= "manifest.json"
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise BrowserError("Input exceeds its size limit.")
        return data.decode("utf-8")
    except BrowserError:
        raise
    except Exception:
        raise BrowserError(
            "Input could not be read. Supply a UTF-8 file, a manifest directory, or '-'."
        ) from None


def read_bundle(source):
    try:
        value = json.loads(
            read_text(source),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except BrowserError:
        raise
    except Exception:
        raise BrowserError("Supply a valid JSON discovery bundle.") from None
    return validate_bundle(value)


def reject_constant(_value):
    raise ValueError("Non-standard JSON constant")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def write_bundle(value, output="-"):
    validate_bundle(value)
    text = encoded(value)
    if str(output) == "-":
        sys.stdout.write(text)
        return
    try:
        descriptor = os.open(
            Path(output).expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
    except Exception:
        raise BrowserError(
            "Bundle output must be a new file in an existing directory."
        ) from None


def merge_bundles(bundles):
    if (
        not bundles
        or len(bundles) > 20
        or sum(len(encoded(value).encode()) for value in bundles) > MAX_BUNDLE_BYTES
    ):
        raise BrowserError("Merge at most 20 bundles totaling at most 64 MiB.")
    if len(bundles) == 1:
        return copy.deepcopy(validate_bundle(bundles[0]))
    for bundle in bundles:
        validate_bundle(bundle)
    merged = new_bundle(
        producer={"name": "website-client-merge", "version": PRODUCER["version"]}
    )
    merged["createdAt"] = max(
        datetime.fromisoformat(item["createdAt"]).astimezone(timezone.utc)
        for item in bundles
    ).isoformat()
    merged["sourceBundles"] = []
    artifacts, services = {}, {}
    for bundle in bundles:
        for source in bundle.get("sourceBundles", []):
            if source not in merged["sourceBundles"]:
                merged["sourceBundles"].append(copy.deepcopy(source))
        merged["sourceBundles"].append(
            {
                "sha256": digest(bundle),
                "producer": bundle["producer"],
                "createdAt": bundle["createdAt"],
            }
        )
        for item in bundle["artifacts"]:
            prior = artifacts.get(item["id"])
            if prior is None:
                artifacts[item["id"]] = copy.deepcopy(item)
            elif (prior["kind"], prior["content"]) != (item["kind"], item["content"]):
                raise BrowserError("Conflicting artifact IDs across producers.")
            else:
                for key in ("locations", "derivedFrom", "producers"):
                    for entry in item["provenance"][key]:
                        if entry not in prior["provenance"][key]:
                            prior["provenance"][key].append(entry)
        for target in bundle["services"]:
            # Re-normalization will reconstruct derived contracts from originals.
            target = copy.deepcopy(target)
            target["schemaArtifact"] = target.get(
                "sourceSchemaArtifact", target["schemaArtifact"]
            )
            prior = services.get(target["id"])
            if prior is None:
                services[target["id"]] = target
                continue
            if (prior["protocol"], prior["endpoint"]) != (
                target["protocol"],
                target["endpoint"],
            ):
                raise BrowserError("Conflicting service IDs across producers.")
            if (
                prior["schemaArtifact"]
                and target["schemaArtifact"]
                and prior["schemaArtifact"] != target["schemaArtifact"]
                and artifacts[prior["schemaArtifact"]]["sha256"]
                != artifacts[target["schemaArtifact"]]["sha256"]
            ):
                # A published definition supersedes samples for the same API.
                if (
                    prior["confidence"] == "inferred"
                    and target["confidence"] != "inferred"
                ):
                    prior["schemaArtifact"], prior["confidence"] = (
                        target["schemaArtifact"],
                        target["confidence"],
                    )
                elif target["confidence"] != "inferred":
                    raise BrowserError(
                        "Conflicting schemas for one service; select or rename the producer's service."
                    )
            elif target["schemaArtifact"] and not prior["schemaArtifact"]:
                prior["schemaArtifact"], prior["confidence"] = (
                    target["schemaArtifact"],
                    target["confidence"],
                )
            prior["operationArtifacts"] = list(
                dict.fromkeys(
                    prior["operationArtifacts"] + target["operationArtifacts"]
                )
            )
    merged["artifacts"], merged["services"] = (
        list(artifacts.values()),
        list(services.values()),
    )
    return validate_bundle(merged)
