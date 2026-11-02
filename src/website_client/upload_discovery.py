"""Collect non-secret file-upload declarations from page-owned source code."""

import re
from urllib.parse import urljoin

from .browser import origin

UPLOAD_PATH = re.compile(r'''["'`]((?:https://[^"'`\s]+)?/rails/active_storage/direct_uploads)["'`]''')
PROPERTY = re.compile(r'\b([_A-Za-z][_0-9A-Za-z]{0,127})\s*:')
ATTACHMENT_NAME = re.compile(r'file|receipt|attachment|blob', re.I)
ID_SUFFIX = re.compile(r'(?:Id|_id)$')


def source_upload_signals(source, application_url):
    """Return literal endpoints and input-property names, never source values.

    Protocol recognition does not execute an upload or grant write permission.
    Only a path actually present in the loaded source becomes an endpoint.
    """
    endpoints = []
    for match in UPLOAD_PATH.finditer(source):
        endpoint = urljoin(application_url, match.group(1))
        if origin(endpoint) == origin(application_url) and endpoint not in endpoints:
            endpoints.append(endpoint)
    return {
        "metadataEndpoints": endpoints,
        "attachmentFields": sorted({name for name in PROPERTY.findall(source)
                                    if ATTACHMENT_NAME.search(name) and ID_SUFFIX.search(name)}),
        "activeStorageImplementation": all(word in source for word in ("signed_id", "direct_upload", "checksum", "byte_size")),
    }

