"""Write command results while keeping local output failures inside the call boundary."""

import os
from pathlib import Path
import sys


def _discard_broken_stdout() -> None:
    # A failed buffered flush otherwise runs again during Python shutdown and
    # changes the intended exit status to 120. Redirect only the broken stream.
    descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(descriptor, sys.stdout.fileno())
    finally:
        os.close(descriptor)


def write_result_text(text: str, output_path: str | Path | None) -> None:
    """Flush stdout now, or exclusively create a private result file.

    Callers own outcome reporting. A local output error must never re-execute an
    already completed remote operation or imply that the remote operation failed.
    """
    if output_path is None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:
            _discard_broken_stdout()
            raise
        return
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
