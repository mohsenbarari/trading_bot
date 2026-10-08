#!/usr/bin/env python3
"""Pure worker schema budget projection for two-site proposal automation."""
from __future__ import annotations

import copy

try:
    from scripts.two_site_proposal_byte_profile import parse_proposal_utf8_budget_profile
except ImportError:
    from two_site_proposal_byte_profile import parse_proposal_utf8_budget_profile

ERROR = "invalid proposal budget schema"


class BudgetSchemaError(ValueError):
    """Raised when proposal changes schema fails budget projection validation."""


def project_worker_schema_budget(
    changes_schema: object,
    requirements: object,
    allowed_files: object,
) -> dict:
    profile = parse_proposal_utf8_budget_profile(requirements, allowed_files, allowed_files)
    if profile is None:
        return copy.deepcopy(changes_schema)

    if type(changes_schema) is not dict or changes_schema.get("type") != "array":
        raise BudgetSchemaError(ERROR)
    if "maxItems" in changes_schema:
        raise BudgetSchemaError(ERROR)
    items = changes_schema.get("items")
    if type(items) is not dict or "anyOf" not in items:
        raise BudgetSchemaError(ERROR)
    branches = items.get("anyOf")
    if type(branches) is not list or not branches:
        raise BudgetSchemaError(ERROR)

    file_limits = profile["file_limits"]
    for branch in branches:
        if type(branch) is not dict:
            raise BudgetSchemaError(ERROR)
        props = branch.get("properties")
        if type(props) is not dict:
            raise BudgetSchemaError(ERROR)
        path_prop = props.get("path")
        if type(path_prop) is not dict or "enum" not in path_prop:
            raise BudgetSchemaError(ERROR)
        path_enum = path_prop.get("enum")
        if type(path_enum) is not list or len(path_enum) != 1:
            raise BudgetSchemaError(ERROR)
        branch_path = path_enum[0]
        if type(branch_path) is not str or branch_path not in file_limits:
            raise BudgetSchemaError(ERROR)
        content_prop = props.get("content")
        if type(content_prop) is not dict or "type" not in content_prop:
            raise BudgetSchemaError(ERROR)
        content_type = content_prop.get("type")
        if content_type not in ("string", "null"):
            raise BudgetSchemaError(ERROR)
        if "maxLength" in content_prop:
            val = content_prop["maxLength"]
            if type(val) is not int or val < 0:
                raise BudgetSchemaError(ERROR)

    out_schema = copy.deepcopy(changes_schema)
    for branch in out_schema["items"]["anyOf"]:
        content = branch["properties"]["content"]
        if content["type"] == "string":
            branch_path = branch["properties"]["path"]["enum"][0]
            limit = file_limits[branch_path]
            if "maxLength" in content:
                content["maxLength"] = min(limit, content["maxLength"])
            else:
                content["maxLength"] = limit
    return out_schema
