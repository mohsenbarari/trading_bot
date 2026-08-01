#!/bin/bash
# This one-off edits a migration through the superseded generic root-Compose
# app. It is never a valid schema path for the three-site architecture: the
# isolated runtime has schema bootstrap disabled and accepts only its reviewed
# release. Keep the unconditional stop before Docker Compose is invoked.
echo "ERROR: generic root-Compose migration is retired for the three-site architecture; use the reviewed isolated schema control plane." >&2
exit 2
