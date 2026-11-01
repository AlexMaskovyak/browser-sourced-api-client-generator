"""Read bounded UTF-8 inputs and write exclusive files or JSON stdout."""

import json
import os
from pathlib import Path
import sys

from ..browser import BrowserError
from .model import MAX_BUNDLE_BYTES, encoded
from .records import Manifest


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


def read_manifest(source, limit=MAX_BUNDLE_BYTES) -> Manifest:
    try:
        value = json.loads(
            read_text(source, limit),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except BrowserError:
        raise
    except Exception:
        raise BrowserError("Supply a valid JSON discovery bundle.") from None
    return Manifest.from_dict(value)


def read_bundle(source, limit=MAX_BUNDLE_BYTES):
    return read_manifest(source, limit).to_dict()


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
    write_manifest(Manifest.from_dict(value), output)


def write_manifest(manifest: Manifest, output="-"):
    if type(manifest) is not Manifest:
        raise BrowserError("Output requires a validated Manifest record.")
    text = encoded(manifest.to_dict())
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

