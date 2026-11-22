"""Execute a discovered Active Storage upload followed by one generated mutation."""

import base64
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import ipaddress
import mimetypes
from pathlib import Path
import socket
from urllib.parse import urlsplit

import httpx

from .upload_workflows import UploadWorkflow


class FileWriteError(RuntimeError):
    """A bounded stage report; credentials, signed URLs and server bodies stay private."""
    def __init__(self, stage, outcome, *, uploaded=False):
        self.stage, self.outcome, self.uploaded = stage, outcome, uploaded
        super().__init__(f"File-backed write failed at {stage}; outcome={outcome}; file_uploaded={uploaded}. No automatic retry. Check the service before retrying.")


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """One immutable snapshot used for both checksum and exact-byte transfer."""
    name: str
    content_type: str
    data: bytes = field(repr=False)
    checksum: str
    sha256: str


def prepare_file(path, limit):
    """Validate and snapshot a bounded local file before login or network writes."""
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError("Receipt file must be nonempty and within the discovered size limit.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit or len(path.name) > 255 or any(ord(c) < 32 or c in '\\/\x7f' for c in path.name):
        raise ValueError("Receipt file or filename is invalid.")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    signatures = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
    if content_type in signatures and not data.startswith(signatures[content_type]):
        raise ValueError("Receipt contents do not match the file type.")
    return PreparedFile(path.name, content_type, data,
                        base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode(),
                        hashlib.sha256(data).hexdigest())


def validate_file_arguments(client, operation, arguments, workflow):
    """Reject read permission, wrong API and preexisting attachment values before upload."""
    from .operation_contracts import validate_variables
    if not client.allow_writes:
        raise ValueError("This write requires allow_writes=True on the Python client.")
    if client.url != workflow.apiEndpoint or operation != workflow.operation:
        raise ValueError("File upload must use its discovered API and operation.")
    validate_variables(client._operations[operation].operation, arguments)
    item = arguments.get(workflow.inputVariable)
    if not isinstance(item, dict) or workflow.attachmentField in item:
        raise ValueError("Receipt input must be an object without an existing attachment field.")


def storage_address(url, metadata_endpoint):
    """Trust the authenticated metadata response only for a credential-free public URL.

    Same-origin storage is supported. Other destinations must resolve publicly;
    private, loopback and link-local storage cannot be selected by remote data.
    Signed query values remain in memory and are never included in errors.
    """
    parsed, source = urlsplit(url), urlsplit(metadata_endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Invalid storage destination.")
    if (parsed.hostname, parsed.port or 443) != (source.hostname, source.port or 443):
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("Invalid storage destination.")
    return url


def submit_with_file(client, operation, arguments, workflow, receipt, *, metadata_client=None, storage_client=None):
    """Upload exact bytes, inject the returned signed ID and call the mutation once.

    Requires allow_writes=True. Inject an application-authenticated metadata_client
    when its origin differs from the API. Storage uses a separate credential-free
    connection. No phase retries, follows redirects or saves a signed URL/ID.
    A failure can leave an unattached upload; the stage error preserves uncertainty.
    """
    if isinstance(workflow, dict):
        workflow = UploadWorkflow.from_dict(workflow)
    validate_file_arguments(client, operation, arguments, workflow)
    receipt = receipt if isinstance(receipt, PreparedFile) else prepare_file(receipt, workflow.maxBytes)
    if len(receipt.data) > workflow.maxBytes:
        raise ValueError("Receipt exceeds the discovered size limit.")
    if metadata_client is None:
        if urlsplit(client.url).netloc != urlsplit(workflow.metadataEndpoint).netloc:
            raise ValueError("An authenticated metadata_client is required for the separate application origin.")
        metadata_client = client.http_client
    stage, uploaded = "metadata", False
    blob = {"filename": receipt.name, "content_type": receipt.content_type,
            "byte_size": len(receipt.data), "checksum": receipt.checksum}
    try:
        response = metadata_client.post(workflow.metadataEndpoint, json={"blob": blob},
                                        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
                                        follow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise FileWriteError(stage, "rejected" if 400 <= response.status_code < 500 else "unknown")
        if len(response.content) > 1024 * 1024:
            raise ValueError()
        metadata = response.json()
        if any(metadata.get(key) != value for key, value in blob.items()):
            raise ValueError()
        signed_id = metadata.get("signed_id")
        if not isinstance(signed_id, str) or not 0 < len(signed_id) <= 16384:
            raise ValueError()
        destination = storage_address(metadata["direct_upload"]["url"], workflow.metadataEndpoint)
        headers = metadata["direct_upload"]["headers"]
        if not isinstance(headers, dict) or len(headers) > 30:
            raise ValueError()
        for name, value in headers.items():
            if not isinstance(name, str) or name.lower() not in {"content-type", "content-md5", "content-disposition", "cache-control", "x-amz-server-side-encryption", "x-amz-acl", "x-goog-content-length-range"} or not isinstance(value, str) or '\r' in value or '\n' in value:
                raise ValueError()
        if any(value != receipt.checksum for name, value in headers.items() if name.lower() == "content-md5"):
            raise ValueError()
        stage = "storage"
        with ExitStack() as stack:
            storage = storage_client or stack.enter_context(httpx.Client(timeout=60, follow_redirects=False))
            if storage.auth is not None or storage.cookies or any(name.lower() in {"authorization", "cookie", "x-csrf-token", "proxy-authorization"} for name in storage.headers):
                raise ValueError()
            response = storage.put(destination, content=receipt.data, headers=headers, follow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise FileWriteError(stage, "unknown")
        uploaded = True
        stage = "mutation"
        supplied = deepcopy(arguments)
        supplied[workflow.inputVariable][workflow.attachmentField] = signed_id
        result = getattr(client, client._operations[operation].operation.method)(**supplied)
        # GraphQL top-level errors are handled by the generated operation runtime;
        # ordinary payload validation errors must also not look like success.
        if isinstance(result, dict) and any(isinstance(value, dict) and value.get("errors") for value in result.values()):
            # A generic payload error does not prove transaction rollback.
            # Treat it as uncertain even when the service reports validation errors.
            raise FileWriteError(stage, "unknown", uploaded=True)
        return result
    except FileWriteError:
        raise
    except Exception:
        raise FileWriteError(stage, "unknown", uploaded=uploaded) from None
