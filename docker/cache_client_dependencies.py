"""Cache locked runtime wheels for offline, independent generated-client tests."""

from pathlib import Path
import subprocess
import sys
import tomllib


packages = tomllib.loads(Path("poetry.lock").read_text())["package"]
versions = {item["name"]: item["version"] for item in packages}
constraints = Path("/tmp/client-runtime-constraints.txt")
constraints.write_text("\n".join(f"{name}=={version}" for name, version in versions.items()) + "\n")
subprocess.run([sys.executable, "-m", "pip", "download", "--timeout", "120", "--only-binary", ":all:",
    "--dest", "/opt/client-wheels", "--constraint", str(constraints),
    *(f"{name}=={versions[name]}" for name in ("httpx", "attrs", "pydantic", "websocket-client"))], check=True)
