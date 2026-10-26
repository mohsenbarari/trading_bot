# Normal Market role admission

`normal_market_role_admission.py` admits one actual normal role and its already
loaded image. It does not expose stop, create, start, rollback or apply. Its
public result keeps activation, backup/restore, functional progress and the
cross-host activation fence gate false. Raw Config, Env, resolved Compose and
the second `collect()` return value remain only in the trusted process RAM.

The old normal adapter's shared-production-layer and whole-image-Config
predicates do not describe these artifacts. Nine authenticated profile exports
are an 18-layer security parent plus their product layer; the E46 export is a
17-layer rebuilt image plus its OS-security layer. Production has six layers
(eight for F79). The new pure image contract checks the actual catalog, complete
evidence ZIP, export, composition, profile payload and fixed public stack proof.
It compares the candidate daemon Config to the independently authenticated
report, and the old non-Env Config to the old public metadata. Only changed
`PYTHON_VERSION`, `PYTHON_SHA256` and `PYTHONDONTWRITEBYTECODE` image defaults are
admitted; bytecode suppression must be `1`. Any other default change refuses.
All other execution fields remain exact. Build labels must equal their actual
authenticated old and candidate records; changed keys must be exactly the
reviewed nine profile provenance keys or seven E46 provenance keys. Business
labels receive no exception. Values and Config/Env hashes are not included in
the public result.

The original LOAD receipt, original control and actual archive admission are
separate inputs. The local daemon ID comes from that receipt and must be one of
the admission's typed config/manifest/index identities. The candidate's full
RootFS and Config are then measured at that local ID. No Descriptor query is
used. F79 uses `image-baked-rebase` source rows, the exact successful rebase proof,
and the byte-identical reviewed pure rebase libraries. Both public files and
their four ancestors are rechecked through held FDs before the result.

The fresh `normal_market_role_admission/1` control has `operation`, `nonce`,
`site`, `role`, `machine_id`, `issued_at`, `deadline` (at most 300 seconds),
`helpers`, `scope`, `fences` and `evidence`, in addition to `schema`. Helpers use
the exact names and pins in the source. Evidence binds public paths, bytes and
SHA-256 for catalog, profile evidence ZIP, stack proof, original LOAD receipt,
original LOAD control, archive admission and (F79 only) the nested rebase proof.
`rebased_proof` is null for other roles. Source files and evidence stay held
through the final checkpoint.

`scope` records the actual project, cwd, container name, ordered Compose files,
profiles, Env references with exact CLI order/alias metadata, secret metadata,
public sources, historical creation Compose version, current Docker version,
public release, current container lifecycle, and
`image_model: authenticated_ci_rebuild`. F79 has exactly the two baked-rebase
rows and no invented code bind. References are held without reading or hashing
their payloads. Trusted normal Compose resolves configuration only in RAM.
Every service's `--no-env-resolution` references must first be declared. A
historical normal `com.docker.compose.replace` label is accepted only with its
exact normal container name. `pull_policy`, when present, is exactly `never`.
Current CLI and historical creation versions are observed separately.

Root launches from that exact normal cwd, under a fresh finite scope with one
CPU, at most 768 MiB RAM, swap zero, 64 tasks, 300-second runtime, five-second
stop and control-group kill. Keep Go clients free of an address-space limit:

```sh
/usr/bin/python3 -I -B /reviewed/source/normal_market_role_admission.py \
  --control /reviewed/public/control.json \
  --control-sha256 ACTUAL_CONTROL_SHA \
  --source-sha256 ACTUAL_ADMISSION_SOURCE_SHA
```

Root must produce the fresh public control from actual normal metadata and
original evidence. This command grants no later apply authority. A normal
serving coordinator still needs live two-host held fences before constructing
the backend, the source-admitted `verify_gate`, `owners`, `functional` and
`fences` callbacks, actual fresh backup/restore compatibility, owner-lock and
strict normal readiness/progress evidence, and durable two-host closure.
The strict capture promotion healthcheck remains distinct from Docker capture
liveness; unresolved replay/identity incidents remain open. A later executable
serving entry must use a new explicit schema and the admitted rebuild model,
preserving all business Env, Host, mounts and normal command values.
