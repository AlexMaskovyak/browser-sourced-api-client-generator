"""Local HTTPS message backend with password and verification-code sign-in."""

from urllib.parse import parse_qs, urlsplit

from fixture_site import SITE_PAGES, Site, SiteRequestHandler


class MessageRequestHandler(SiteRequestHandler):
    @property
    def authenticated(self):
        return self.site.sessions_active and super().authenticated

    def do_POST(self):
        if self.path in {"/login", "/mfa"}:
            super().do_POST()
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_GET(self):
        target = urlsplit(self.path)
        if target.path == "/message":
            message_id = parse_qs(target.query).get("message_id", [None])[0]
            self.serve_message(message_id)
        elif target.path == "/app":
            self.serve_dashboard()
        elif target.path in {"/login", "/mfa"}:
            self.send_html((SITE_PAGES / f"{target.path[1:]}.html").read_text())
        elif target.path == "/assets/messages.js":
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.end_headers()
            self.wfile.write((SITE_PAGES / "messages.js").read_bytes())
        elif target.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_json({"error": "Not found"}, 404)

    def serve_dashboard(self):
        if self.authenticated:
            self.site.counts["app"] += 1
            self.send_html((SITE_PAGES / "dashboard.html").read_text())
        else:
            self.redirect("/login")

    def serve_message(self, message_id):
        if not self.authenticated:
            status, body = 401, {"error": "Sign-in required"}
        elif message_id not in self.site.messages:
            status, body = 404, {"error": "Unknown message"}
        else:
            status, body = 200, dict(self.site.messages[message_id])

        self.site.message_requests.append({
            "method": "GET",
            "path": self.path,
            "authenticated": self.authenticated,
            "status": status,
            "body": body.copy(),
        })
        self.send_json(body, status)


class MessageSite(Site):
    """Provide fresh message records and revocable sessions for each test."""

    def __init__(self, root):
        self.messages = {
            "m1": {"id": "m1", "subject": "First fixture message"},
            "m2": {"id": "m2", "subject": "Second fixture message"},
        }
        self.sessions_active = True
        self.message_requests = []
        super().__init__(root, handler=MessageRequestHandler)
