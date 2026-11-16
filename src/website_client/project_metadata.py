"""Add offline contract tests and component notices to standalone distributions."""

from importlib.metadata import version
from pathlib import Path
from string import Template

RESOURCES = Path(__file__).parent


def _notice_sources(backends: tuple[str, ...]) -> dict[str, Path]:
    sources = {
        "LICENSE.website-client": RESOURCES / "licenses" / "LICENSE.website-client"
    }
    for backend in backends:
        if backend == "arazzo-runner":
            for prefix in ("LICENSE", "NOTICE"):
                name = prefix + ".arazzo-runner"
                sources[name] = RESOURCES / "native_dependencies" / name
        elif backend in {"openapi-python-client", "ariadne-codegen"}:
            name = "LICENSE." + backend
            sources[name] = RESOURCES / "licenses" / name
        else:
            raise ValueError("unsupported_notice_component")
    return sources


def write_distribution_support(
    project: Path,
    package: str,
    name: str,
    backends: tuple[str, ...],
    *,
    workflow: bool = False,
) -> None:
    """Ship notices inside both the source project and installed wheel, plus offline tests."""
    notices = project / package / "_notices"
    notices.mkdir()
    for filename, source in _notice_sources(backends).items():
        (notices / filename).write_text(source.read_text())
    components = [
        "- browser-sourced-api-client-generator runtime: MIT license; see LICENSE.website-client."
    ]
    for backend in backends:
        components.append(
            f"- {backend} {version(backend)}: see LICENSE.{backend}"
            + (" and NOTICE.arazzo-runner." if backend == "arazzo-runner" else ".")
        )
    text = (
        "# Component notices\n\nThis distribution contains generated request code and runtime adapters from the\nfollowing components. Their included notices remain applicable. Independently\ninstalled dependencies retain their own distribution licenses.\n\n"
        + "\n".join(components)
        + "\n"
    )
    (notices / "NOTICE.md").write_text(text)
    (project / "NOTICE.md").write_text(text)
    if not (project / "LICENSE").exists():
        (project / "LICENSE").write_text(
            (RESOURCES / "licenses" / "LICENSE.website-client").read_text()
        )
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    filename = (
        "test_workflow_contract.py.tmpl" if workflow else "test_sdk_contract.py.tmpl"
    )
    source = Template((RESOURCES / "templates" / filename).read_text()).substitute(
        name=name, package=package
    )
    (tests / "test_packaged_contract.py").write_text(source)
