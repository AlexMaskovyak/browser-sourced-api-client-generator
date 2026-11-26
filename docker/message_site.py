"""Disposable message application, with real login and no API specification."""

from urllib.parse import parse_qs, urlsplit

from fixture_site import LOGIN_FORM, VERIFICATION_FORM, Site, SiteRequestHandler


DASHBOARD_HTML = """<!doctype html>
<h1>Messages</h1><p id="subject">Loading</p>
<script>
fetch('/message?message_id=m1').then(response => {
  if (!response.ok) throw new Error('Message read failed');
  return response.json();
}).then(message => {
  document.querySelector('#subject').textContent = message.subject;
}).catch(() => { document.querySelector('#subject').textContent = 'Read failed'; });
</script>
"""


class MessageRequestHandler(SiteRequestHandler):
    def do_POST(self):
        if self.path in {"/login", "/mfa"}:
            super().do_POST()
        else:
            self.send_json({"error": "Not found"}, 404)

    @property
    def authenticated(self):
        return self.site.sessions_active and super().authenticated

    def do_GET(self):
        target = urlsplit(self.path)
        if target.path == "/message":
            message_id = parse_qs(target.query).get("message_id", [None])[0]
            if not self.authenticated:
                status, body = 401, {"error": "Sign-in required"}
            elif message_id not in self.site.messages:
                status, body = 404, {"error": "Unknown message"}
            else:
                status, body = 200, dict(self.site.messages[message_id])
            self.site.message_requests.append({
                "method": "GET", "path": self.path,
                "authenticated": self.authenticated,
                "status": status, "body": dict(body),
            })
            self.send_json(body, status)
        elif target.path == "/login":
            self.send_html(LOGIN_FORM)
        elif target.path == "/mfa":
            self.send_html(VERIFICATION_FORM)
        elif target.path == "/app":
            if self.authenticated:
                self.site.counts["app"] += 1
                self.send_html(DASHBOARD_HTML)
            else:
                self.redirect("/login")
        elif target.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            # Never delegate to the generic fixture: it exposes valid schemas
            # and returns an HTML dashboard for unknown authenticated paths.
            self.send_json({"error": "Not found"}, 404)


class MessageSite(Site):
    """Own independent records and revocable authentication for future scenarios."""

    def __init__(self, root):
        self.messages = {
            "m1": {"id": "m1", "subject": "First fixture message"},
            "m2": {"id": "m2", "subject": "Second fixture message"},
        }
        self.sessions_active = True
        self.message_requests = []
        super().__init__(root, handler=MessageRequestHandler)
