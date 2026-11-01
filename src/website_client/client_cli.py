"""Standalone command wrapper copied into generated packages."""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import importlib
from importlib.resources import files
import inspect
import json
import os
from pathlib import Path
import sys
import types
import typing
from urllib.parse import urlsplit

BYTES_PER_KIBIBYTE = 1024
BYTES_PER_MEBIBYTE = 1024 * BYTES_PER_KIBIBYTE
# Bound user-controlled files and credential streams before model validation
# or browser startup. Credentials have a smaller budget than endpoint inputs.
MAX_INPUT_JSON_BYTES = BYTES_PER_MEBIBYTE
MAX_RUNTIME_HEADERS_BYTES = 64 * BYTES_PER_KIBIBYTE
PRIVATE_FILE_MODE = 0o600
FIRST_NON_SUCCESS_HTTP_STATUS = 300
CANCELLED_EXIT_CODE = 130

# These parameters belong to the generated client, not the user's JSON payload.
CLIENT_ARGUMENT_NAMES = {"self", "client"}
VARIADIC_PARAMETER_KINDS = {
    inspect.Parameter.VAR_KEYWORD,
    inspect.Parameter.VAR_POSITIONAL,
}
SAFE_INPUT_ERROR_PREFIXES = (
    "This write requires",
    "Use HTTPS",
    "Output already exists",
    "Input exceeds",
    "Input must",
    "Headers exceed",
    "Invalid runtime",
    "Input has unknown",
    "A required input is missing",
    "Browser login",
    "Browser options",
)


@dataclass
class RequestState:
    # Set before calling the typed method: a later failure can be ambiguous.
    started: bool = False


def convert(value, annotation):
    if annotation in {typing.Any, inspect.Parameter.empty}:
        return value
    base = typing.get_origin(annotation)
    if base in {typing.Union, types.UnionType}:
        for member in typing.get_args(annotation):
            try:
                return convert(value, member)
            except (TypeError, ValueError, KeyError):
                pass
        raise ValueError("Input does not match its declared type")
    if annotation is type(None):
        if value is not None:
            raise ValueError()
        return None
    if base is list:
        if not isinstance(value, list):
            raise ValueError()
        return [convert(item, typing.get_args(annotation)[0]) for item in value]
    if base is dict:
        if not isinstance(value, dict):
            raise ValueError()
        return value
    if base is typing.Literal:
        if value not in typing.get_args(annotation):
            raise ValueError()
        return value
    if annotation in {date, datetime}:
        if not isinstance(value, str):
            raise ValueError()
        return annotation.fromisoformat(value)
    if annotation in {str, int, float, bool}:
        if type(value) is not annotation and not (
            annotation is float and type(value) is int
        ):
            raise ValueError()
        return value
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return annotation(value)
    if hasattr(annotation, "model_validate"):
        return annotation.model_validate(value)
    if hasattr(annotation, "from_dict") and isinstance(value, dict):
        return annotation.from_dict(value)
    raise ValueError("Unsupported input type; use the generated Python API")


