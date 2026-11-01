"""Merge validated producer records, resolving each schema choice explicitly."""

from dataclasses import replace
from datetime import datetime, timezone

from ..browser import BrowserError
from .model import MAX_BUNDLE_BYTES, PRODUCER, digest, encoded
from .records import Artifact, Confidence, Manifest, Producer, Service, SourceBundle


def unique(values):
    return tuple(dict.fromkeys(values))


def merge_artifact(prior: Artifact, incoming: Artifact) -> Artifact:
    if (prior.kind, prior.content) != (incoming.kind, incoming.content):
        raise BrowserError("Conflicting artifact IDs across producers.")
    first, second = prior.provenance, incoming.provenance
    return replace(
        prior,
        provenance=replace(
            first,
            locations=unique(first.locations + second.locations),
            derived_from=unique(first.derived_from + second.derived_from),
            producers=unique(first.producers + second.producers),
        ),
    )


def select_schema(
    prior: Service, incoming: Service, artifacts: dict[str, Artifact],
) -> Service:
    """Prefer a complete source over inference; reject unresolved conflicts."""
    existing = artifacts.get(prior.schema_artifact)
    candidate = artifacts.get(incoming.schema_artifact)
    if existing is None:
        return incoming if candidate is not None else prior
    if candidate is None:
        return prior
    if prior.confidence is Confidence.INFERRED and incoming.confidence is not Confidence.INFERRED:
        return incoming
    if existing.sha256 == candidate.sha256:
        return prior
    if prior.confidence is not Confidence.INFERRED and incoming.confidence is Confidence.INFERRED:
        return prior
    raise BrowserError(
        "Conflicting schemas for one service; select or rename the producer's service."
    )


def merge_service(prior: Service, incoming: Service, artifacts: dict[str, Artifact]) -> Service:
    if (prior.protocol, prior.endpoint) != (incoming.protocol, incoming.endpoint):
        raise BrowserError("Conflicting service IDs across producers.")
    selected = select_schema(prior, incoming, artifacts)
    return replace(
        prior, schema_artifact=selected.schema_artifact, confidence=selected.confidence,
        operation_artifacts=unique(prior.operation_artifacts + incoming.operation_artifacts),
    )


def merge_manifests(manifests: tuple[Manifest, ...]) -> Manifest:
    if type(manifests) is not tuple or any(type(item) is not Manifest for item in manifests):
        raise BrowserError("Merge requires validated Manifest records.")
    if (
        not manifests or len(manifests) > 20
        or sum(len(encoded(item.to_dict()).encode()) for item in manifests) > MAX_BUNDLE_BYTES
    ):
        raise BrowserError("Merge at most 20 bundles totaling at most 64 MiB.")
    if len(manifests) == 1:
        return manifests[0]

    artifacts: dict[str, Artifact] = {}
    services: dict[str, Service] = {}
    sources: tuple[SourceBundle, ...] = ()
    for manifest in manifests:
        parent = SourceBundle(digest(manifest.to_dict()), manifest.producer, manifest.created_at)
        sources = unique(sources + (manifest.source_bundles or ()) + (parent,))
        for item in manifest.artifacts:
            prior = artifacts.get(item.id)
            artifacts[item.id] = merge_artifact(prior, item) if prior is not None else item
        for target in manifest.services:
            incoming = target.without_normalization()
            prior = services.get(incoming.id)
            services[incoming.id] = (
                merge_service(prior, incoming, artifacts) if prior is not None else incoming
            )
    latest = max(
        datetime.fromisoformat(item.created_at.upper()).astimezone(timezone.utc)
        for item in manifests
    ).isoformat()
    return Manifest.create(
        tuple(artifacts.values()), tuple(services.values()),
        Producer("website-client-merge", PRODUCER["version"]),
        created_at=latest, source_bundles=sources,
    )


def merge_bundles(bundles):
    """Compatibility adapter for callers supplying version 1 JSON dictionaries."""
    if not bundles or len(bundles) > 20:
        raise BrowserError("Merge at most 20 bundles totaling at most 64 MiB.")
    return merge_manifests(tuple(Manifest.from_dict(value) for value in bundles)).to_dict()
