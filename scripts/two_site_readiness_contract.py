#!/usr/bin/env python3
"""Literal definitions for two-site proposal runner readiness contracts."""
from __future__ import annotations

_TASK_BOUNDARY_CODES = frozenset({
    "TASK_FILE_NOT_FOUND",
    "TASK_FILE_HARDLINKED",
    "TASK_FILE_SYMLINK",
    "TASK_FILE_OUTSIDE_ROOT",
    "TASK_FILE_NOT_REGULAR",
    "TASK_BUDGET_EXCEEDED",
    "TASK_CREDENTIAL_DETECTED",
    "TASK_SCHEMA_INVALID",
    "TASK_PATH_OUTSIDE_SCOPE",
    "TASK_ASSIGNMENT_TOKEN_MISSING",
    "ASSIGNMENT_EXPIRED",
    "ASSIGNMENT_CONTRACT_INVALID",
    "TASK_FILE_UNREADABLE",
    "TASK_FILE_CHANGED_DURING_READ",
})
_TASK_BOUNDARY_DESCRIPTIONS = {
    "TASK_FILE_NOT_FOUND": "file not found",
    "TASK_FILE_HARDLINKED": "is hardlinked and cannot be trusted",
    "TASK_FILE_SYMLINK": "is a symlink or path escape",
    "TASK_FILE_OUTSIDE_ROOT": "is outside the project root",
    "TASK_FILE_NOT_REGULAR": "is not a regular file",
    "TASK_BUDGET_EXCEEDED": "exceeds the safe context budget",
    "TASK_FILE_UNREADABLE": "is unreadable",
    "TASK_FILE_CHANGED_DURING_READ": "changed while it was being read",
}
_READINESS_ISSUE_DETAILS = {
    "TASK_FILE_NOT_FOUND": "file not found",
    "TASK_FILE_HARDLINKED": "is hardlinked and cannot be trusted",
    "TASK_FILE_SYMLINK": "is a symlink or path escape",
    "TASK_FILE_OUTSIDE_ROOT": "is outside the project root",
    "TASK_FILE_NOT_REGULAR": "is not a regular file",
    "TASK_BUDGET_EXCEEDED": "exceeds the safe context budget",
    "TASK_CREDENTIAL_DETECTED": "credential-like content detected",
    "TASK_SCHEMA_INVALID": "task schema is invalid",
    "TASK_PATH_OUTSIDE_SCOPE": "task path is outside assignment scope",
    "TASK_ASSIGNMENT_TOKEN_MISSING": "task assignment token is missing",
    "ASSIGNMENT_EXPIRED": "assignment has expired",
    "ASSIGNMENT_CONTRACT_INVALID": "assignment contract is invalid",
    "TASK_FILE_UNREADABLE": "is unreadable",
    "TASK_FILE_CHANGED_DURING_READ": "changed while it was being read",
    "MODEL_CONTEXT_INVALID": "model context is invalid",
    "MODEL_CONTEXT_LIMIT_EXCEEDED": "model context limit exceeded",
    "MODEL_CONTEXT_MISSING": "model context is missing",
}
_MODEL_CONTEXT_CODES = frozenset({
    "MODEL_CONTEXT_INVALID",
    "MODEL_CONTEXT_LIMIT_EXCEEDED",
    "MODEL_CONTEXT_MISSING",
})