def serialize(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "to_dict"):
        return serialize(value.to_dict())
    if isinstance(value, Enum):
        return serialize(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Use the generated Python API for this response type")


def load_operation_function(operation):
    """Use the upstream generated method as the only request builder."""
    if operation["kind"] == "openapi":
        module = importlib.import_module(operation["module"])
        return module.sync_detailed
    client_class = importlib.import_module(__package__ + ".client").Client
    return getattr(client_class, operation["method"])


def input_parameters(function):
    """Select only explicit endpoint inputs, excluding client and catch-all args."""
    return {
        name: parameter
        for name, parameter in inspect.signature(function).parameters.items()
        if name not in CLIENT_ARGUMENT_NAMES
        and parameter.kind not in VARIADIC_PARAMETER_KINDS
    }


def describe_inputs(function):
    hints = typing.get_type_hints(function)
    descriptions = []
    for name, parameter in input_parameters(function).items():
        required = parameter.default is inspect.Parameter.empty
        requirement = "required" if required else "optional"
        descriptions.append(f"{name}: {hints.get(name, 'JSON')} ({requirement})")
    return "; ".join(descriptions) or "none"


def describe_operation_inputs(operation, function):
    if operation["kind"] != "graphql-operation":
        return describe_inputs(function)
    return (
        "; ".join(
            item["parameter"]
            + ": "
            + item["type"]
            + (
                " (required)"
                if item["required"]
                else " (optional; omitted unless supplied)"
            )
            for item in operation["variables"]
        )
        or "none"
    )


def add_operation_arguments(command):
    command.add_argument(
        "--url",
        required=True,
        help="Service base URL for OpenAPI; exact endpoint URL for GraphQL.",
    )
    command.add_argument(
        "--input", help="JSON object file containing the input fields shown below."
    )
    command.add_argument(
        "--headers-stdin",
        action="store_true",
        help=(
            "Read temporary header values as a JSON object from standard input, "
            f"up to {MAX_RUNTIME_HEADERS_BYTES // BYTES_PER_KIBIBYTE} KiB."
        ),
    )
    command.add_argument(
        "--login",
        action="store_true",
        help=(
            "Open a temporary browser, sign in, then press Enter to run "
            "this endpoint through that session."
        ),
    )
    command.add_argument(
        "--login-url",
        help="Website address for sign-in when different from --url; requires --login.",
    )
    command.add_argument(
        "--browser",
        choices=["chrome", "chromium"],
        help="Login browser (default chrome); requires --login.",
    )
    command.add_argument(
        "--browser-path",
        help="Absolute path to the login browser executable; requires --login.",
    )
    command.add_argument(
        "--session-header",
        action="append",
        default=[],
        help=(
            "Additional header name to observe during login; its value stays "
            "in memory. Requires --login."
        ),
    )
    command.add_argument(
        "--confirm-write", help="For writes, repeat this exact operation name."
    )
    command.add_argument(
        "--output", help="Save JSON to a new private file instead of stdout."
    )


def build_parser(operations):
    parser = argparse.ArgumentParser(
        description="Call generated endpoint methods. Help is local and sends no requests."
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    functions = {}
    for name, operation in operations.items():
        function = load_operation_function(operation)
        functions[name] = function
        label = "WRITES remote data" if operation["write"] else "READS remote data"
        command = commands.add_parser(
            name,
            help=label,
            description=label + ". " + operation.get("description", ""),
            epilog=(
                "JSON input fields: "
                + describe_operation_inputs(operation, function)
                + ". Models use JSON objects; complex types may require "
                "the generated Python API."
            ),
        )
        add_operation_arguments(command)
    return parser, functions


def validate_execution_options(options, operation):
    """Reject invalid inputs and unconfirmed writes before opening a browser."""
    if operation["write"] and options.confirm_write != options.operation:
        raise ValueError("This write requires --confirm-write " + options.operation)
    if options.login and options.headers_stdin:
        raise ValueError("Browser login cannot be combined with --headers-stdin")
    has_browser_options = (
        options.login_url
        or options.browser
        or options.browser_path
        or options.session_header
    )
    if not options.login and has_browser_options:
        raise ValueError("Browser options require --login")
    if options.login and not sys.stdin.isatty():
        raise ValueError("Browser login requires an interactive terminal")

    validate_service_url(options.url)
    if options.output and os.path.lexists(options.output):
        raise ValueError("Output already exists")


def validate_service_url(url):
    # Permit unencrypted HTTP only for local development and test services.
    parsed = urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(
            "Use HTTPS, or HTTP on a loopback address, without embedded credentials."
        )


def read_input_json(input_path):
    if not input_path:
        return {}
    source = Path(input_path)
    if source.stat().st_size > MAX_INPUT_JSON_BYTES:
        limit_mebibytes = MAX_INPUT_JSON_BYTES // BYTES_PER_MEBIBYTE
        raise ValueError(f"Input exceeds {limit_mebibytes} MiB")
    data = json.loads(source.read_text())
    if not isinstance(data, dict):
        raise ValueError("Input must be a JSON object")
    return data


def read_runtime_headers(enabled):
    """Read a bounded object without echoing its credential values."""
    if not enabled:
        return {}

    # Read bytes so the limit also holds for multi-byte UTF-8 credentials. One
    # extra byte detects overflow without consuming an unbounded input stream.
    raw_header_bytes = sys.stdin.buffer.read(MAX_RUNTIME_HEADERS_BYTES + 1)
    if len(raw_header_bytes) > MAX_RUNTIME_HEADERS_BYTES:
        limit_kibibytes = MAX_RUNTIME_HEADERS_BYTES // BYTES_PER_KIBIBYTE
        raise ValueError(f"Headers exceed {limit_kibibytes} KiB")
    headers = json.loads(raw_header_bytes.decode("utf-8"))
    if not isinstance(headers, dict):
        raise ValueError("Invalid runtime headers")
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("Invalid runtime headers")
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid runtime headers")
    return headers


def bind_input_arguments(function, data):
    """Convert JSON into the upstream method's scalars, enums and typed models.

    The generated signatures define required fields. Their model adapters own
    nested validation, so the CLI does not implement a second API input schema.
    """
    parameters = input_parameters(function)
    if set(data) - parameters.keys():
        raise ValueError("Input has unknown fields; check operation help")

    hints = typing.get_type_hints(function)
    arguments = {
        name: convert(value, hints.get(name, typing.Any))
        for name, value in data.items()
    }
    for name, parameter in parameters.items():
        if parameter.default is inspect.Parameter.empty and name not in arguments:
            raise ValueError("A required input is missing: " + name)
    return arguments


def enter_browser_transport(stack, options, operation):
    if not options.login:
        return None
    from .session_http import BrowserLoginTransport

    return stack.enter_context(
        BrowserLoginTransport(
            options.url,
            operation.get("httpMethod", "POST"),
            options.login_url,
            options.browser or "chrome",
            options.browser_path,
            options.session_header,
        )
    )


def enter_generated_client(stack, options, operation, headers, transport):
    """Inject authentication transport while retaining the upstream client."""
    module = importlib.import_module(__package__ + ".client")
    if operation["kind"] == "openapi":
        client = module.Client(
            base_url=options.url, headers=headers, follow_redirects=False
        )
        if transport is not None:
            import httpx

            client.set_httpx_client(
                httpx.Client(
                    base_url=options.url,
                    transport=transport,
                    follow_redirects=False,
                )
            )
    else:
        client_options = {"url": options.url, "headers": headers}
        if transport is not None:
            import httpx

            client_options["http_client"] = stack.enter_context(
                httpx.Client(transport=transport, follow_redirects=False)
            )
        if operation["kind"] == "graphql-operation":
            client_options["allow_writes"] = operation["write"]
        client = module.Client(**client_options)
    return stack.enter_context(client)


def execute_operation(options, operation, function, arguments, headers, state):
    # Keep the client and browser alive for the typed method; close both on
    # success, failure or cancellation before serializing the returned data.
    with ExitStack() as stack:
        transport = enter_browser_transport(stack, options, operation)
        client = enter_generated_client(stack, options, operation, headers, transport)
        state.started = True
        if operation["kind"] == "openapi":
            response = function(client=client, **arguments)
            if int(response.status_code) >= FIRST_NON_SUCCESS_HTTP_STATUS:
                raise RuntimeError("Endpoint returned a non-success status")
            if response.parsed is None:
                return {"status": int(response.status_code)}
            return response.parsed

        method = getattr(client, operation["method"])
        return method(**arguments)


def write_result(result, output_path):
    text = json.dumps(serialize(result)) + "\n"
    if not output_path:
        print(text, end="")
        return
    descriptor = os.open(
        output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)


def show_failure(error, state):
    if isinstance(error, ValueError) and not state.started:
        # Model errors may echo private inputs. Only our own validation messages
        # are eligible for display; all other diagnostics stay bounded.
        message = str(error)
        if not message.startswith(SAFE_INPUT_ERROR_PREFIXES):
            message = "Input validation failed. Check operation help."
        print(message, file=sys.stderr)
    elif (
        type(error).__name__ == "OperationError"
        and type(getattr(error, "http_status", None)) is int
    ):
        print(
            f"Endpoint returned HTTP {error.http_status}. "
            "No automatic retry occurred; check the service before retrying.",
            file=sys.stderr,
        )
    elif type(error).__name__ == "BrowserError":
        print(str(error), file=sys.stderr)
    elif not state.started:
        print(
            "Command setup failed before the endpoint method ran. "
            "Check the generated package and dependencies.",
            file=sys.stderr,
        )
    else:
        print(
            "Endpoint call failed. A request may have succeeded; check the "
            "service before retrying. No automatic retry occurred.",
            file=sys.stderr,
        )


def main(args=None):
    operations = json.loads(files(__package__).joinpath("_operations.json").read_text())
    parser, functions = build_parser(operations)
    options = parser.parse_args(args)
    operation = operations[options.operation]
    function = functions[options.operation]
    state = RequestState()

    try:
        validate_execution_options(options, operation)
        data = read_input_json(options.input)
        headers = read_runtime_headers(options.headers_stdin)
        arguments = bind_input_arguments(function, data)
        if operation["kind"] == "graphql-operation":
            from .operation_runtime import validate_arguments

            validate_arguments(__package__, options.operation, arguments)
        result = execute_operation(
            options, operation, function, arguments, headers, state
        )
        write_result(result, options.output)
        return 0
    except KeyboardInterrupt:
        print("Command cancelled. The temporary browser was closed.", file=sys.stderr)
        if state.started:
            print(
                "The request may have succeeded; check the service before retrying.",
                file=sys.stderr,
            )
        return CANCELLED_EXIT_CODE
    except Exception as error:
        show_failure(error, state)
        return 1


def entrypoint():
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
