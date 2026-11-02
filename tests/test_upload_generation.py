"""Complete builds own upload methods, commands, tests and portable inputs."""

from website_client.upload_workflows import UploadWorkflow, write_workflows

WORKFLOW = UploadWorkflow("CreateRecord", "https://app.example.test/graphql",
                          "https://app.example.test/rails/active_storage/direct_uploads",
                          "input", "attachmentId", ("a" * 64,))


def test_generated_file_method_and_cli_come_from_manifest(tmp_path):
    import subprocess
    import sys
    from website_client.discovery_manifest.records import Artifact, Kind, Source, Service, Protocol, Manifest
    from website_client.discovery_manifest import write_manifest
    from website_client.project_generation import Project, build_project
    operation = Artifact.create("create.graphql", Kind.GRAPHQL_OPERATIONS,
        "mutation CreateRecord($input: RecordInput!) { create(input:$input) { id } }", Source.FRONTEND)
    contract = Artifact.create("upload-workflows.json", Kind.REPORT, write_workflows((WORKFLOW,)), Source.FRONTEND)
    manifest = Manifest.create((operation, contract), ())
    path = tmp_path / "manifest.json"
    write_manifest(manifest, path)
    project = tmp_path / "generated"
    build_project(Project("file-client", (path,), "https://app.example.test/", required_uploads=("CreateRecord",)), project)
    result = subprocess.run([sys.executable, "-m", "file_client._cli", "CreateRecord", "--help"],
                            cwd=project, capture_output=True, text=True)
    assert result.returncode == 0 and "--receipt" in result.stdout
    assert "create_record_with_file" in (project / "file_client/client.py").read_text()
    assert "Gusto" not in (project / "file_client/upload_runtime.py").read_text()
    rebuilt = tmp_path / "rebuilt"
    build_project(Project.read(project / "client-project.json"), rebuilt)
    tree = lambda root: {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
                         if p.is_file() and '__pycache__' not in str(p)}
    assert tree(project) == tree(rebuilt)
    assert (rebuilt / "file_client/upload_runtime.py").is_file()


def test_cli_preview_sends_no_requests_and_generated_upload_tests_pass(tmp_path):
    import hashlib
    import json
    import subprocess
    import sys
    from website_client.discovery_manifest.records import Artifact, Kind, Source, Manifest
    from website_client.discovery_manifest import write_manifest
    from website_client.project_generation import Project, build_project
    operation = Artifact.create("create.graphql", Kind.GRAPHQL_OPERATIONS,
        "mutation CreateRecord($input: RecordInput!) { create(input:$input) { id } }", Source.FRONTEND)
    workflow = Artifact.create("upload-workflows.json", Kind.REPORT, write_workflows((WORKFLOW,)), Source.FRONTEND)
    path = tmp_path / "manifest.json"
    write_manifest(Manifest.create((operation, workflow), ()), path)
    output = tmp_path / "client"
    build_project(Project("preview-client", (path,), "https://app.example.test/", required_uploads=("CreateRecord",)), output)
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.7\nlocal synthetic receipt")
    supplied = tmp_path / "input.json"
    supplied.write_text(json.dumps({"input": {"amount": "12.34"}}))
    result = subprocess.run([sys.executable, "-m", "preview_client._cli", "CreateRecord", "--preview",
                             "--receipt", str(receipt), "--input", str(supplied)], cwd=output,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    preview = json.loads(result.stdout)
    assert preview["requestsSent"] == 0 and preview["attachmentField"] == "input.attachmentId"
    assert preview["receipt"]["sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    tested = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=output,
                            capture_output=True, text=True, timeout=30)
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "3 passed" in tested.stdout


def test_required_upload_failure_publishes_nothing(tmp_path):
    import pytest
    from website_client.browser import BrowserError
    from website_client.discovery_manifest.records import Artifact, Kind, Source, Service, Protocol, Manifest
    from website_client.discovery_manifest import write_manifest
    from website_client.project_generation import Project, build_project
    operation = Artifact.create("create.graphql", Kind.GRAPHQL_OPERATIONS,
        "mutation CreateRecord($input: RecordInput!) { create(input:$input) { id } }", Source.EXTERNAL)
    target = Service.create(Protocol.GRAPHQL, WORKFLOW.apiEndpoint, operations=(operation.id,))
    path = tmp_path / "manifest.json"
    write_manifest(Manifest.create((operation,), (target,)), path)
    output = tmp_path / "client"
    with pytest.raises(BrowserError, match="Required file-upload workflows"):
        build_project(Project("missing-upload-client", (path,), "https://app.example.test/", required_uploads=("CreateRecord",)), output)
    assert not output.exists()
