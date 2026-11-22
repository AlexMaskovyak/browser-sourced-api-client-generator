"""Small standard API definitions for independently generated client tests."""


def read_definition(endpoint="https://neutral.invalid"):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Contract", "version": "1"},
        "servers": [{"url": endpoint}],
        "paths": {
            "/read": {
                "get": {
                    "operationId": "read",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["value"],
                                        "properties": {"value": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
