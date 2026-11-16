"""Python commands for browser discovery and standalone client generation."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .discovery_manifest.records import Manifest

from .browser import BrowserSession, BrowserError, origin
from .capture import NetworkCapture
from .discovery import Discovery, import_har, new_output, publish


def _add_login_command(commands: Any) -> None:
    login = commands.add_parser(
        "login",
        help="Open a browser and sign in manually.",
        description="Open a temporary browser, sign in and complete verification codes there, "
        "then press Enter in this terminal. The browser closes afterward. This does not save "
        "a login or generate a client. Ctrl+C cancels.",
    )
    login.add_argument(
        "--url", required=True, help="HTTPS address of the website to sign into."
    )
    login.add_argument("--browser", choices=["chrome", "chromium"], default="chrome")
    login.add_argument(
        "--browser-path",
        help="Absolute path to an installed browser executable, if needed.",
    )


def _add_discovery_auto_arguments(discover: argparse.ArgumentParser) -> None:
    discover.add_argument(
        "--auto",
        action="store_true",
        help="After login, reload and find service definitions automatically. Sends schema GETs and read-only GraphQL introspection queries.",
    )
    discover.add_argument(
        "--max-probes",
        type=int,
        help="Automatic discovery request limit, 1 to 30 (default 12).",
    )
    discover.add_argument(
        "--observe-seconds",
        type=int,
        help="Observe traffic after reloading for 1 to 30 seconds (default 5).",
    )
    discover.add_argument(
        "--graphql-endpoint",
        action="append",
        default=[],
        help="Explicit HTTPS endpoint to introspect with --auto; repeat for multiple endpoints.",
    )


def _add_discovery_exploration_arguments(discover: argparse.ArgumentParser) -> None:
    discover.add_argument(
        "--explore",
        action="store_true",
        help="Walk pages, menus, tabs and pagination algorithmically, inventory forms and loaded frontend operations, then discover schemas. Implies --auto; no model or API key.",
    )
    discover.add_argument(
        "--max-pages", type=int, help="Exploration page limit, 1 to 100 (default 20)."
    )
    discover.add_argument(
        "--max-actions",
        type=int,
        help="Exploration control-click limit, 1 to 500 (default 60).",
    )
    discover.add_argument(
        "--max-depth", type=int, help="Exploration link depth, 0 to 10 (default 3)."
    )
    discover.add_argument(
        "--explore-seconds",
        type=int,
        help="Exploration time limit, 1 to 1800 seconds (default 120).",
    )


def _add_discovery_command(commands: Any) -> None:
    discover = commands.add_parser(
        "discover",
        help="Find schemas or capture traffic after login, or import a HAR offline.",
        description="Save a sanitized HAR, endpoint evidence, GraphQL operations and inferred OpenAPI. "
        "Actual OpenAPI and introspection responses are validated separately. "
        "Manual capture never replays traffic. --auto sends definition reads after login.",
    )
    source = discover.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--url", help="HTTPS website to open for manual login, then capture."
    )
    source.add_argument(
        "--har", help="Existing HAR file to analyze offline, up to 32 MiB."
    )
    discover.add_argument(
        "--output", required=True, help="New output directory; its parent must exist."
    )
    discover.add_argument(
        "--max-entries",
        type=int,
        default=1000,
        help="Maximum retained requests, 1 to 10000 (default 1000).",
    )
    discover.add_argument("--browser", choices=["chrome", "chromium"])
    discover.add_argument("--browser-path")
    _add_discovery_auto_arguments(discover)
    _add_discovery_exploration_arguments(discover)
    discover.add_argument(
        "--emit-manifest",
        "--emit-bundle",
        dest="emit_bundle",
        action="store_true",
        help="Write the complete discovery manifest JSON to stdout for command chaining. Also saves manifest.json in --output. --emit-bundle is an alias.",
    )


def _add_generate_command(commands: Any) -> tuple[argparse.ArgumentParser, Any]:
    generate_command = commands.add_parser(
        "generate",
        help="Generate a Python client from captured operations or definitions.",
        description="Generate from captured requests and operations. GraphQL documents work without a schema; an optional schema enables Ariadne Codegen models. HTTP definitions use openapi-python-client. Sends no API requests.",
    )
    definition = generate_command.add_mutually_exclusive_group(required=True)
    definition.add_argument(
        "--openapi",
        help="Local OpenAPI 3.0/3.1 JSON or YAML file with bundled references.",
    )
    definition.add_argument(
        "--graphql-schema",
        help="Local GraphQL schema SDL or complete introspection JSON.",
    )
    generate_command.add_argument(
        "--operations",
        help="Optional named GraphQL operation document; a schema also works without this option.",
    )
    generate_command.add_argument(
        "--output",
        required=True,
        help="New output directory for the standalone package.",
    )
    generate_command.add_argument(
        "--name",
        default="service-client",
        help="Generated package and CLI name, lowercase with hyphens (default service-client).",
    )
    return generate_command, definition


def _add_automatic_command(commands: Any) -> None:
    automatic = commands.add_parser(
        "run",
        help="Generate all eligible clients from a URL or saved input after initial login.",
    )
    automatic.add_argument(
        "source",
        help="Website URL or saved HAR, definition, operations, workflow or manifest file.",
    )
    automatic.add_argument(
        "--input",
        action="append",
        default=[],
        help="Additional initially supplied manifest or bundled definition; repeat to combine.",
    )
    automatic.add_argument(
        "--output",
        required=True,
        help="New output directory. Writes sanitized evidence, coverage and generated packages.",
    )
    automatic.add_argument(
        "--name",
        default="service-client",
        help="Base package name; stable service suffixes keep namespaces distinct.",
    )
    automatic.add_argument(
        "--endpoint",
        help="Initial service address when a saved definition does not contain one.",
    )
    automatic.add_argument(
        "--operations",
        help="Initially supplied named GraphQL operations. A schema also works without this option.",
    )
    automatic.add_argument(
        "--browser", choices=["chrome", "chromium"], default="chrome"
    )
    automatic.add_argument("--browser-path")
    automatic.add_argument("--max-pages", type=int, default=20)
    automatic.add_argument("--max-actions", type=int, default=60)
    automatic.add_argument("--max-depth", type=int, default=3)
    automatic.add_argument("--explore-seconds", type=int, default=120)


def build_parser() -> argparse.ArgumentParser:
    """Build all command help locally, without opening a browser or sending requests."""
    from .pipeline import add_commands

    parser = argparse.ArgumentParser(
        prog="website-client",
        description="Discover website endpoints from a logged-in browser or existing HAR; generate Python clients from standard definitions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _add_login_command(commands)
    _add_discovery_command(commands)
    generate_command, definition = _add_generate_command(commands)
    add_commands(commands, generate_command, definition)
    _add_automatic_command(commands)
    return parser


def _run_command(options: argparse.Namespace) -> int:
    from .autonomous import run

    result = run(
        options.source,
        options.output,
        name=options.name,
        inputs=options.input,
        endpoint=options.endpoint,
        operations=options.operations,
        browser=options.browser,
        browser_path=options.browser_path,
        max_pages=options.max_pages,
        max_actions=options.max_actions,
        max_depth=options.max_depth,
        duration_seconds=options.explore_seconds,
    )
    print(json.dumps(result))
    return 0 if result["services"] else 1


def _offline_command(options: argparse.Namespace) -> int:
    from .pipeline import run_offline

    run_offline(options)
    return 0


def _generate_command(options: argparse.Namespace) -> int:
    from .pipeline import generate_bundle
    from .generation import generate

    if options.bundle:
        if options.operations:
            raise BrowserError(
                "--operations is not used with --bundle; bind operations during normalization."
            )
        report = generate_bundle(
            options.bundle,
            options.output,
            options.name,
            options.service,
            options.allow_inferred,
        )
    else:
        if options.service or options.allow_inferred:
            raise BrowserError("--service and --allow-inferred require --bundle.")
        if options.openapi and options.operations:
            raise BrowserError("--operations is only used with --graphql-schema.")
        report = generate(
            options.output,
            options.name,
            options.openapi,
            options.graphql_schema,
            options.operations,
        )
    print(json.dumps(report))
    return 0


def _exploration_options(options: argparse.Namespace) -> dict[str, int] | None:
    if not options.explore:
        values = (
            options.max_pages,
            options.max_actions,
            options.max_depth,
            options.explore_seconds,
        )
        if any(value is not None for value in values):
            raise BrowserError(
                "Page, action, depth and exploration time options require --explore."
            )
        return None
    from .exploration import validate_exploration

    if options.observe_seconds is not None:
        raise BrowserError(
            "--observe-seconds is for the starting-page --auto scan. Use --explore-seconds for exploration."
        )
    options.auto = True
    limits = {
        "max_pages": options.max_pages if options.max_pages is not None else 20,
        "max_actions": options.max_actions if options.max_actions is not None else 60,
        "max_depth": options.max_depth if options.max_depth is not None else 3,
        "duration_seconds": options.explore_seconds
        if options.explore_seconds is not None
        else 120,
    }
    validate_exploration(**limits)
    return limits


def _validate_discovery(options: argparse.Namespace) -> None:
    if not 1 <= options.max_entries <= 10000:
        raise BrowserError("--max-entries must be between 1 and 10000.")
    if options.auto and options.har:
        raise BrowserError("--auto requires --url; HAR import stays offline.")
    if not options.auto and (
        options.max_probes is not None
        or options.observe_seconds is not None
        or options.graphql_endpoint
    ):
        raise BrowserError("Probe and observation options require --auto.")


def _automatic_capture(
    session: BrowserSession,
    options: argparse.Namespace,
    exploration: dict | None,
    observer: Any,
    max_probes: int,
    observe_seconds: int,
) -> tuple[Discovery, dict]:
    from .exploration import SiteExplorer
    from .self_discovery import SelfDiscovery

    print(
        "Discovering service definitions. No captured business operation will be replayed.",
        file=sys.stderr,
        flush=True,
    )
    explorer = SiteExplorer(session, **exploration) if options.explore else None
    if explorer is not None:
        explorer.prepare()
    return SelfDiscovery(
        session,
        observer,
        options.max_entries,
        max_probes,
        observe_seconds,
        options.graphql_endpoint,
        explorer=explorer,
    ).run()


def _browser_capture(
    options: argparse.Namespace,
    exploration: dict | None,
    observer: Any,
    max_probes: int | None,
    observe_seconds: int | None,
) -> tuple[Discovery, dict | None]:
    origin(options.url)
    if not sys.stdin.isatty():
        raise BrowserError(
            "Browser discovery requires an interactive terminal, without piped input."
        )
    with BrowserSession(
        options.url,
        options.browser or "chrome",
        options.browser_path,
        on_event=observer.event if options.auto else None,
        block_service_workers=options.explore,
    ) as session:
        action = "discovery" if options.auto else "capture"
        session.confirm_login(
            prompt="Log in and return to the requested website. Press Enter here to start "
            + action
            + ". Ctrl+C cancels."
        )
        if options.auto:
            return _automatic_capture(
                session, options, exploration, observer, max_probes, observe_seconds
            )
        return NetworkCapture(session, options.max_entries).run(), None


def _capture_source(
    options: argparse.Namespace, exploration: dict | None
) -> tuple[Discovery, dict | None]:
    observer, max_probes, observe_seconds = None, None, None
    if options.auto:
        from .self_discovery import (
            DEFAULT_MAX_PROBES,
            DEFAULT_OBSERVE_SECONDS,
            DiscoveryObserver,
            validate_settings,
        )

        max_probes = (
            options.max_probes if options.max_probes is not None else DEFAULT_MAX_PROBES
        )
        observe_seconds = (
            options.observe_seconds
            if options.observe_seconds is not None
            else DEFAULT_OBSERVE_SECONDS
        )
        validate_settings(max_probes, observe_seconds, options.graphql_endpoint)
        observer = DiscoveryObserver(options.url)
    if options.har:
        if options.browser or options.browser_path:
            raise BrowserError("Browser options cannot be combined with --har.")
        return import_har(options.har, options.max_entries), None
    return _browser_capture(options, exploration, observer, max_probes, observe_seconds)


def _discovery_files(
    result: Discovery, automatic_report: dict | None, source_kind: str
) -> tuple[dict[str, str], Manifest]:
    from .bundle_capture import capture_manifest
    from .discovery_manifest import encoded
    from .privacy import safe_ui_report

    files = result.files(source_kind)
    if automatic_report is not None:
        report = safe_ui_report(automatic_report)
        files["self-discovery.json"] = json.dumps(report, indent=2) + "\n"
        if "exploration" in report:
            files["exploration.json"] = (
                json.dumps(report["exploration"], indent=2) + "\n"
            )
    manifest = capture_manifest(result, files)
    files["manifest.json"] = encoded(manifest.to_dict())
    return files, manifest


def _discovery_summary(
    result: Discovery, files: dict[str, str], output: Path, version: int
) -> dict[str, Any]:
    return {
        "harEntries": len(result.entries),
        "endpoints": len(result.endpoints),
        "omitted": result.omitted,
        "files": sorted(files),
        "liveVerified": False,
        "clientGenerated": False,
        "manifest": str(output / "manifest.json"),
        "bundleVersion": version,
    }


def _discover_command(options: argparse.Namespace) -> int:
    exploration = _exploration_options(options)
    output = new_output(options.output)
    _validate_discovery(options)
    result, automatic_report = _capture_source(options, exploration)
    if not result.entries:
        raise BrowserError(
            "No eligible requests were found. Reload and browse after capture starts, or check the HAR source."
        )
    files, manifest = _discovery_files(
        result, automatic_report, "har" if options.har else "browser"
    )
    publish(output, files)
    if options.emit_bundle:
        sys.stdout.write(files["manifest.json"])
    else:
        print(json.dumps(_discovery_summary(result, files, output, manifest.version)))
    return 0


def _login_command(options: argparse.Namespace) -> int:
    origin(options.url)
    if not sys.stdin.isatty():
        raise BrowserError(
            "Login requires an interactive terminal, without piped input."
        )
    print(
        "Opening a temporary browser. Enter credentials only into the website.",
        file=sys.stderr,
        flush=True,
    )
    with BrowserSession(options.url, options.browser, options.browser_path) as session:
        result = session.confirm_login()
    print(json.dumps({**result, "sessionClosed": True, "clientGenerated": False}))
    return 0


def _show_failure(command: str, error: BrowserError | None) -> None:
    if command == "run":
        dependency_missing = error is not None and str(error).startswith(
            "har_dependency_required"
        )
        reason = (
            "har_dependency_required"
            if dependency_missing
            else "invalid_or_unsupported_input"
        )
        print(
            json.dumps({"coverage": "unsupported", "reason": reason}), file=sys.stderr
        )
    elif error is not None:
        print(str(error), file=sys.stderr)
    else:
        print(
            "Command failed. Check the inputs and use a new output directory before retrying.",
            file=sys.stderr,
        )


def main(args: list[str] | None = None) -> int:
    """Parse, dispatch, and apply the common bounded failure policy."""
    options = build_parser().parse_args(args)
    handlers = {
        "run": _run_command,
        "import": _offline_command,
        "normalize": _offline_command,
        "generate": _generate_command,
        "discover": _discover_command,
        "login": _login_command,
    }
    try:
        return handlers[options.command](options)
    except KeyboardInterrupt:
        print("Command cancelled. The temporary browser was closed.", file=sys.stderr)
        return 130
    except BrowserError as error:
        _show_failure(options.command, error)
    except Exception:
        # Parser, validator, and driver messages may include private source values.
        # Only authored BrowserError messages are eligible for direct display.
        _show_failure(options.command, None)
    return 1


def entrypoint() -> None:
    """Run the installed command and propagate its exit status."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
