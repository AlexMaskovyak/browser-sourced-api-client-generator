"""Build a complete operation client from portable inputs and public runtime settings.

The command owns packaging, documentation, synthetic checks and source snapshots.
It never logs in or executes a service operation. Credential values are runtime
inputs and are deliberately absent from the project format.
"""

from dataclasses import dataclass
import ast
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from .browser import BrowserError, origin
from .discovery import new_output
from .discovery_manifest import (
    merge_manifests,
    read_manifest,
    read_text,
    write_manifest,
)
from .discovery_manifest.records import (
    Artifact,
    Kind,
    Manifest,
    Protocol,
    Service,
    Source,
)
from .generation import package_name_for, publish_tree, write_json
from .normalization import normalize_manifest, select_service
from .pipeline import generate_bundle
from .session_http import BrowserLoginTransport


@dataclass(frozen=True, slots=True)
class Project:
    """Version 1 build inputs, with paths resolved relative to the project file.

    Header names select values observed during a future login. They never contain
    the values themselves. lockfile is an existing Poetry lock to verify and copy.
    Without one, output can be installed but dependency resolution is not pinned.
    """

    name: str
    manifests: tuple[Path, ...]
    login_url: str
    session_headers: tuple[str, ...] = ()
    service: str | None = None
    lockfile: Path | None = None
    allow_inferred: bool = False
    required_operations: tuple[str, ...] = ()

    def __post_init__(self):
        """Reject invalid names, unbounded input lists and unsafe header selections."""
        if type(self.allow_inferred) is not bool:
            raise BrowserError("Project allowInferred must be a boolean.")
        if len(self.required_operations) > 100 or any(
            not isinstance(name, str)
            or not re.fullmatch(r"[_A-Za-z][_0-9A-Za-z]*", name)
            for name in self.required_operations
        ):
            raise BrowserError(
                "Required operations must be at most 100 operation names."
            )
        package_name_for(self.name)
        origin(self.login_url)
        if not self.manifests or len(self.manifests) > 20:
            raise BrowserError("Supply between one and twenty project manifests.")
        for header in self.session_headers:
            BrowserLoginTransport._validate_session_header_name(header)
        if len(set(self.session_headers)) > 24:
            raise BrowserError("Select at most 24 additional session headers.")

    @classmethod
    def read(cls, path):
        """Read strict, versioned JSON without accepting arbitrary files to copy."""
        path = Path(path).resolve()
        value = json.loads(read_text(path, 65536))
        allowed = {
            "version",
            "name",
            "manifests",
            "loginUrl",
            "sessionHeaders",
            "service",
            "lockfile",
            "allowInferred",
            "requiredOperations",
        }
        if (
            not isinstance(value, dict)
            or value.keys() - allowed
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise BrowserError(
                "Use a version 1 client project with supported fields only."
            )
        for key in ("name", "loginUrl"):
            if not isinstance(value.get(key), str):
                raise BrowserError("Project name and loginUrl must be strings.")
        manifests, headers = value.get("manifests"), value.get("sessionHeaders", [])
        if not isinstance(manifests, list) or any(
            not isinstance(item, str) for item in manifests
        ):
            raise BrowserError("Project manifests must be a list of paths.")
        if not isinstance(headers, list) or any(
            not isinstance(item, str) for item in headers
        ):
            raise BrowserError("Project sessionHeaders must contain header names only.")
        required = value.get("requiredOperations", [])
        if not isinstance(required, list):
            raise BrowserError("Project requiredOperations must be a list of names.")
        for key in ("service", "lockfile"):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise BrowserError(
                    "Project service and lockfile must be strings or null."
                )
        return cls(
            value["name"],
            tuple(path.parent / item for item in manifests),
            value["loginUrl"],
            tuple(headers),
            value.get("service"),
            path.parent / value["lockfile"] if value.get("lockfile") else None,
            value.get("allowInferred", False),
            tuple(required),
        )


def add_build_command(commands):
    """Register complete project generation separately from lower-level SDK generation."""
    command = commands.add_parser(
        "build",
        help="Generate a complete Poetry client project, README and tests; no service requests.",
    )
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--project",
        help="Version 1 client-project.json; relative paths resolve beside this file.",
    )
    source.add_argument(
        "--manifest",
        action="append",
        help="Saved manifest; repeat to merge inputs automatically.",
    )
    command.add_argument(
        "--output", required=True, help="New directory; existing output is refused."
    )
    command.add_argument(
        "--require-operation",
        action="append",
        default=[],
        help="Required operation name; repeat to reject incomplete discovery before publication.",
    )
    command.add_argument("--name", help="Package and CLI name when using --manifest.")
    command.add_argument(
        "--service", help="Select one API when inputs contain several."
    )
    command.add_argument(
        "--login-url", help="Public website sign-in URL; requires --manifest."
    )
    command.add_argument(
        "--session-header",
        action="append",
        default=[],
        help="Additional header NAME to observe at login, never a value; repeat as needed.",
    )
    command.add_argument(
        "--allow-inferred",
        action="store_true",
        help="Build a reviewed inferred HTTP definition; preserve this permission in rebuild inputs.",
    )
    command.add_argument(
        "--lockfile",
        help="Existing Poetry lock to include and validate against generated packaging.",
    )


