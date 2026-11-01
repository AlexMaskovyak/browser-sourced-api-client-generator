"""Construct manifest entries and canonical content identities."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..browser import BrowserError, origin

FORMAT = "website-client-discovery"
VERSION = 1
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
PRODUCER = {"name": "website-client", "version": "0.1.0"}
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "discovery-bundle-v1.schema.json"


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
    from ..discovery import route

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

