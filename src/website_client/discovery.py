"""Turn browser observations or HAR entries into bounded, sanitized definitions."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, quote, urlencode, unquote, urlsplit, urlunsplit

from graphql import parse, print_ast
from graphql.language.ast import DocumentNode
from graphql.language.visitor import Visitor, visit
import yaml

from .browser import BrowserError, origin
from .definitions import graphql_schema, openapi_document

MAX_BODY = 1024 * 1024
MAX_HAR = 32 * 1024 * 1024
AUTH = re.compile(r"(?:^|[/_.-])(login|log_in|sign_in|signin|sign-in|oauth|token|password|credential|authorize|authentication|session)(?:$|[/_.-])", re.I)
AUTH_OPERATION = re.compile(r"auth|token|password|credential|session|log.?in|sign.?in", re.I)
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)


def route(url):
    origin(url)
    parsed = urlsplit(url)
    if AUTH.search(parsed.path): raise ValueError()
    segments, parameters = [], []
    for index, raw in enumerate(parsed.path.split("/")):
        segment = unquote(raw)
        if re.fullmatch(r"\{id_\d+\}", segment):
            segments.append(segment)
            parameters.append(segment[1:-1])
        elif segment.isdigit() or UUID.match(segment) or len(segment) >= 32 or "@" in segment:
            name = f"id_{index}"
            segments.append("{" + name + "}")
            parameters.append(name)
        else:
            segments.append(quote(segment, safe="-._~"))
    base = f"https://{parsed.netloc.lower()}"
    return base, "/".join(segments) or "/", parameters


def safe_query(url):
    return sorted({key for key, _value in parse_qsl(urlsplit(url).query) if FIELD.fullmatch(key) and not AUTH.search(key)})


def mime(value):
    result = str(value or "").split(";", 1)[0].strip().lower()
    return result if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", result) else "application/octet-stream"


def shape(value, depth=0):
    if depth > 16: return {}
    if value is None: return {"type": "null"}
    if isinstance(value, bool): return {"type": "boolean"}
    if isinstance(value, int): return {"type": "integer"}
    if isinstance(value, float): return {"type": "number"}
    if isinstance(value, str): return {"type": "string"}
    if isinstance(value, list):
        types = {json.dumps(shape(item, depth + 1), sort_keys=True) for item in value[:20]}
        items = {} if not types else json.loads(next(iter(types))) if len(types) == 1 else {"anyOf": [json.loads(item) for item in sorted(types)]}
        return {"type": "array", "items": items}
    if isinstance(value, dict):
        return {"type": "object", "properties": {key: shape(item, depth + 1) for key, item in list(value.items())[:100] if FIELD.fullmatch(key)}}
    return {}


def redact(value, depth=0):
    if depth > 16: return None
    if isinstance(value, dict): return {key: redact(item, depth + 1) for key, item in list(value.items())[:100] if FIELD.fullmatch(key)}
    if isinstance(value, list): return [redact(item, depth + 1) for item in value[:20]]
    if isinstance(value, bool): return False
    if isinstance(value, int): return 0
    if isinstance(value, float): return 0.0
    return None if value is None else "[REDACTED]"


def json_body(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BODY: return None
    try: return json.loads(raw)
    except (ValueError, RecursionError): return None


class LiteralCheck(Visitor):
    found = False

    def enter(self, node, *_args):
        if node.kind in {"string_value", "int_value", "float_value", "enum_value", "boolean_value"}:
            self.found = True


def operation(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("query"), str): return None
    text = payload["query"]
    if len(text.encode()) > MAX_BODY: return None
    try:
        document = parse(text, max_tokens=20000)
        operations = [item for item in document.definitions if item.kind == "operation_definition"]
        if any(item.name is None for item in operations): return None
        name = payload.get("operationName")
        selected = next((item for item in operations if item.name and item.name.value == name), None) if name else operations[0] if len(operations) == 1 else None
        if selected is None or selected.name is None or AUTH_OPERATION.search(selected.name.value): return None
        checker = LiteralCheck()
        visit(document, checker)
        return {"name": selected.name.value, "kind": selected.operation.value,
                "document": None if checker.found else print_ast(document),
                "variables": {item.variable.name.value: print_ast(item.type) for item in selected.variable_definitions}}
    except Exception: return None


class Discovery:
    def __init__(self, max_entries=1000):
        if not isinstance(max_entries, int) or not 1 <= max_entries <= 10000:
            raise BrowserError("--max-entries must be between 1 and 10000.")
        self.max_entries = max_entries
        self.entries = []
        self.endpoints = {}
        self.documents = {}
        self.definitions = {}
        self.omitted = 0
        self.failures = 0
        self.capture_failures = 0
        self.seen = 0

    def add(self, entry):
        self.seen += 1
        if len(self.entries) >= self.max_entries:
            self.omitted += 1
            return
        try:
            request, response = entry["request"], entry.get("response", {})
            base, path, path_params = route(request["url"])
            method = request["method"].upper()
            if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}: raise ValueError()
            query_names = safe_query(request["url"])
            post = request.get("postData", {})
            payload = json_body(post.get("text", ""))
            if method == "GET" and payload is None:
                parts = dict(parse_qsl(urlsplit(request["url"]).query))
                if "query" in parts: payload = parts
            graphql = operation(payload)
            graphql_unknown = not graphql and (isinstance(payload, dict) and "query" in payload
                or isinstance(payload, list) and any(isinstance(item, dict) and "query" in item for item in payload))
            content = response.get("content", {})
            raw = content.get("text", "")
            if content.get("encoding") == "base64" and isinstance(raw, str) and len(raw) <= MAX_BODY:
                try: raw = base64.b64decode(raw, validate=True).decode()
                except Exception: raw = ""
            body = json_body(raw)
            status = int(response.get("status", 0))
            if not 0 <= status <= 599: raise ValueError()
            response_mime = mime(content.get("mimeType"))
            if body is None and "yaml" in response_mime and isinstance(raw, str) and len(raw.encode()) <= MAX_BODY:
                try: body = yaml.safe_load(raw)
                except Exception: pass
            key = (base, path, method, graphql["name"] if graphql else "")
            endpoint = self.endpoints.setdefault(key, {"origin": base, "path": path, "method": method,
                "queryParameters": [], "pathParameters": path_params, "responses": {}, "observations": 0,
                "graphql": graphql, "graphqlUnmodeled": bool(graphql_unknown), "liveVerified": False})
            if post.get("text") and payload is None: endpoint["requestBodyUnmodeled"] = True
            endpoint["observations"] += 1
            endpoint["queryParameters"] = sorted(set(endpoint["queryParameters"]) | set(query_names))
            if payload is not None and not graphql: endpoint["requestSchema"] = shape(payload)
            if status:
                endpoint["responses"][str(status)] = {"description": "Observed response; inferred from samples.", "content": {response_mime: {"schema": shape(body)}}}
            if graphql and graphql["document"]:
                digest = hashlib.sha256(graphql["document"].encode()).hexdigest()
                self.documents[digest] = graphql["document"]
            safe_url = base + quote(path, safe="/%-._~")
            if query_names: safe_url += "?" + urlencode([(key, "[REDACTED]") for key in query_names])
            request_version = str(request.get("httpVersion", ""))[:16]
            response_version = str(response.get("httpVersion", ""))[:16]
            versions = {"HTTP/1.0", "HTTP/1.1", "HTTP/2.0", "HTTP/3.0"}
            safe_request = {"method": method, "url": safe_url, "httpVersion": request_version if request_version in versions else "", "headers": [], "cookies": [],
                            "queryString": [{"name": key, "value": "[REDACTED]"} for key in query_names], "headersSize": -1, "bodySize": -1}
            if payload is not None:
                safe_payload = {"operationName": graphql["name"], "query": graphql["document"], "variables": {}} if graphql else redact(payload)
                safe_request["postData"] = {"mimeType": "application/json", "text": json.dumps(safe_payload)}
            elif post.get("text"):
                safe_request["_bodyOmitted"] = True
            safe_response = {"status": status, "statusText": "", "httpVersion": response_version if response_version in versions else "", "headers": [], "cookies": [],
                             "redirectURL": "", "headersSize": -1, "bodySize": -1,
                             "content": {"size": 0, "mimeType": response_mime}}
            if body is not None: safe_response["content"]["text"] = json.dumps(redact(body))
            safe_response["content"]["size"] = len(safe_response["content"].get("text", "").encode())
            duration = max(0, min(float(entry.get("time", 0)), 3600000))
            try: started = datetime.fromisoformat(entry["startedDateTime"]).isoformat()
            except Exception: started = datetime.now(timezone.utc).isoformat()
            self.entries.append({"startedDateTime": started, "time": duration,
                "request": safe_request, "response": safe_response, "cache": {},
                "timings": {"send": 0, "wait": duration, "receive": 0}, "_timingsEstimated": True, "_sanitized": True})
            if 200 <= status < 300 and isinstance(body, dict): self._definition(body)
            elif 200 <= status < 300 and body is None and isinstance(raw, str) and len(raw.encode()) <= MAX_BODY and ("graphql" in response_mime or urlsplit(request["url"]).path.endswith((".graphql", ".gql"))):
                try:
                    text = graphql_schema(raw)
                    self.definitions[("graphql-schema", hashlib.sha256(text.encode()).hexdigest())] = text
                except BrowserError: self.failures += 1
        except Exception:
            self.omitted += 1

    def _definition(self, body):
        try:
            if "openapi" in body:
                value = openapi_document(body)
                kind, text = "openapi", json.dumps(value, indent=2) + "\n"
            elif "__schema" in body or isinstance(body.get("data"), dict) and "__schema" in body["data"]:
                kind, text = "graphql-schema", graphql_schema(body)
            else: return
            digest = hashlib.sha256(text.encode()).hexdigest()
            self.definitions[(kind, digest)] = text
        except BrowserError:
            self.failures += 1

    def files(self, source):
        endpoints = list(self.endpoints.values())
        groups = {}
        for endpoint in endpoints:
            if endpoint["graphql"] or endpoint.get("graphqlUnmodeled"): continue
            spec = groups.setdefault(endpoint["origin"], {"openapi": "3.1.0", "info": {"title": "Observed API", "version": "0.0.0"},
                "servers": [{"url": endpoint["origin"]}], "paths": {}, "x-discovery-inferred": True,
                "x-discovery-limitations": "Observed traffic only. Shapes are inferred, required fields and completeness are unknown."})
            digest = hashlib.sha256((endpoint["method"] + endpoint["path"]).encode()).hexdigest()[:12]
            parameters = [{"name": key, "in": "path", "required": True, "schema": {"type": "string"}} for key in endpoint["pathParameters"]]
            parameters += [{"name": key, "in": "query", "required": False, "schema": {"type": "string"}} for key in endpoint["queryParameters"]]
            item = {"operationId": endpoint["method"].lower() + "_" + digest, "parameters": parameters,
                    "responses": endpoint["responses"] or {"default": {"description": "No completed response observed."}}, "x-discovery-inferred": True}
            if endpoint.get("requestBodyUnmodeled"): item["x-discovery-request-body-unmodeled"] = True
            if "requestSchema" in endpoint: item["requestBody"] = {"required": False, "content": {"application/json": {"schema": endpoint["requestSchema"]}}}
            spec["paths"].setdefault(endpoint["path"], {})[endpoint["method"].lower()] = item
        files = {"capture.har": json.dumps({"log": {"version": "1.2", "creator": {"name": "browser-sourced-api-client-generator", "version": "0.1.0"}, "entries": self.entries, "_sanitized": True}}, indent=2) + "\n"}
        for index, spec in enumerate(groups.values(), 1):
            openapi_document(spec)
            files[f"openapi-inferred-{index:04}.json"] = json.dumps(spec, indent=2) + "\n"
        for index, ((kind, _digest), text) in enumerate(self.definitions.items(), 1):
            files[f"{kind}-downloaded-{index:04}." + ("json" if kind == "openapi" else "graphql")] = text
        conflicts = set()
        combined = {}
        if self.documents:
            # A document may contain multiple operations and shared fragments.
            # Preserve distinct documents separately to avoid duplicate definitions.
            for index, text in enumerate(self.documents.values(), 1):
                files[f"operations-{index:04}.graphql"] = text + "\n"
                for definition in parse(text).definitions:
                    key = (definition.kind, definition.name.value)
                    if key in combined and print_ast(combined[key]) != print_ast(definition): conflicts.add(definition.name.value)
                    combined[key] = definition
            if not conflicts: files["operations.graphql"] = print_ast(DocumentNode(definitions=tuple(combined.values()))) + "\n"
        files["discovery.json"] = json.dumps({"source": source, "observations": self.seen, "harEntries": len(self.entries),
            "omitted": self.omitted, "definitionFailures": self.failures, "bounded": self.omitted > 0,
            "captureFailures": self.capture_failures,
            "graphqlDefinitionConflicts": sorted(conflicts),
            "endpoints": endpoints, "definitionFiles": sorted(name for name in files if name != "capture.har"),
            "liveVerified": False, "completeServiceCoverage": False,
            "limitations": ["Only observed traffic is represented.", "HAR values and headers are redacted.", "GraphQL operations are not a complete schema.", "OpenAPI marked inferred requires review before use."]}, indent=2) + "\n"
        return files


def import_har(path, max_entries=1000):
    try:
        path = Path(path)
        if path.stat().st_size > MAX_HAR: raise ValueError()
        document = json.loads(path.read_text())
        entries = document["log"]["entries"]
        if not isinstance(entries, list): raise ValueError()
    except Exception:
        raise BrowserError("Supply a valid HAR file no larger than 32 MiB.") from None
    discovery = Discovery(max_entries)
    for entry in entries: discovery.add(entry)
    return discovery


def new_output(path):
    path = Path(path).expanduser().absolute()
    if os.path.lexists(path): raise BrowserError("Output already exists. Choose a new directory.")
    if not path.parent.is_dir(): raise BrowserError("The output parent directory must already exist.")
    return path


def publish(path, files):
    path = new_output(path)
    path.mkdir(mode=0o700)
    try:
        for name, text in files.items():
            if Path(name).name != name: raise BrowserError("Invalid output filename.")
            descriptor = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream: stream.write(text)
    except Exception:
        raise BrowserError("Output could not be completed. Inspect the new directory before retrying; existing output was not overwritten.") from None
