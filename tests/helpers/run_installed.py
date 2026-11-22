"""Run a pip-installed script or module from an isolated target directory.

Arguments: TARGET_DIRECTORY (script|module) ENTRYPOINT [ENTRYPOINT_ARGUMENTS ...].
The caller uses Python -I so the source checkout cannot shadow the installed wheel.
"""

from pathlib import Path
import runpy
import sys


def main() -> None:
    target, kind, entrypoint, *arguments = sys.argv[1:]
    sys.path.insert(0, str(Path(target).resolve()))
    sys.argv = [entrypoint, *arguments]
    if kind == "module":
        runpy.run_module(entrypoint, run_name="__main__", alter_sys=True)
    elif kind == "script":
        runpy.run_path(entrypoint, run_name="__main__")
    else:
        raise ValueError("Expected script or module")


if __name__ == "__main__":
    main()
