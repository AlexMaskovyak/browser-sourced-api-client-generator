"""Inventory declared writes without submitting forms or executing source."""

import ast
import hashlib
import json
import re
from urllib.parse import urljoin

from graphql import parse, print_ast
from graphql.language.ast import DocumentNode
from graphql.language.visitor import Visitor, visit

from .discovery import FIELD, MAX_BODY, operation
from .exploration_policy import safe_address
from .upload_discovery import source_upload_signals

MAX_FORMS = 30
MAX_FIELDS = 100
# Modern applications load many small shared chunks before feature code. Keep
# a separate byte cap while allowing the feature sources to be inspected.
MAX_MODULES = 4096
MAX_MODULE_BYTES = 4 * MAX_BODY
MAX_SOURCE_BYTES = 32 * MAX_BODY
MAX_DOCUMENTS_PER_MODULE = 100
# Only literal source strings are decoded. Interpolated templates and executable
# expressions are not evaluated or guessed.
LITERAL = r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)'
GRAPHQL_LITERAL = re.compile(LITERAL, re.S)


def inspect_forms(page):
    forms = page.locator('form, [role="form"]')
    result = []
    truncated = forms.count() > MAX_FORMS
    for index in range(min(forms.count(), MAX_FORMS)):
        form = forms.nth(index)
        native_form = form.locator("xpath=self::form").count() > 0
        method = (
            form.get_attribute("method") or ("get" if native_form else "unknown")
        ).upper()
        fields = form.locator("input, select, textarea, button")
        row = {
            "action": safe_address(
                urljoin(page.url, form.get_attribute("action") or page.url)
            ),
            "method": method,
            "enctype": form.get_attribute("enctype")
            or "application/x-www-form-urlencoded",
            "source": "declared_form",
            "executed": False,
            "apiContractVerified": False,
            "fields": [],
            "submitters": [],
            "fieldsTruncated": fields.count() > MAX_FIELDS,
        }
        field_count = 0
        for element in ("input", "select", "textarea", "button"):
            elements = form.locator(element)
            for field_index in range(min(elements.count(), MAX_FIELDS - field_count)):
                field_count += 1
                field = elements.nth(field_index)
                name = field.get_attribute("name")
                if element == "button" or field.get_attribute("type") in {
                    "submit",
                    "image",
                }:
                    row["submitters"].append(
                        {
                            "type": field.get_attribute("type") or "submit",
                            "action": safe_address(
                                urljoin(
                                    page.url,
                                    field.get_attribute("formaction")
                                    or form.get_attribute("action")
                                    or page.url,
                                )
                            ),
                            "method": (
                                field.get_attribute("formmethod") or method
                            ).upper(),
                            "executed": False,
                        }
                    )
                if not name:
                    continue
                attributes = {
                    key: field.get_attribute(key)
                    for key in (
                        "type",
                        "accept",
                        "minlength",
                        "maxlength",
                        "min",
                        "max",
                        "step",
                    )
                }
                row["fields"].append(
                    {
                        "name": name
                        if FIELD.fullmatch(name)
                        else "[field name omitted]",
                        "element": element,
                        **{
                            key: value[:200]
                            for key, value in attributes.items()
                            if value is not None
                        },
                        "required": field.get_attribute("required") is not None,
                        "multiple": field.get_attribute("multiple") is not None,
                        "disabled": field.get_attribute("disabled") is not None,
                    }
                )
                # Never read input values, selected options or hidden tokens.
        result.append(row)
    return result, truncated


def extract_documents(source):
    if len(source.encode()) > MAX_MODULE_BYTES:
        return []
    result = []
    for match in GRAPHQL_LITERAL.finditer(source):
        raw = match.group(1)
        # Bundlers also store fragments in ordinary string constants. Inspect
        # literal contents, not only strings next to a query/body property.
        if not re.search(r"\b(query|mutation|fragment|subscription)\b", raw[:120]):
            continue
        if len(result) >= MAX_DOCUMENTS_PER_MODULE:
            break
        try:
            if raw.startswith("`"):
                if "${" in raw:
                    continue
                value = raw[1:-1].replace("\\`", "`")
            elif raw.startswith('"'):
                value = json.loads(raw)
            else:
                value = ast.literal_eval(raw)
            document = parse(value, max_tokens=20_000)
            result.append(print_ast(document))
        except Exception:
            continue
    return result


