"""
Export the API description: api-spec.yaml (OpenAPI 3.1) and
postman_collection.json (Postman collection v2.1), both at the repo root.

Run from src/ after changing endpoints (tests/test_api_spec.py fails while
api-spec.yaml is out of date):
    ../.venv/bin/python -m scripts.export_api

Both come from the app's own schema (utils/openapi.py), so they match /docs.
The Postman collection has a "Sign in" request that stores a Supabase access
token in the {{token}} variable, which every other request sends.
"""
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "api-spec.yaml"
POSTMAN_PATH = ROOT / "postman_collection.json"
LOCAL_URL = "http://localhost:8000"
SUPABASE_URL = "https://cjknmfzthsuhgfpocxef.supabase.co"
METHODS = ("get", "post", "put", "patch", "delete")
EXAMPLE_STRINGS = {
    "uuid": "00000000-0000-0000-0000-000000000000", "date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01",
    "email": "user@example.com", "uri": "https://example.com",
}
# Realistic values for fields a bare "string" wouldn't pass (patterns) or explain.
EXAMPLE_FIELDS: dict[str, Any] = {
    "language": "en", "lang": "en", "currency": "usd", "country": "US", "colors": "UR", "set": "m21",
    "q": "Lightning Bolt", "name_text": "Lightning Bolt", "collector_text": "0150 R\nM21 • EN",
    "text": "4 Lightning Bolt\n1 Lightning Helix (RAV) 213", "username": "player1", "name": "My Collection",
    "postal_code": "12345", "line1": "1 Main St", "city": "Springfield", "recipient_name": "Jo Buyer",
    "tracking_number": "1Z999AA10123456784", "content_type": "image/jpeg",
}


def build_spec() -> dict[str, Any]:
    from app import app

    spec = dict(app.openapi())
    spec["servers"] = [{"url": LOCAL_URL, "description": "Local (uvicorn)"}]
    return spec


def spec_yaml(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120)


# ----------------------------------------------------------------
# Example values from JSON Schema
# ----------------------------------------------------------------
def example(schema: dict[str, Any], spec: dict[str, Any], depth: int = 0, field: str | None = None) -> Any:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return example(spec["components"]["schemas"][name], spec, depth, field)
    if depth > 6:
        return None
    for key in ("example", "const"):
        if key in schema:
            return schema[key]
    if schema.get("default") is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if field in EXAMPLE_FIELDS and "properties" not in schema:
        return EXAMPLE_FIELDS[field]
    for key in ("anyOf", "oneOf", "allOf"):
        options = [o for o in schema.get(key, []) if o.get("type") != "null"]
        if options:
            return example(options[0], spec, depth, field)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        # Required fields and those with a default; all of them when that leaves nothing (e.g. updates).
        shown = [k for k, v in props.items() if k in required or v.get("default") is not None] or list(props)
        body = {k: example(props[k], spec, depth + 1, k) for k in shown}
        extra = schema.get("additionalProperties")
        if not body and isinstance(extra, dict):
            body = {"key": example(extra, spec, depth + 1)}
        return body
    if kind == "array":
        return [example(schema.get("items", {}), spec, depth + 1)]
    if kind == "string":
        return EXAMPLE_STRINGS.get(schema.get("format", ""), "string")
    if kind in ("integer", "number"):
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0) + 1)
        return int(low) if kind == "integer" else low
    if kind == "boolean":
        return False
    return None


# ----------------------------------------------------------------
# Postman
# ----------------------------------------------------------------
def _request(path: str, method: str, operation: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    params = operation.get("parameters", [])
    path_vars = [p for p in params if p["in"] == "path"]
    query = [p for p in params if p["in"] == "query"]
    postman_path = path.lstrip("/").replace("{", ":").replace("}", "")
    raw = "{{baseUrl}}/" + postman_path
    query_items = [{
        "key": p["name"], "value": str(example(p.get("schema", {}), spec, field=p["name"])),
        "description": p.get("description", ""), "disabled": not p.get("required", False),
    } for p in query]
    enabled = [q for q in query_items if not q["disabled"]]
    if enabled:
        raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in enabled)

    request: dict[str, Any] = {
        "method": method.upper(),
        "header": [],
        "url": {
            "raw": raw, "host": ["{{baseUrl}}"], "path": postman_path.split("/"),
            "query": query_items,
            "variable": [{"key": p["name"], "value": "", "description": p.get("description", p["name"])}
                         for p in path_vars],
        },
        "description": operation.get("description", ""),
    }
    if "security" not in operation:
        request["auth"] = {"type": "noauth"}
    body = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if body:
        request["header"].append({"key": "Content-Type", "value": "application/json"})
        raw_body = json.dumps(example(body["schema"], spec), indent=2, ensure_ascii=False)
        request["body"] = {"mode": "raw", "raw": raw_body, "options": {"raw": {"language": "json"}}}
    return {"name": operation.get("summary", f"{method.upper()} {path}"), "request": request}


SIGN_IN = {
    "name": "Sign in (Supabase)",
    "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": [
        "const body = pm.response.json();",
        "if (body.access_token) { pm.collectionVariables.set('token', body.access_token); }",
    ]}}],
    "request": {
        "method": "POST",
        "auth": {"type": "noauth"},
        "header": [{"key": "apikey", "value": "{{supabaseAnonKey}}"},
                   {"key": "Content-Type", "value": "application/json"}],
        "url": {"raw": "{{supabaseUrl}}/auth/v1/token?grant_type=password", "host": ["{{supabaseUrl}}"],
                "path": ["auth", "v1", "token"], "query": [{"key": "grant_type", "value": "password"}]},
        "body": {"mode": "raw", "raw": json.dumps({"email": "{{email}}", "password": "{{password}}"}, indent=2),
                 "options": {"raw": {"language": "json"}}},
        "description": "Signs in with email and password and stores the access token in {{token}}. Set "
                       "supabaseAnonKey, email and password in the collection variables first (use a test "
                       "account; tokens expire after an hour).",
    },
}


def build_postman(spec: dict[str, Any]) -> dict[str, Any]:
    folders: dict[str, list[dict[str, Any]]] = {}
    for path, operations in spec["paths"].items():
        for method in METHODS:
            if method in operations:
                operation = operations[method]
                tag = (operation.get("tags") or ["other"])[0]
                folders.setdefault(tag, []).append(_request(path, method, operation, spec))
    return {
        "info": {
            "name": spec["info"]["title"],
            "description": spec["info"].get("description", "") + "\n\nGenerated by src/scripts/export_api.py.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
        "variable": [
            {"key": "baseUrl", "value": LOCAL_URL},
            {"key": "token", "value": ""},
            {"key": "supabaseUrl", "value": SUPABASE_URL},
            {"key": "supabaseAnonKey", "value": ""},
            {"key": "email", "value": ""},
            {"key": "password", "value": ""},
        ],
        "item": [{"name": "auth", "item": [SIGN_IN]},
                 *({"name": tag, "item": items} for tag, items in folders.items())],
    }


def main() -> None:
    spec = build_spec()
    SPEC_PATH.write_text(spec_yaml(spec))
    POSTMAN_PATH.write_text(json.dumps(build_postman(spec), indent=2) + "\n")
    count = sum(1 for ops in spec["paths"].values() for m in METHODS if m in ops)
    print(f"Wrote {SPEC_PATH.name} and {POSTMAN_PATH.name} ({count} operations)")


if __name__ == "__main__":
    main()
