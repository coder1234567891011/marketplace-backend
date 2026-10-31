"""
The OpenAPI schema, with what FastAPI can't infer on its own:
  * authentication: routes read the Authorization header themselves
    (auth/auth.py), so the schema would show it as a plain header. Instead
    it's declared as a bearer scheme, required or optional per route;
  * errors: every error has the shape {"error": {"code", "message", "details"?}}
    (utils/errors.py), including 422s, which FastAPI would describe as its own
    validation format.
Served at /openapi.json and /docs, and exported by scripts/export_api.py.
"""
from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from auth.auth import get_current_user, get_optional_user

ERROR_SCHEMA = {
    "type": "object",
    "required": ["error"],
    "properties": {"error": {
        "type": "object",
        "required": ["code", "message"],
        "properties": {
            "code": {"type": "string", "description": "Stable machine-readable code, e.g. not_found, "
                                                       "validation_error, account_suspended, rate_limited"},
            "message": {"type": "string", "description": "Human-readable explanation"},
            "details": {"description": "Extra information; for validation_error, a list of {loc, msg}"},
        },
    }},
}


def _auth(dependant: Dependant) -> str | None:
    """'required' or 'optional' when the route reads the caller's token."""
    calls = set()
    stack = [dependant]
    while stack:
        current = stack.pop()
        calls.add(current.call)
        stack.extend(current.dependencies)
    if get_current_user in calls:
        return "required"
    if get_optional_user in calls:
        return "optional"
    return None


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "A Supabase access token for a user with a confirmed email.",
    }
    schemas = components.setdefault("schemas", {})
    schemas["Error"] = ERROR_SCHEMA
    for unused in ("HTTPValidationError", "ValidationError"):
        schemas.pop(unused, None)
    error_ref = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}

    # iter_route_contexts sees through included routers, with their prefixes applied.
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        auth = _auth(route.dependant)
        path = schema["paths"][route.path_format]
        for method in route.methods or ():
            operation = path.get(method.lower())
            if operation is None:
                continue
            operation["parameters"] = [p for p in operation.get("parameters", [])
                                       if not (p["in"] == "header" and p["name"].lower() == "authorization")]
            if not operation["parameters"]:
                del operation["parameters"]
            if auth == "required":
                operation["security"] = [{"bearerAuth": []}]
            elif auth == "optional":
                operation["security"] = [{"bearerAuth": []}, {}]
            responses = operation["responses"]
            if "422" in responses:
                if "parameters" in operation or "requestBody" in operation:
                    responses["422"] = {"description": "Validation error", **deepcopy(error_ref)}
                else:
                    del responses["422"]   # its only input was the Authorization header
            responses["4XX"] = {"description": "Error (see Error.code)", **deepcopy(error_ref)}
    app.openapi_schema = schema
    return schema
