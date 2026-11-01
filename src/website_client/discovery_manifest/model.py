"""Construct manifest entries and canonical content identities."""

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
    """Legacy JSON factory; internal code constructs typed records directly."""
    from .records import Artifact, Kind, Producer, Source

    try:
        kind, source = Kind(kind), Source(source)
    except (ValueError, TypeError):
        raise BrowserError("Unknown artifact kind or source.") from None
    return Artifact.create(
        name, kind, content, source, locations=tuple(locations),
        derived_from=tuple(derived_from),
        producer=Producer.from_dict(producer) if producer is not None else None,
    ).to_dict()


def service(
    protocol, endpoint, schema=None, operations=(), confidence="external", identifier=None
):
    """Legacy JSON factory for a validated service record."""
    from .records import Confidence, Protocol, Service

    try:
        protocol, confidence = Protocol(protocol), Confidence(confidence)
    except (ValueError, TypeError):
        raise BrowserError("Unknown service protocol or confidence.") from None
    return Service.create(
        protocol, endpoint, schema, tuple(operations), confidence, identifier,
    ).to_dict()


def new_bundle(artifacts=(), services=(), producer=None):
    """Legacy JSON factory for a complete validated manifest."""
    from .records import Artifact, Manifest, Producer, Service

    return Manifest.create(
        tuple(Artifact.from_dict(item) for item in artifacts),
        tuple(Service.from_dict(item) for item in services),
        Producer.from_dict(producer) if producer is not None else None,
    ).to_dict()
