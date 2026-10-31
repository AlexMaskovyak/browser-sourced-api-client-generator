"""Standalone command wrapper copied into generated packages."""

import argparse
from contextlib import ExitStack
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


def entrypoint():
    operations = json.loads(files(__package__).joinpath("_operations.json").read_text())
    parser = argparse.ArgumentParser(
        description="Call generated endpoint methods. Help is local and sends no requests."
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    functions = {}
    for name, operation in operations.items():
        if operation["kind"] == "openapi":
            function = getattr(
                importlib.import_module(operation["module"]), "sync_detailed"
            )
        else:
            function = getattr(
                importlib.import_module(__package__ + ".client").Client,
                operation["method"],
            )
        functions[name] = function
        parameters = inspect.signature(function).parameters
        hints = typing.get_type_hints(function)
        field_help = []
        for field, parameter in parameters.items():
            if field in {"self", "client"} or parameter.kind in {
                parameter.VAR_KEYWORD,
                parameter.VAR_POSITIONAL,
            }:
                continue
            required = parameter.default is parameter.empty
            field_help.append(
                f"{field}: {hints.get(field, 'JSON')} ({'required' if required else 'optional'})"
            )
        label = "WRITES remote data" if operation["write"] else "READS remote data"
        command = commands.add_parser(
            name,
            help=label,
            description=label + ". " + operation.get("description", ""),
            epilog="JSON input fields: "
            + ("; ".join(field_help) or "none")
            + ". Models use JSON objects; complex types may require the generated Python API.",
        )
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
            help="Read temporary header values as a JSON object from standard input, up to 64 KiB.",
        )
        command.add_argument(
            "--login",
            action="store_true",
            help="Open a temporary browser, sign in, then press Enter to run this endpoint through that session.",
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
            help="Additional header name to observe during login; its value stays in memory. Requires --login.",
        )
        command.add_argument(
            "--confirm-write", help="For writes, repeat this exact operation name."
        )
        command.add_argument(
            "--output", help="Save JSON to a new private file instead of stdout."
        )
    options = parser.parse_args()
    operation = operations[options.operation]
    sent = False
    try:
        if operation["write"] and options.confirm_write != options.operation:
            raise ValueError("This write requires --confirm-write " + options.operation)
        if options.login and options.headers_stdin:
            raise ValueError("Browser login cannot be combined with --headers-stdin")
        if not options.login and (
            options.login_url
            or options.browser
            or options.browser_path
            or options.session_header
        ):
            raise ValueError("Browser options require --login")
        if options.login and not sys.stdin.isatty():
            raise ValueError("Browser login requires an interactive terminal")
        parsed = urlsplit(options.url)
        if (
            (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "Use HTTPS, or HTTP on a loopback address, without embedded credentials."
            )
        if options.output and os.path.lexists(options.output):
            raise ValueError("Output already exists")
        data = {}
        if options.input:
            source = Path(options.input)
            if source.stat().st_size > 1024 * 1024:
                raise ValueError("Input exceeds 1 MiB")
            data = json.loads(source.read_text())
        if not isinstance(data, dict):
            raise ValueError("Input must be a JSON object")
        headers = {}
        if options.headers_stdin:
            raw = sys.stdin.read(65537)
            if len(raw) > 65536:
                raise ValueError("Headers exceed 64 KiB")
            headers = json.loads(raw)
            if not isinstance(headers, dict) or not all(
                isinstance(k, str)
                and isinstance(v, str)
                and "\n" not in v
                and "\r" not in v
                for k, v in headers.items()
            ):
                raise ValueError("Invalid runtime headers")
        function = functions[options.operation]
        signature, hints = inspect.signature(function), typing.get_type_hints(function)
        allowed = {
            name
            for name, parameter in signature.parameters.items()
            if name not in {"self", "client"}
            and parameter.kind not in {parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL}
        }
        if set(data) - allowed:
            raise ValueError("Input has unknown fields; check operation help")
        kwargs = {
            name: convert(value, hints.get(name, typing.Any))
            for name, value in data.items()
        }
        for name in allowed:
            if (
                signature.parameters[name].default is inspect.Parameter.empty
                and name not in kwargs
            ):
                raise ValueError("A required input is missing: " + name)
        module = importlib.import_module(__package__ + ".client")
        with ExitStack() as stack:
            transport = None
            if options.login:
                from .session_http import BrowserLoginTransport

                transport = stack.enter_context(
                    BrowserLoginTransport(
                        options.url,
                        operation.get("httpMethod", "POST"),
                        options.login_url,
                        options.browser or "chrome",
                        options.browser_path,
                        options.session_header,
                    )
                )
            if operation["kind"] == "openapi":
                client = module.Client(
                    base_url=options.url, headers=headers, follow_redirects=False
                )
                if transport:
                    import httpx

                    client.set_httpx_client(
                        httpx.Client(
                            base_url=options.url,
                            transport=transport,
                            follow_redirects=False,
                        )
                    )
                client = stack.enter_context(client)
                sent = True
                response = function(client=client, **kwargs)
                if int(response.status_code) >= 300:
                    raise RuntimeError("Endpoint returned a non-success status")
                result = response.parsed
                if result is None:
                    result = {"status": int(response.status_code)}
            else:
                arguments = {"url": options.url, "headers": headers}
                if transport:
                    import httpx

                    arguments["http_client"] = httpx.Client(
                        transport=transport, follow_redirects=False
                    )
                client = stack.enter_context(module.Client(**arguments))
                sent = True
                result = getattr(client, operation["method"])(**kwargs)
        text = json.dumps(serialize(result)) + "\n"
        if options.output:
            descriptor = os.open(
                options.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w") as stream:
                stream.write(text)
        else:
            print(text, end="")
    except KeyboardInterrupt:
        print("Command cancelled. The temporary browser was closed.", file=sys.stderr)
        if sent:
            print(
                "The request may have succeeded; check the service before retrying.",
                file=sys.stderr,
            )
        raise SystemExit(130)
    except Exception as error:
        if isinstance(error, ValueError) and not sent:
            # Validation libraries can echo private input values. Only our small
            # static set of messages is suitable for terminal output.
            message = str(error)
            prefixes = (
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
            print(
                message
                if message.startswith(prefixes)
                else "Input validation failed. Check operation help.",
                file=sys.stderr,
            )
        elif not sent and type(error).__name__ == "BrowserError":
            print(str(error), file=sys.stderr)
        else:
            print(
                "Endpoint call failed. A request may have succeeded; check the service before retrying. No automatic retry occurred.",
                file=sys.stderr,
            )
        raise SystemExit(1)


if __name__ == "__main__":
    entrypoint()