def project_from_options(options):
    """Choose a complete saved project or explicit inputs, rejecting ambiguous overrides."""
    if options.project:
        if (
            options.name
            or options.service
            or options.login_url
            or options.session_header
            or options.lockfile
            or options.allow_inferred
            or options.require_operation
        ):
            raise BrowserError(
                "Do not override project settings; edit the project file and rebuild."
            )
        return Project.read(options.project)
    if not options.name or not options.login_url:
        raise BrowserError("Building from manifests requires --name and --login-url.")
    return Project(
        options.name,
        tuple(Path(item).resolve() for item in options.manifest),
        options.login_url,
        tuple(options.session_header),
        options.service,
        Path(options.lockfile).resolve() if options.lockfile else None,
        options.allow_inferred,
        tuple(options.require_operation),
    )


def portable_manifest(target, metadata, normalized):
    """Retain selected operation documents and request conventions, dropping capture context.

    Original reports may include unrelated endpoints, private filenames and source
    locations. Only the contracts already shipped in the SDK are needed to rebuild.
    Each operation keeps its own document so fragments remain source-local.
    """
    if target.schema_artifact is not None:
        # Preserve the canonical selected definition rather than source locations
        # or unrelated captured requests. This supports both schema backends.
        indexed = {item.id: item for item in normalized.artifacts}
        original = indexed[target.schema_artifact]
        schema = Artifact.create(
            "definition", original.kind, original.content, Source.EXTERNAL
        )
        documents = ()
        if target.compiled_operations_artifact is not None:
            operations = indexed[target.compiled_operations_artifact]
            documents = (
                Artifact.create(
                    "operations.graphql",
                    Kind.GRAPHQL_OPERATIONS,
                    operations.content,
                    Source.EXTERNAL,
                ),
            )
        selected = Service.create(
            target.protocol,
            target.endpoint,
            schema.id,
            operations=tuple(item.id for item in documents),
            confidence=target.confidence,
        )
        return Manifest.create(
            (schema,) + documents, (selected,), created_at=normalized.created_at
        )
    documents = tuple(
        Artifact.create(
            name + ".graphql",
            Kind.GRAPHQL_OPERATIONS,
            item["document"],
            Source.EXTERNAL,
        )
        for name, item in metadata.items()
    )
    endpoint = urlsplit(target.endpoint)
    report = {
        "endpoints": [
            {
                "origin": f"{endpoint.scheme}://{endpoint.netloc}",
                "path": endpoint.path or "/",
                "method": item["httpMethod"],
                "queryParameters": ["operationName"]
                if item["operationNameInUrl"]
                else [],
                "graphql": {"name": name},
            }
            for name, item in metadata.items()
            if item["requestStyleObserved"]
        ]
    }
    evidence = Artifact.create(
        "discovery.json",
        Kind.REPORT,
        json.dumps(report, sort_keys=True),
        Source.EXTERNAL,
    )
    return Manifest.create(
        documents + (evidence,),
        (
            Service.create(
                Protocol.GRAPHQL,
                target.endpoint,
                operations=tuple(item.id for item in documents),
            ),
        ),
        created_at=normalized.created_at,
    )


