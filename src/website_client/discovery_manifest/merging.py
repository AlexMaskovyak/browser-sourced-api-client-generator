"""Merge producer evidence without retaining stale computed selections."""

import copy
from datetime import datetime, timezone

from ..browser import BrowserError
from .model import MAX_BUNDLE_BYTES, PRODUCER, digest, encoded, new_bundle
from .validation import validate_bundle


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
        datetime.fromisoformat(item["createdAt"].upper()).astimezone(timezone.utc)
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
            for computed in (
                "sourceSchemaArtifact", "compiledOperationsArtifact", "status",
                "issues", "operationNames",
            ):
                target.pop(computed, None)
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
                elif not (
                    prior["confidence"] != "inferred"
                    and target["confidence"] == "inferred"
                ):
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
