"""Public operations for portable discovery manifests.

model constructs entries; validation checks the exchange contract; transport
handles files and streams; merging reconciles independently produced inputs.
"""

from .model import artifact, digest, encoded, endpoint_url, new_bundle, observed_endpoint, service, sha256
from .merging import merge_bundles, merge_manifests
from .transport import read_bundle, read_manifest, read_text, write_bundle, write_manifest
from .validation import validate_bundle
