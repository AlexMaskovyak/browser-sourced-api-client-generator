"""Inventory declared writes without submitting forms or executing source."""

import ast
import hashlib
import json
import re
from urllib.parse import urljoin

from graphql import parse, print_ast

from .discovery import FIELD, MAX_BODY, operation
from .exploration_policy import safe_address

MAX_FORMS = 30
MAX_FIELDS = 100
MAX_MODULES = 60
MAX_MODULE_BYTES = 4 * MAX_BODY
MAX_SOURCE_BYTES = 32 * MAX_BODY
MAX_DOCUMENTS_PER_MODULE = 100
# Only literal source strings are decoded. Interpolated templates and executable
# expressions are not evaluated or guessed.
LITERAL = r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)'
GRAPHQL_LITERAL = re.compile(
    r"(?:\b(?:body|query)\s*:\s*|\b(?:gql|graphql)\s*)" + LITERAL, re.S
)


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
    for match in list(GRAPHQL_LITERAL.finditer(source))[:MAX_DOCUMENTS_PER_MODULE]:
        raw = match.group(1)
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
                names = []
                for text in documents:
                    for definition in parse(text).definitions:
                        if (
                            definition.kind != "operation_definition"
                            or definition.name is None
                        ):
                            continue
                        item = operation(
                            {"query": text, "operationName": definition.name.value}
                        )
                        if item:
                            names.append(
                                {
                                    "name": item["name"],
                                    "kind": item["kind"],
                                    "variables": item["variables"],
                                    "source": "frontend_declaration",
                                    "executed": False,
                                    "endpointVerified": False,
                                    "documentExported": item["document"] is not None,
                                }
                            )
                            if item["document"]:
                                self.discovery.documents[
                                    hashlib.sha256(
                                        item["document"].encode()
                                    ).hexdigest()
                                ] = item["document"]
                                digest = hashlib.sha256(
                                    item["document"].encode()
                                ).hexdigest()
                                self.discovery.document_declarations.setdefault(
                                    digest, set()
                                ).add(url)
                record.update(
                    operations=names,
                    sourceInspected=True,
                    sourceBytes=len(source.encode()),
                    documentAstCandidates=len(
                        re.findall(r'kind\s*:\s*["\']Document["\']', source)
                    ),
                )
            except Exception:
                record["reason"] = "source_unavailable_or_size_limit"
                self.omitted += 1
