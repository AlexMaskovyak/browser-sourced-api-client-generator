"""Expose the same public Python methods as deterministic CLI commands."""

import argparse
import importlib
from importlib.resources import files
import json
import sys

from ._cli import serialize


def boolean(value):
    """Accept explicit boolean values without leaking a parser traceback."""
    if value.lower() not in {"true", "false"}:
        raise argparse.ArgumentTypeError("Use true or false.")
    return value.lower() == "true"


def main(args=None):
    arguments = list(sys.argv[1:] if args is None else args)
    if arguments and arguments[0] == "login":
        from ._login import main as login_main

        return login_main(arguments)
    legacy = json.loads(files(__package__).joinpath("_operations.json").read_text())
    if arguments and arguments[0] in legacy:
        from ._cli import main as legacy_main

        return legacy_main(arguments)
    metadata = json.loads(files(__package__).joinpath("_public.json").read_text())
    parser = argparse.ArgumentParser(
        description="Read the recorded website using your private login profile."
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    for name, operation in metadata["operations"].items():
        command = commands.add_parser(name.replace("_", "-"), help="Reads remote data.")
        for parameter in operation["parameters"]:
            scalar = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": boolean,
            }[parameter["type"]]
            command.add_argument(
                "--" + parameter["python"].replace("_", "-"),
                dest=parameter["python"],
                required=parameter["required"],
                type=scalar,
                default=argparse.SUPPRESS,
            )
    for name in legacy:
        commands.add_parser(
            name,
            add_help=False,
            help="Upstream endpoint command; use its --help for inputs.",
        )
    commands.add_parser(
        "login",
        add_help=False,
        help="Explicitly sign in and renew the private profile.",
    )
    options = vars(parser.parse_args(arguments))
    name = options.pop("operation").replace("-", "_")
    try:
        client = importlib.import_module(__package__).Client()
        result = getattr(client, name)(**options)
        print(json.dumps(serialize(result)))
        return 0
    except Exception as error:
        from .runtime_profile import ProfileError
        from .public_runtime import OperationError

        message = (
            str(error)
            if isinstance(error, (ProfileError, OperationError))
            else "request_failed"
        )
        print(json.dumps({"error": message}), file=sys.stderr)
        return 1


def entrypoint():
    raise SystemExit(main())
