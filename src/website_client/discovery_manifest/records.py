"""Immutable domain records; version 1 JSON is validated at both boundaries."""

from dataclasses import MISSING, dataclass, field, fields
from enum import StrEnum
from functools import cache
from types import UnionType
from typing import ClassVar, get_args, get_origin, get_type_hints

from ..browser import BrowserError
from .model import MAX_ARTIFACT_BYTES, endpoint_url, sha256
from .validation import validate_bundle, validate_record


class Protocol(StrEnum):
    OPENAPI = "openapi"
    GRAPHQL = "graphql"


class Kind(StrEnum):
    REPORT = "report"
    HAR = "har"
    OPENAPI = "openapi"
    GRAPHQL_SCHEMA = "graphql-schema"
    GRAPHQL_OPERATIONS = "graphql-operations"


class Source(StrEnum):
    CAPTURED = "captured"
    DOWNLOADED = "downloaded"
    INFERRED = "inferred"
    FRONTEND = "frontend"
    EXTERNAL = "external"
    NORMALIZED = "normalized"
    REPORT = "report"


class Confidence(StrEnum):
    PUBLISHED = "published"
    INFERRED = "inferred"
    EXTERNAL = "external"
    OBSERVED = "observed"
    DECLARED = "declared"


class Status(StrEnum):
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class Stage(StrEnum):
    DISCOVERY = "discovery"
    NORMALIZED = "normalized"


def json_field(name, *, default=MISSING, omit_none=False):
    return field(default=default, metadata={"json": name, "omit_none": omit_none})


@cache
def record_types(cls):
    return get_type_hints(cls)


def matches(value, expected):
    """Dataclass annotations alone do not enforce types or freeze nested lists."""
    if get_origin(expected) is UnionType:
        return any(matches(value, option) for option in get_args(expected))
    if get_origin(expected) is tuple:
        return type(value) is tuple and all(
            matches(item, get_args(expected)[0]) for item in value
        )
    return type(value) is expected


def encode(value):
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    return value


def decode(value, expected):
    if get_origin(expected) is UnionType:
        if value is None and type(None) in get_args(expected):
            return None
        expected = next(
            option for option in get_args(expected) if option is not type(None)
        )
    if get_origin(expected) is tuple:
        return tuple(decode(item, get_args(expected)[0]) for item in value)
    if isinstance(expected, type) and issubclass(expected, Record):
        return expected.from_dict(value)
    if isinstance(expected, type) and issubclass(expected, StrEnum):
        return expected(value)
    return value


class Record:
    __slots__ = ()
    SCHEMA: ClassVar[str]

    def __post_init__(self):
        for member in fields(self):
            if not matches(
                getattr(self, member.name), record_types(type(self))[member.name]
            ):
                raise BrowserError(f"Invalid {type(self).__name__}.{member.name} type.")
        self.validate_json(self.to_dict())

    @classmethod
    def validate_json(cls, value):
        if cls.SCHEMA == "":
            validate_bundle(value)
        else:
            validate_record(cls.SCHEMA, value)

    @classmethod
    def from_dict(cls, value):
        cls.validate_json(value)
        values = {}
        for member in fields(cls):
            name = member.metadata.get("json", member.name)
            if name in value:
                values[member.name] = decode(
                    value[name], record_types(cls)[member.name]
                )
        return cls(**values)

    def to_dict(self):
        result = {}
        for member in fields(self):
            value = getattr(self, member.name)
            if value is None and member.metadata.get("omit_none"):
                continue
            result[member.metadata.get("json", member.name)] = encode(value)
        return result


@dataclass(frozen=True, slots=True)
class Producer(Record):
    SCHEMA = "#/$defs/producer"
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class Provenance(Record):
    SCHEMA = "#/$defs/artifact/properties/provenance"
    source: Source
    locations: tuple[str, ...]
    derived_from: tuple[str, ...] = json_field("derivedFrom")
    producers: tuple[Producer, ...]


@dataclass(frozen=True, slots=True)
class Artifact(Record):
    SCHEMA = "#/$defs/artifact"
    id: str
    name: str
    kind: Kind
    content: str
    sha256: str
    provenance: Provenance

    def __post_init__(self):
        Record.__post_init__(self)
        if len(self.content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise BrowserError("Artifact exceeds the 32 MiB limit.")
        if sha256(self.content) != self.sha256:
            raise BrowserError("Artifact hash mismatch; no generation was performed.")


@dataclass(frozen=True, slots=True)
class Service(Record):
    SCHEMA = "#/$defs/service"
    id: str
    protocol: Protocol
    endpoint: str
    schema_artifact: str | None = json_field("schemaArtifact")
    operation_artifacts: tuple[str, ...] = json_field("operationArtifacts")
    confidence: Confidence
    status: Status | None = json_field("status", default=None, omit_none=True)
    issues: tuple[str, ...] | None = json_field("issues", default=None, omit_none=True)
    operation_names: tuple[str, ...] | None = json_field(
        "operationNames", default=None, omit_none=True
    )
    source_schema_artifact: str | None = json_field(
        "sourceSchemaArtifact", default=None, omit_none=True
    )
    compiled_operations_artifact: str | None = json_field(
        "compiledOperationsArtifact", default=None, omit_none=True
    )

    def __post_init__(self):
        Record.__post_init__(self)
        if endpoint_url(self.endpoint) != self.endpoint:
            raise BrowserError("Service endpoint must be canonical HTTPS.")


@dataclass(frozen=True, slots=True)
class SourceBundle(Record):
    SCHEMA = "#/properties/sourceBundles/items"
    sha256: str
    producer: Producer
    created_at: str = json_field("createdAt")


@dataclass(frozen=True, slots=True)
class Normalization(Record):
    SCHEMA = "#/properties/normalization"
    policy_version: int = json_field("policyVersion")
    unassigned_artifacts: tuple[str, ...] = json_field("unassignedArtifacts")
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Manifest(Record):
    SCHEMA = ""
    format: str
    version: int
    stage: Stage
    producer: Producer
    created_at: str = json_field("createdAt")
    artifacts: tuple[Artifact, ...]
    services: tuple[Service, ...]
    source_bundles: tuple[SourceBundle, ...] | None = json_field(
        "sourceBundles", default=None, omit_none=True
    )
    normalization: Normalization | None = json_field(
        "normalization", default=None, omit_none=True
    )
