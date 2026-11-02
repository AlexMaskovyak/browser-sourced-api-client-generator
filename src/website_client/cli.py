"""Python command for website client generation."""

import argparse
import json
import sys

from .browser import BrowserSession, BrowserError, origin
from .capture import NetworkCapture
from .discovery import import_har, new_output, publish


def main(args=None):
    """Parse website discovery and offline pipeline commands and dispatch the selected workflow."""
    parser = argparse.ArgumentParser(
        prog="website-client",
        description="Discover website endpoints from a logged-in browser or existing HAR; generate Python clients from standard definitions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
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
    discover.add_argument(
        "--emit-manifest",
        "--emit-bundle",
        dest="emit_bundle",
        action="store_true",
        help="Write the complete discovery manifest JSON to stdout for command chaining. Also saves manifest.json in --output. --emit-bundle is an alias.",
    )
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
        help="GraphQL operation document, required with --graphql-schema.",
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
    from .pipeline import add_commands, run_offline, generate_bundle

    add_commands(commands, generate_command, definition)
    from .project_generation import (
        add_build_command,
        project_from_options,
        build_project,
    )

    add_build_command(commands)
    options = parser.parse_args(args)
    try:
        if options.command == "build":
            print(
                json.dumps(build_project(project_from_options(options), options.output))
            )
            return 0
        if options.command in {"normalize", "import"}:
            run_offline(options)
            return 0
        if options.command == "generate":
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
                print(json.dumps(report))
                return 0
            if options.service or options.allow_inferred:
                raise BrowserError("--service and --allow-inferred require --bundle.")
            from .generation import generate

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
        if options.command == "discover":
            exploration_options = None
            if options.explore:
                from .exploration import SiteExplorer, validate_exploration

                if options.observe_seconds is not None:
                    raise BrowserError(
                        "--observe-seconds is for the starting-page --auto scan. Use --explore-seconds for exploration."
                    )
                options.auto = True
                exploration_options = {
                    "max_pages": options.max_pages
                    if options.max_pages is not None
                    else 20,
                    "max_actions": options.max_actions
                    if options.max_actions is not None
                    else 60,
                    "max_depth": options.max_depth
                    if options.max_depth is not None
                    else 3,
                    "duration_seconds": options.explore_seconds
                    if options.explore_seconds is not None
                    else 120,
                }
                validate_exploration(**exploration_options)
            elif any(
                value is not None
                for value in (
                    options.max_pages,
                    options.max_actions,
                    options.max_depth,
                    options.explore_seconds,
                )
            ):
                raise BrowserError(
                    "Page, action, depth and exploration time options require --explore."
                )
            output = new_output(options.output)
            automatic_report = None
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
            if options.auto:
                from .self_discovery import (
                    DEFAULT_MAX_PROBES,
                    DEFAULT_OBSERVE_SECONDS,
                    DiscoveryObserver,
                    SelfDiscovery,
                    validate_settings,
                )

                max_probes = (
                    options.max_probes
                    if options.max_probes is not None
                    else DEFAULT_MAX_PROBES
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
                result = import_har(options.har, options.max_entries)
            else:
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
                    session.confirm_login(
                        prompt="Log in and return to the requested website. Press Enter here to start "
                        + ("discovery" if options.auto else "capture")
                        + ". Ctrl+C cancels."
                    )
                    if options.auto:
                        print(
                            "Discovering service definitions. No captured business operation will be replayed.",
                            file=sys.stderr,
                            flush=True,
                        )
                        explorer = (
                            SiteExplorer(session, **exploration_options)
                            if options.explore
                            else None
                        )
                        if explorer is not None:
                            explorer.prepare()
                        result, automatic_report = SelfDiscovery(
                            session,
                            observer,
                            options.max_entries,
                            max_probes,
                            observe_seconds,
                            options.graphql_endpoint,
                            explorer=explorer,
                        ).run()
                    else:
                        result = NetworkCapture(session, options.max_entries).run()
            if not result.entries:
                raise BrowserError(
                    "No eligible requests were found. Reload and browse after capture starts, or check the HAR source."
                )
            files = result.files("har" if options.har else "browser")
            if automatic_report is not None:
                files["self-discovery.json"] = (
                    json.dumps(automatic_report, indent=2) + "\n"
                )
            if automatic_report is not None and "exploration" in automatic_report:
                files["exploration.json"] = (
                    json.dumps(automatic_report["exploration"], indent=2) + "\n"
                )
            from .bundle_capture import capture_manifest
            from .discovery_manifest import encoded

            manifest = capture_manifest(result, files)
            files["manifest.json"] = encoded(manifest.to_dict())
            publish(output, files)
            if options.emit_bundle:
                sys.stdout.write(files["manifest.json"])
                return 0
            print(
                json.dumps(
                    {
                        "harEntries": len(result.entries),
                        "endpoints": len(result.endpoints),
                        "omitted": result.omitted,
                        "files": sorted(files),
                        "liveVerified": False,
                        "clientGenerated": False,
                        "manifest": str(output / "manifest.json"),
                        "bundleVersion": manifest.version,
                    }
                )
            )
            return 0
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
        with BrowserSession(
            options.url, options.browser, options.browser_path
        ) as session:
            result = session.confirm_login()
        print(json.dumps({**result, "sessionClosed": True, "clientGenerated": False}))
        return 0
    except KeyboardInterrupt:
        print("Command cancelled. The temporary browser was closed.", file=sys.stderr)
        return 130
    except BrowserError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        from traceback import extract_tb

        locations = [
            frame
            for frame in extract_tb(error.__traceback__)
            if "/website_client/" in frame.filename
        ]
        location = locations[-1] if locations else None
        detail = (
            f"{type(error).__name__} at {location.filename.rsplit('/', 1)[-1]}:{location.lineno}"
            if location
            else type(error).__name__
        )
        print(
            "Command failed ("
            + detail
            + "). Check the inputs and use a new output directory before retrying.",
            file=sys.stderr,
        )
        return 1


def entrypoint():
    """Run the installed website-client command and propagate its exit status."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
