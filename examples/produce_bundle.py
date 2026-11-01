"""An independent producer using only Python's standard library.

Run with `python -S` to demonstrate that no generator package is imported.
The consumer validates the definitions and this public bundle contract.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=["openapi", "graphql"], required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--operations", type=Path)
    parser.add_argument("--endpoint", required=True)
    options = parser.parse_args()
    parsed = urlsplit(options.endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error("Use an HTTPS endpoint without credentials, query or fragment.")
    authority = parsed.hostname.lower()
    if ":" in authority:
        authority = "[" + authority + "]"
    if parsed.port and parsed.port != 443:
        authority += ":" + str(parsed.port)
    endpoint = urlunsplit(
        ("https", authority, parsed.path or "/", "", "")
    )
    producer = {"name": "standalone-example-producer", "version": "1"}

    def artifact(name, kind, path):
        content = path.read_text(encoding="utf-8")
        return {
            "id": "artifact-" + digest(kind + "\0" + name + "\0" + content),
            "name": name,
            "kind": kind,
            "content": content,
            "sha256": digest(content),
            "provenance": {
                "source": "external",
                "locations": [endpoint],
                "derivedFrom": [],
                "producers": [producer],
            },
        }

    schema = artifact(
        "producer-schema.txt",
        "openapi" if options.protocol == "openapi" else "graphql-schema",
        options.schema,
    )
    artifacts = [schema]
    operations = []
    if options.operations:
        item = artifact(
            "producer-operations.graphql", "graphql-operations", options.operations
        )
        artifacts.append(item)
        operations.append(item["id"])
    bundle = {
        "format": "website-client-discovery",
        "version": 1,
        "stage": "discovery",
        "producer": producer,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
        "services": [
            {
                "id": "service-" + digest(options.protocol + "\0" + endpoint)[:16],
                "protocol": options.protocol,
                "endpoint": endpoint,
                "schemaArtifact": schema["id"],
                "operationArtifacts": operations,
                "confidence": "external",
            }
        ],
    }
    print(json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