def required_inputs(stage, package, operation):
    """Read required Python arguments from generated syntax without importing code.

    Backend models and Python aliases remain authoritative. Do not infer required
    fields from URL patterns or assume all read operations have zero arguments.
    """
    if operation["kind"] == "openapi":
        path = stage.joinpath(*operation["module"].split(".")).with_suffix(".py")
        name = "sync_detailed"
    else:
        path, name = stage / package / "client.py", operation["method"]
    matches = [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise BrowserError("Generated operation has no unambiguous Python signature.")
    args = matches[0].args
    positional = args.posonlyargs + args.args
    required = positional[: len(positional) - len(args.defaults)]
    required += [
        item
        for item, default in zip(args.kwonlyargs, args.kw_defaults)
        if default is None
    ]
    return tuple(item.arg for item in required if item.arg not in {"self", "client"})


def project_readme(project, endpoint, metadata, stage, package):
    """Generate runnable commands from the same defaults used by the packaged CLI."""
    available = {
        name: required_inputs(stage, package, item) for name, item in metadata.items()
    }
    preferred = project.required_operations or tuple(metadata)
    reads = [name for name in preferred if not metadata[name]["write"]]
    ready = [name for name in reads if not available[name]]
    example = (ready or reads or list(metadata))[0]
    if ready:
        call = f"poetry run {project.name} {example} --login"
        action = "The command sends one read and prints JSON."
    else:
        call = f"poetry run {project.name} {example} --help"
        action = (
            "This command displays help only. Create a JSON object containing the required "
            "fields shown there and pass its path with --input FILE when calling the operation."
        )
    kind = next(iter(metadata.values()))["kind"]
    if kind == "graphql-operation":
        validation = (
            "Declared standard scalar types are validated; custom input fields and response "
            "shapes remain JSON without a complete server schema. Python mutation calls "
            "require Client(..., allow_writes=True)."
        )
    else:
        validation = (
            "Input and response models come from the supplied API definition. "
            "Python calls use the backend's generated API; CLI write confirmation "
            "does not impose a separate write-permission switch on those Python calls."
        )
    return f"""# {project.name}

Python methods and CLI commands generated from {len(metadata)} API operations.
The generator produced every file in this project. No service-specific source
patches are needed. This operation set does not imply coverage of every service API.

## Install and inspect

Use Python 3.11 or newer, Poetry 2.2 or newer and installed Chrome for browser login.
From this generated directory:

```sh
poetry install --with dev
poetry run {project.name} --help
poetry run {project.name} {example} --help
poetry run pytest -q
poetry build
```

Installation may download dependencies. Help, tests and package builds send no
service requests. Tests use synthetic responses; they do not prove live compatibility.

## Call an operation

The configured endpoint is `{endpoint}`. With an interactive terminal:

```sh
{call}
```

Add `--login` when executing an operation to open `{project.login_url}`. Sign in,
open the relevant application page and wait for it to load, then press Enter in
the terminal. {action} Use `--output NEW_FILE` to save a private result. Existing files
are refused. Each --login invocation opens a fresh login; the browser closes afterward.

Configured additional session-header names: {", ".join(project.session_headers) or "none"}.
Their values are observed in memory at the API origin. Never put tokens in source
files or command arguments. Missing required headers can cause HTTP 403. Inspect
the application request and update `client-project.json` if its contract changes.

For operations with inputs, create a JSON object using the names/types in that
operation's help and pass `--input FILE`. Write commands require
`--confirm-write OPERATION` with the exact operation name. No automatic retries occur. Check the service before
retrying an uncertain write. Receipt uploads, pagination and other multi-step
workflows require their own captured operations and orchestration.

## Reproduce this project

`inputs/manifest.json` contains the selected API definition or operation documents
and request conventions. `client-project.json` supplies public endpoint/login configuration
and header names. No saved login or account values are needed to build.
Using an installed compatible generator, from this directory:

```sh
website-client build --project client-project.json --output ../{project.name}-rebuilt
```

The output directory must not exist and its parent must exist. The command reads
local inputs, generates code, documentation and tests, and validates an included
Poetry lock. It does not call the service. Compare outputs before replacing an
existing project. Reuse the same generator revision and lock for reproducibility.

## Validation and ownership

{validation}
The service remains authoritative for its records. Generation and
synthetic tests do not verify submissions or payment. HTTP failures report status
without response bodies. Authentication stays in browser/process memory.
"""


def build_project(project, output):
    """Generate, verify dependency inputs and publish one untouched installable tree.

    All enrichment happens inside generator-owned staging. Failed lock validation
    leaves the requested output absent, rather than publishing a partial project.
    """
    output = new_output(output)
    # Use the manifest reader's byte limits and normalization for every producer.
    inputs, remaining = [], 64 * 1024 * 1024
    for path in project.manifests:
        document = read_manifest(path, limit=remaining)
        remaining -= len(json.dumps(document.to_dict()).encode())
        if remaining < 0:
            raise BrowserError("Project inputs exceed the 64 MiB aggregate limit.")
        inputs.append(document)
    normalized = normalize_manifest(merge_manifests(tuple(inputs)))
    target = select_service(
        normalized, project.service, allow_inferred=project.allow_inferred
    )
    package = package_name_for(project.name)
    with tempfile.TemporaryDirectory(prefix="website-client-project-") as directory:
        stage = Path(directory) / "project"
        # Reuse the same backend selection as ordinary generation. Complete
        # packaging must not substitute a protocol-specific request implementation.
        manifest_path = Path(directory) / "input-manifest.json"
        write_manifest(normalized, manifest_path)
        report = generate_bundle(
            manifest_path, stage, project.name, target.id, project.allow_inferred
        )
        metadata = json.loads((stage / package / "_operations.json").read_text())
        missing = sorted(set(project.required_operations) - metadata.keys())
        if missing:
            raise BrowserError(
                "Required operations were not discovered: "
                + ", ".join(missing)
                + ". No client was published."
            )
        defaults = {
            "url": target.endpoint,
            "loginUrl": project.login_url,
            "sessionHeaders": list(project.session_headers),
        }
        write_json(stage / package / "_defaults.json", defaults)
        # A public build report needs source hashes, not private capture lineage.
        report.pop("lineage", None)
        write_json(stage / "generation.json", report)
        with (stage / "pyproject.toml").open("a") as stream:
            stream.write('\n[dependency-groups]\ndev = ["pytest>=8,<10"]\n')
        # The SDK includes shared MIT-licensed runtime source, so retain its
        # distribution notice rather than dropping it when creating a clean repo.
        (stage / "LICENSE").write_text(
            Path(__file__)
            .with_name("templates")
            .joinpath("runtime_license.txt")
            .read_text()
        )
        (stage / ".gitignore").write_text(
            ".venv/\n__pycache__/\n.pytest_cache/\ndist/\n.private/\n"
        )
        (stage / "README.md").write_text(
            project_readme(project, target.endpoint, metadata, stage, package)
        )
        (stage / "inputs").mkdir()
        snapshot = portable_manifest(target, metadata, normalized)
        write_manifest(snapshot, stage / "inputs" / "manifest.json")
        recipe = {
            "version": 1,
            "name": project.name,
            "manifests": ["inputs/manifest.json"],
            "loginUrl": project.login_url,
            "sessionHeaders": list(project.session_headers),
            "service": None,
            "lockfile": "poetry.lock" if project.lockfile else None,
            "allowInferred": project.allow_inferred,
            "requiredOperations": list(project.required_operations),
        }
        write_json(stage / "client-project.json", recipe)
        (stage / "tests").mkdir()
        (stage / "tests" / "test_client.py").write_text(render_tests(package, metadata))
        if project.lockfile:
            # Reuse an explicit dependency input, then ask Poetry to verify its
            # content hash. Never silently relock against current registry state.
            if project.lockfile.stat().st_size > 8 * 1024 * 1024:
                raise BrowserError("Poetry lock exceeds the 8 MiB limit.")
            shutil.copyfile(project.lockfile, stage / "poetry.lock")
            checked = subprocess.run(
                ["poetry", "check", "--lock"],
                cwd=stage,
                capture_output=True,
                timeout=120,
            )
            if checked.returncode:
                raise BrowserError(
                    "Poetry lock does not match generated packaging; create a compatible lock and rebuild."
                )
        publish_tree(stage, output)
        return report


def render_operation_tests(package):
    """Emit synthetic operation checks without embedding any source-account values."""
    return f'''"""Generated checks use synthetic responses and never call the service."""
import inspect
import json
from importlib.resources import files
import httpx
import pytest
from {package} import Client

OPERATIONS = json.loads(files('{package}').joinpath('_operations.json').read_text())

@pytest.mark.parametrize('name', sorted(OPERATIONS))
def test_documented_method(name):
    """Every source operation has an inspectable, documented Python method."""
    assert inspect.getdoc(getattr(Client, OPERATIONS[name]['method']))

def test_all_operation_requests_and_write_guards():
    """Exercise request construction without a server or schema-specific fixture values."""
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={{'data': {{'synthetic': True}}}})
    with httpx.Client(transport=httpx.MockTransport(response)) as http:
        with Client('https://example.test/graphql', http_client=http, allow_writes=True) as client:
            for name, bound in client._operations.items():
                # Request mechanics are independent of server-specific input shapes.
                # Variable validation has its own generator tests, so patch only that
                # step to exercise every captured operation without invented inputs.
                from unittest.mock import patch
                with patch('{package}.operation_runtime.validate_variables', return_value={{}}):
                    assert client._execute(name, {{}}) == {{'synthetic': True}}
                    assert calls[-1].method == bound.method
                    payload = json.loads(calls[-1].content) if bound.method == 'POST' else dict(calls[-1].url.params)
                    assert payload['operationName'] == name
        with Client('https://example.test/graphql', http_client=http) as client:
            for name, bound in client._operations.items():
                if bound.operation.write:
                    from unittest.mock import patch
                    before = len(calls)
                    with patch('{package}.operation_runtime.validate_variables', return_value={{}}):
                        with pytest.raises(ValueError, match='allow_writes'):
                            client._execute(name, {{}})
                    assert len(calls) == before
'''


def render_tests(package, metadata):
    """Emit backend-neutral command checks, plus operation-only transport checks.

    CLI write guards apply to every backend. Operation-document transport tests
    additionally exercise its runtime; typed backends retain their own model and
    request implementations rather than a synthetic replacement runtime.
    """
    if all(item["kind"] == "graphql-operation" for item in metadata.values()):
        return render_operation_tests(package)
    return f'''"""Generated checks are local and make no service requests."""
import inspect
import json
from importlib.resources import files
import pytest
from {package} import _cli

OPERATIONS = json.loads(files('{package}').joinpath('_operations.json').read_text())

@pytest.mark.parametrize('name', sorted(OPERATIONS))
def test_operation_help_and_callable(name):
    """Every packaged operation resolves to the backend's callable and local help."""
    parser, functions = _cli.build_parser(OPERATIONS, {{'url': 'https://example.test'}})
    assert callable(functions[name])
    assert inspect.signature(functions[name])
    with pytest.raises(SystemExit) as exited:
        parser.parse_args([name, '--help'])
    assert exited.value.code == 0

@pytest.mark.parametrize('name', [name for name, item in OPERATIONS.items() if item['write']])
def test_write_requires_confirmation_before_transport(name, monkeypatch, capsys):
    """Unconfirmed CLI writes must fail before any generated endpoint method runs."""
    def forbidden(*args, **kwargs):
        raise AssertionError('An unconfirmed write reached the transport')
    monkeypatch.setattr(_cli, 'execute_operation', forbidden)
    assert _cli.main([name, '--url', 'https://example.test']) == 1
    assert '--confirm-write ' + name in capsys.readouterr().err
'''
