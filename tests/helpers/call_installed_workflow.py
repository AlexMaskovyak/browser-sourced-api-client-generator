"""Exercise the installed workflow Python API without importing the generator."""

import importlib
import importlib.metadata
import json
from pathlib import Path
import sys


def main() -> None:
    target, payload_path = sys.argv[1:]
    sys.path.insert(0, target)
    workflow = importlib.import_module("neutral_workflows")
    result = workflow.execute_workflow(
        "upload", {"file": Path(payload_path).read_bytes()}
    )
    print(json.dumps(result))
    assert "website_client" not in sys.modules, (
        "Installed client imported the generator"
    )
    assert importlib.metadata.version("arazzo-runner") == "0.9.6+websiteclient.1"


if __name__ == "__main__":
    main()
