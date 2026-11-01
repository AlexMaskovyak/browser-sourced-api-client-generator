"""Target website fixtures for real algorithmic browser exploration.

Scripts below are response bodies of the website under test, not generator code.
They exercise normal UI state changes and attempted writes in a real browser.
"""

import json

CREATE_DOCUMENT = (
    "mutation CreateItem($name: String!) { createItem(name: $name) { id name } }"
)
READ_DOCUMENT = "query GetItem($id: ID!) { item(id: $id) { id name } }"


def response(path):
    if path == "/create-expense.js":
        return "application/javascript", "const contract={body:" + json.dumps(
            CREATE_DOCUMENT
        ) + "};"
    if path == "/reports-runtime.js":
        # Both writes must be stopped before reaching the fixture server.
        return "application/javascript", (
            "fetch('/graphql',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify("
            + json.dumps(
                {
                    "operationName": "CreateItem",
                    "query": CREATE_DOCUMENT,
                    "variables": {"name": "Created"},
                }
            )
            + ")}).catch(()=>{});"
            "fetch('/items',{method:'POST',headers:{'content-type':'application/json'},body:'{\"name\":\"Created\"}'}).catch(()=>{});"
            "const socket=new WebSocket('wss://'+location.host+'/live');socket.onerror=()=>{};"
            "new Worker('/worker.js');"
            "fetch('/graphql',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify("
            + json.dumps(
                {
                    "operationName": "GetItem",
                    "query": READ_DOCUMENT,
                    "variables": {"id": "7"},
                }
            )
            + ")}).catch(()=>{});"
        )
    if path == "/worker.js":
        return (
            "application/javascript",
            "fetch('/items',{method:'POST',headers:{'content-type':'application/json'},body:'{\"name\":\"Created\"}'}).catch(()=>{});",
        )
    if path == "/app":
        return "text/html", (
            "<h1>Workspace</h1><details><summary>Reports</summary>"
            '<a href="/reports">Report history</a></details>'
            '<a href="/records?page=1">Records</a><a href="/logout">Sign out</a>'
            '<a href="/outside">Other site</a><script src="/create-expense.js"></script>'
        )
    if path == "/reports":
        return "text/html", (
            '<h1>Reports</h1><button type="button" aria-haspopup="dialog" '
            "onclick=\"document.getElementById('expense-form').hidden=false\">New expense</button>"
            '<form id="expense-form" hidden method="post" action="/items" enctype="multipart/form-data">'
            '<input name="name" required maxlength="80" value="do-not-export-form-value">'
            '<input name="csrf" type="hidden" value="do-not-export-hidden-token">'
            '<input name="receipt" type="file" accept="application/pdf" required>'
            '<button type="submit" aria-haspopup="menu">Create item</button></form>'
            '<a role="tab" href="/reports/details">Report details</a>'
            '<script src="/reports-runtime.js"></script>'
        )
    if path == "/reports/details":
        return (
            "text/html",
            '<h1>Report details</h1><iframe hidden src="/items/7"></iframe>',
        )
    if path == "/records?page=1":
        return "text/html", '<h1>Records</h1><a href="/records?page=2">Next</a>'
    if path == "/records?page=2":
        return "text/html", (
            '<h1>More records</h1><button type="button" '
            "onclick=\"this.disabled=true;fetch('/items/7')\">Load more</button>"
        )
    return None