class FrontendContracts:
    def __init__(self, connection, discovery, allowed_origin):
        self.connection = connection
        self.discovery = discovery
        self.allowed_origin = allowed_origin
        self.pending = {}
        self.ready = []
        self.modules = []
        self.bytes_read = 0
        self.omitted = 0
        self.owned_requests = {}
        self.resource_types = {}
        self.activity = 0
        self.source_slots = 0
        self.declarations = []
        self.published_declarations = {}

    def event(self, method, params):
        from .browser import origin

        identifier = params.get("requestId")
        if method == "Network.requestWillBeSent":
            # Asset origins need not match the application origin. Only inspect
            # responses for scripts actually requested by the owned application.
            try:
                request = params.get("request", {})
                if (
                    params.get("type") in {"Script", "Document"}
                    and origin(params.get("documentURL", "")) == self.allowed_origin
                ):
                    origin(request["url"])
                    self.activity += 1
                    if len(self.owned_requests) < MAX_MODULES * 4:
                        self.owned_requests[identifier] = request["url"]
            except Exception:
                pass
            return
        if method in {"Network.loadingFinished", "Network.loadingFailed"}:
            self.owned_requests.pop(identifier, None)
        if method == "Network.responseReceived":
            response = params.get("response", {})
            try:
                resource_type = params.get("type")
                same_origin = origin(response["url"]) == self.allowed_origin
                owned = self.owned_requests.get(identifier) == response["url"]
                if resource_type not in {"Script", "Document"} or not (
                    same_origin or owned and resource_type == "Script"
                ):
                    return
                if self.source_slots >= MAX_MODULES:
                    self.omitted += 1
                    return
                self.source_slots += 1
                self.pending[identifier] = response["url"]
                self.resource_types[identifier] = resource_type
            except Exception:
                return
        elif method == "Network.loadingFinished" and identifier in self.pending:
            self.ready.append((identifier, self.pending.pop(identifier)))
            self.owned_requests.pop(identifier, None)
        elif method == "Network.loadingFailed":
            self.owned_requests.pop(identifier, None)
            if self.pending.pop(identifier, None) is not None:
                self.omitted += 1

    def drain(self):
        # Do not issue a response-body command inside the network callback:
        # script execution can pause new requests while that command is pending.
        completed, self.ready = self.ready, []
        for identifier, url in completed:
            record = {
                "url": safe_address(url),
                "sourceType": self.resource_types.pop(identifier, "Script"),
                "operations": [],
                "sourceInspected": False,
            }
            self.modules.append(record)
            try:
                if self.bytes_read >= MAX_SOURCE_BYTES:
                    raise ValueError()
                body = self.connection.call(
                    "Network.getResponseBody", {"requestId": identifier}
                )
                source = body.get("body", "")
                if (
                    body.get("base64Encoded")
                    or not isinstance(source, str)
                    or len(source.encode()) > MAX_MODULE_BYTES
                    or self.bytes_read + len(source.encode()) > MAX_SOURCE_BYTES
                ):
                    raise ValueError()
                self.bytes_read += len(source.encode())
                documents = extract_documents(source)
                for text in documents:
                    self.declarations.append((url, parse(text), record))
                record.update(
                    sourceInspected=True,
                    sourceBytes=len(source.encode()),
                    sourceSha256=hashlib.sha256(source.encode()).hexdigest(),
                    uploadSignals=source_upload_signals(source, "https://" + self.allowed_origin[0] + (":" + str(self.allowed_origin[1]) if self.allowed_origin[1] != 443 else "") + "/"),
                    documentAstCandidates=len(
                        re.findall(r'kind\s*:\s*["\']Document["\']', source)
                    ),
                )
            except Exception:
                record["reason"] = "source_unavailable_or_size_limit"
                self.omitted += 1

        self._publish_declarations()

    def _publish_declarations(self):
        """Complete source operations with uniquely declared fragment dependencies.

        A fragment may be emitted in a different loaded module. Local definitions
        win; external definitions must agree byte for byte. Missing, conflicting
        or cyclic dependencies stay in the report and cannot generate a method.
        Recompute exports when more modules arrive, retaining observed requests.
        """
        for digest, urls in self.published_declarations.items():
            locations = self.discovery.document_declarations.get(digest, set())
            locations.difference_update(urls)
            if not locations:
                self.discovery.document_declarations.pop(digest, None)
                if not self.discovery.document_sources.get(digest):
                    self.discovery.documents.pop(digest, None)
        self.published_declarations.clear()
        fragments = {}
        for url, document, record in self.declarations:
            record["operations"] = []
            for node in document.definitions:
                if node.kind == "fragment_definition":
                    fragments.setdefault(node.name.value, {})[print_ast(node)] = (node, url)
        for url, document, record in self.declarations:
            local = {node.name.value: node for node in document.definitions
                     if node.kind == "fragment_definition"}
            for node in document.definitions:
                if node.kind != "operation_definition" or node.name is None:
                    continue
                reached, active, unresolved = {}, set(), set()
                locations = {url}

                def resolve(name):
                    if name in active:
                        unresolved.add(name)
                        return
                    if name in reached:
                        return
                    fragment = local.get(name)
                    if fragment is None:
                        candidates = fragments.get(name, {})
                        if len(candidates) != 1:
                            unresolved.add(name)
                            return
                        fragment, source = next(iter(candidates.values()))
                        locations.add(source)
                    active.add(name)
                    for dependency in fragment_spreads(fragment):
                        resolve(dependency)
                    active.remove(name)
                    reached[name] = fragment

                for name in fragment_spreads(node):
                    resolve(name)
                text = print_ast(DocumentNode(definitions=(node, *reached.values())))
                item = operation({"query": text, "operationName": node.name.value})
                if item is None:
                    continue
                exported = not unresolved and item["document"] is not None
                record["operations"].append({
                    "name": item["name"], "kind": item["kind"],
                    "variables": item["variables"], "source": "frontend_declaration",
                    "executed": False, "endpointVerified": False,
                    "documentExported": exported,
                    "unresolvedFragments": sorted(unresolved),
                })
                if exported:
                    digest = hashlib.sha256(item["document"].encode()).hexdigest()
                    self.discovery.documents[digest] = item["document"]
                    self.discovery.document_declarations.setdefault(digest, set()).update(locations)
                    self.published_declarations.setdefault(digest, set()).update(locations)

        from .upload_inference import discover_workflows
        api_endpoint = self.discovery.declared_graphql_endpoint
        if not api_endpoint and len(self.discovery.graphql_endpoints) == 1:
            api_endpoint = next(iter(self.discovery.graphql_endpoints))
        self.discovery.upload_workflows = discover_workflows(self.modules, api_endpoint)


def fragment_spreads(node):
    """Return referenced fragment names without evaluating website source."""
    class Spreads(Visitor):
        def __init__(self):
            super().__init__()
            self.names = set()

        def enter_fragment_spread(self, item, *_args):
            self.names.add(item.name.value)

    collector = Spreads()
    visit(node, collector)
    return sorted(collector.names)
