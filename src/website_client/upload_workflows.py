"""Versioned file-upload contracts shared by discovery and standalone clients."""

from dataclasses import asdict, dataclass
import json
import re
from urllib.parse import urlsplit

FORMAT = "website-client-upload-workflows"
VERSION = 1
PROTOCOL = "rails-active-storage-v1"
IDENTIFIER = re.compile(r"^[_A-Za-z][_0-9A-Za-z]*$")
MAX_FILE_BYTES = 15_000_000


def endpoint(value):
    """Accept a public HTTPS contract URL without credentials or temporary values."""
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Invalid upload endpoint.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Upload endpoints require HTTPS without credentials or query values.")
    return value


@dataclass(frozen=True, slots=True)
class UploadWorkflow:
    """A declared metadata/bytes/mutation sequence, never a saved upload session.

    Source inspection infers the attachment property from its declaring module.
    The contract does not assert live acceptance or authorize its execution.
    """
    operation: str
    apiEndpoint: str
    metadataEndpoint: str
    inputVariable: str
    attachmentField: str
    evidence: tuple[str, ...]
    protocol: str = PROTOCOL
    maxBytes: int = MAX_FILE_BYTES

    def __post_init__(self):
        """Validate endpoints, identifiers, protocol bounds and source hashes."""
        for name in (self.operation, self.inputVariable, self.attachmentField):
            if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
                raise ValueError("Invalid upload workflow identifier.")
        endpoint(self.apiEndpoint)
        endpoint(self.metadataEndpoint)
        if self.protocol != PROTOCOL or type(self.maxBytes) is not int or not 1 <= self.maxBytes <= 32 * 1024 * 1024:
            raise ValueError("Unsupported upload protocol or file limit.")
        if not isinstance(self.evidence, tuple) or not self.evidence or len(self.evidence) > 20:
            raise ValueError("Upload workflows require bounded source evidence.")
        if any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in self.evidence):
            raise ValueError("Upload source evidence must use content hashes.")

    @classmethod
    def from_dict(cls, value):
        """Reject unknown fields and malformed upstream producer contracts."""
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid upload workflow fields.")
        if not isinstance(value["evidence"], list):
            raise ValueError("Upload evidence must be a list.")
        return cls(**{**value, "evidence": tuple(value["evidence"])})

    def to_dict(self):
        """Return the portable contract without request values or credentials."""
        return {**asdict(self), "evidence": list(self.evidence)}


def read_workflows(text):
    """Read one bounded versioned upload-contract document."""
    if len(text.encode()) > 1024 * 1024:
        raise ValueError("Upload workflow document exceeds 1 MiB.")
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"format", "version", "workflows"} or value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != VERSION:
        raise ValueError("Use a version 1 upload workflow document.")
    if not isinstance(value["workflows"], list) or len(value["workflows"]) > 100:
        raise ValueError("Supply at most 100 upload workflows.")
    workflows = tuple(UploadWorkflow.from_dict(item) for item in value["workflows"])
    if len({(item.apiEndpoint, item.operation) for item in workflows}) != len(workflows):
        raise ValueError("Duplicate upload workflows are not permitted.")
    return workflows


def write_workflows(workflows):
    """Serialize validated contracts for manifests and reproducible builds."""
    return json.dumps({"format": FORMAT, "version": VERSION,
                       "workflows": [item.to_dict() for item in workflows]}, sort_keys=True, indent=2) + "\n"
