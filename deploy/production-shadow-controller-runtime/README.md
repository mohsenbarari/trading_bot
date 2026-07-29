# Controller Runtime Closure Status

This directory defines a controller-only, offline Python dependency contract
for the production-shadow convergence source-set producer.  It is not an
operational deployment artifact yet.

The current builder and verifier are synthetic test components only.  Their
public CLIs and build/materialization APIs fail closed for production build or
launch until a separate checkpoint provides all of the following:

* a held-file-descriptor bootstrap that proves the detached release commit,
  tree, and required Git blobs before importing controller code;
* the missing release-bound source-set runtime bootstrap; and
* a real-release integration test of that bootstrap.

The wheel-input receipt digest must come from the fixed root-only campaign
plan at `/etc/trading-bot-three-site/campaigns/<campaign-id>/controller-runtime-closure-plan.json`.
It is never accepted as a caller-supplied command-line digest.  No plan,
wheel, runtime closure, or Object Storage artifact is created by this
checkpoint.
