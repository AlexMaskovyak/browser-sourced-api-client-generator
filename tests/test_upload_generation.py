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
    build_project(Project("file-client", (path,), "https://app.example.test/"), project)
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
