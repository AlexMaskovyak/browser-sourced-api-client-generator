"""Run a Python script in isolation and fail on any attempted socket operation."""

import runpy
import sys


def reject_network(event: str, arguments: tuple) -> None:
    if event.startswith("socket."):
        raise AssertionError(f"Offline client attempted network access: {event}")


def main() -> None:
    sys.addaudithook(reject_network)
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
