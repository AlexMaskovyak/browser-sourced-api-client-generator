"""Python command for website client generation."""

import argparse
import json
import sys

from .browser import BrowserSession, BrowserError, origin
from .capture import NetworkCapture
from .discovery import import_har, new_output, publish


def main(args=None):
    parser = argparse.ArgumentParser(prog="website-client", description="Discover website endpoints from a logged-in browser or existing HAR; generate Python clients from standard definitions.")
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="Open a browser and sign in manually.",
        description="Open a temporary browser, sign in and complete verification codes there, "
        "then press Enter in this terminal. The browser closes afterward. This does not save "
        "a login or generate a client. Ctrl+C cancels.")
    login.add_argument("--url", required=True, help="HTTPS address of the website to sign into.")
    login.add_argument("--browser", choices=["chrome", "chromium"], default="chrome")
    login.add_argument("--browser-path", help="Absolute path to an installed browser executable, if needed.")
    discover = commands.add_parser("discover", help="Capture traffic after login, or analyze an existing HAR offline.",
        description="Save a sanitized HAR, endpoint evidence, GraphQL operations and inferred OpenAPI. "
        "Actual observed OpenAPI and introspection responses are validated separately. No request is replayed.")
    source = discover.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="HTTPS website to open for manual login, then capture.")
    source.add_argument("--har", help="Existing HAR file to analyze offline, up to 32 MiB.")
    discover.add_argument("--output", required=True, help="New output directory; its parent must exist.")
    discover.add_argument("--max-entries", type=int, default=1000, help="Maximum retained requests, 1 to 10000 (default 1000).")
    discover.add_argument("--browser", choices=["chrome", "chromium"])
    discover.add_argument("--browser-path")
    generate_command = commands.add_parser("generate", help="Generate a standalone Python client from a standard definition.",
        description="Use openapi-python-client for OpenAPI, or Ariadne Codegen for GraphQL schemas plus operation documents. Sends no API requests.")
    definition = generate_command.add_mutually_exclusive_group(required=True)
    definition.add_argument("--openapi", help="Local OpenAPI 3.0/3.1 JSON or YAML file with bundled references.")
    definition.add_argument("--graphql-schema", help="Local GraphQL schema SDL or complete introspection JSON.")
    generate_command.add_argument("--operations", help="GraphQL operation document, required with --graphql-schema.")
    generate_command.add_argument("--output", required=True, help="New output directory for the standalone package.")
    generate_command.add_argument("--name", default="service-client", help="Generated package and CLI name, lowercase with hyphens (default service-client).")
    options = parser.parse_args(args)
    try:
        if options.command == "generate":
            from .generation import generate
            if options.openapi and options.operations: raise BrowserError("--operations is only used with --graphql-schema.")
            report = generate(options.output, options.name, options.openapi, options.graphql_schema, options.operations)
            print(json.dumps(report))
            return 0
        if options.command == "discover":
            output = new_output(options.output)
            if not 1 <= options.max_entries <= 10000: raise BrowserError("--max-entries must be between 1 and 10000.")
            if options.har:
                if options.browser or options.browser_path: raise BrowserError("Browser options cannot be combined with --har.")
                result = import_har(options.har, options.max_entries)
            else:
                origin(options.url)
                if not sys.stdin.isatty(): raise BrowserError("Browser discovery requires an interactive terminal, without piped input.")
                with BrowserSession(options.url, options.browser or "chrome", options.browser_path) as session:
                    session.confirm_login(prompt="Log in and return to the requested website. Press Enter here to start capture. Ctrl+C cancels.")
                    result = NetworkCapture(session, options.max_entries).run()
            if not result.entries: raise BrowserError("No eligible requests were found. Reload and browse after capture starts, or check the HAR source.")
            files = result.files("har" if options.har else "browser")
            publish(output, files)
            print(json.dumps({"harEntries": len(result.entries), "endpoints": len(result.endpoints), "omitted": result.omitted,
                              "files": sorted(files), "liveVerified": False, "clientGenerated": False}))
            return 0
        origin(options.url)
        if not sys.stdin.isatty():
            raise BrowserError("Login requires an interactive terminal, without piped input.")
        print("Opening a temporary browser. Enter credentials only into the website.", file=sys.stderr, flush=True)
        with BrowserSession(options.url, options.browser, options.browser_path) as session:
            result = session.confirm_login()
        print(json.dumps({**result, "sessionClosed": True, "clientGenerated": False}))
        return 0
    except KeyboardInterrupt:
        print("Command cancelled. The temporary browser was closed.", file=sys.stderr)
        return 130
    except BrowserError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Command failed. Check the inputs and use a new output directory before retrying.", file=sys.stderr)
        return 1


def entrypoint():
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
