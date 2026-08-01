# Phase-5 reverse control-mailbox deployment gap

## Status

This is a source-grounded design/audit note.  It does not create an Object
Storage bucket, credential, IAM policy, provider request, service, or Phase-5
adapter.  No Full Matrix phase has run.

The reverse recovery data plane and the reverse control-mailbox plane are
different trust domains.  Having the former does not provision the latter.

## What exists today

### Four recovery-data roles

The four-role preflight deliberately models the two physical recovery routes:

| Route | Publisher role/profile | Receiver role/profile | Namespace |
| --- | --- | --- | --- |
| WA-FI → Object Storage → WA-IR | `fi-publisher` / `fi-publisher-immutable-create-only-v1` | `ir-receiver` / `ir-receiver-exact-readonly-v1` | `physical-wal` |
| WA-IR → Object Storage → WA-FI | `ir-publisher` / `ir-publisher-immutable-create-only-v1` | `fi-receiver` / `fi-receiver-exact-readonly-v1` | `physical-failback` |

Their role-local factories/loaders use separate fixed credential files and
exact campaign/release recovery prefixes.  The four-role binding rejects
identity collisions and binds the normal and reverse routes separately.  This
is the physical baseline/WAL/blob recovery plane; none of those credentials
or profiles is a control-mailbox role.

### Normal V2 mailbox plane

Normal V2 already has a complete, distinct eight-role mailbox model under
`physical-wal-v2-witness-roundtrip-delivery-v1/`:

```text
fi-writer-source-outbox  -> witness-fi-ingress
witness-ir-egress        -> ir-standby-ack-inbox
ir-durable-ack-outbox    -> witness-ir-ingress
witness-fi-egress        -> fi-writer-ack-inbox
```

For every normal role there is a named admission policy, host-role assertion,
retention proof, role-local credential file, S3 scope/adapter, durable
delivery runtime, dispatcher/full-bundle attestation, and a three-site
default-off deployment manifest.  That generation is directionally and
cryptographically normal-V2-specific; it must not be reused for reverse
Phase 5.

### V2R currently has a wire, receiver-local replay, and a local admission seam

`physical_wal_v2r_witness_roundtrip_contract` fixes the reverse four-hop
wire route and names its endpoints:

| V2R hop | Sender role | Receiver role |
| --- | --- | --- |
| `ir-to-witness` | `wa-ir-v2r-exporter` | `witness-v2r-reverse-ingress` |
| `witness-to-fi` | `witness-v2r-reverse-egress` | `wa-fi-v2r-recovery-inbox` |
| `fi-to-witness` | `wa-fi-v2r-ack-outbox` | `witness-v2r-ack-ingress` |
| `witness-to-ir` | `witness-v2r-return-egress` | `wa-ir-v2r-return-inbox` |

Its base prefix is `physical-wal-v2r-reverse/`.  The four mailbox labels
intentionally overlap the normal V2 labels, so a mailbox string alone is
never an authority selector.  Every future V2R admission must additionally
bind the V2R protocol domain, V2R role, exact child prefix, host, release,
stream generation, route/frontier pins, and V2R deployment binding.

V2R does reject reuse of the normal protocol domain, prefix, IAM hash, and
its four Ed25519 message-signing keys.  Its durable anti-replay registry also
has fixed namespaces for the four **receiving** V2R roles.  The new
`physical_wal_v2r_witness_roundtrip_control_mailbox_admission` module is a
default-off, signed local admission seam: it pins the exact eight-role
site/mailbox/prefix/action matrix, requires all twelve recovery/normal-V2
credential identities as explicit deny-pins, binds a fresh per-role identity,
and covers role-IAM/provider-route/Object-Lock proof hashes in its signed
assertion.  Its matrix check requires eight distinct V2R identities.
`physical_wal_v2r_witness_roundtrip_control_mailbox_profile` then accepts
only those verified admissions, repeats the exact host-attestation hash and
role/prefix/action tuple, and requires all twelve legacy deny-pins with their
fixed recovery-data/normal-V2 role labels.  Its profile set remains all-false
evidence only; it neither receives credentials nor verifies provider facts.
`physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission` can also
verify a fresh signed public eight-role bundle and its public per-site
manifest projection against those already admitted roles.  It is still only a
parser/verification boundary: it does not issue a bundle, render or install a
manifest, or make any provider call.

`physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer` is now the
matching pure prepare/finalize producer for that exact public bundle schema.
It derives all eight role projections only from one opaque verified V2R
profile set, uses a V2R-only injected bundle signer, and deny-pins the normal
V2 bundle signer, prefix, and IAM catalog.  It packages already signed local
*claims* only: it does not verify Object-Lock, IAM, or any provider fact, and
its prepared state remains explicitly non-operational.

`physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer` is the
matching pure, default-off renderer for one public site projection.  It
accepts only an opaque already-admitted V2R public bundle and a fixed site
selector, derives the exact two WA-FI, two WA-IR, or four Witness role slice,
and emits only the existing public site-manifest wire schema.  It accepts no
raw role, credential, IAM, provider, path, installer, service, or runtime
input; it also refuses an in-memory rewrite of the admitted bundle's sealed
role/prefix/identity claims.  The output has no activation or authority field.

It opens no S3 client, creates no Object Storage role, verifies no live IAM
or retention evidence, and invokes no callback.  It grants no election,
lease, writer, promotion, traffic, Phase-5 success, or Full-Matrix authority.

## Exact Phase-5 deployment gap

There is still no V2R equivalent of any of these operational V2 modules today
(the local signed admission, pure profile-set grammar, public-bundle
admission, claims-only bundle issuer, and public site-slice renderer below
are not substitutes):

- deployment-plan renderer, installer, or activation materializer (the public
  bundle issuer and public site-slice renderer cannot establish provider facts
  or install anything);
- Object-Lock retention proof and provider route/IAM attestation;
- fixed-role credential reader, S3v4 scope, or named mailbox adapter;
- durable outbound/inbound mailbox delivery runtime;
- eight-role dispatcher;
- three-site V2R deployment-plan renderer and fresh manifest materializer; or
- integration of the four anti-replay reservations into real receiver
  callbacks.

`physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission` verifies
a fresh signed **public** eight-role bundle against all eight fresh local
admissions, then verifies one exact public per-site manifest slice against
that bundle.  Both results are non-serializable, default-off, and explicitly
non-operational.  It is not an issuer, provider/IAM verifier, credential
reader, S3 adapter, service runtime, or deployment permission.  The separate
V2R public site-slice renderer only materializes that already-defined public
schema; it is not an installer, service configuration renderer, or deployment
permission.

Consequently `v2r_iam_policy_sha256` is still only a deny-pinned scalar plus
signed public hash projections.  It is not eight scoped IAM grants, live
provider evidence, or permission to use any existing V2 or recovery-data
credential.

## Required fresh V2R role matrix

Before a reverse Phase-5 strict-ACK runtime can exist, add an isolated V2R
mailbox generation with exactly these eight roles and child prefixes:

| Host | V2R role | Direction | Least privilege | Required fixed child prefix |
| --- | --- | --- | --- | --- |
| WA-IR | `wa-ir-v2r-exporter` | publish | create-only + own exact receipt | `physical-wal-v2r-reverse/ir-to-witness/` |
| Witness | `witness-v2r-reverse-ingress` | consume | fixed-prefix list + exact-version read | `physical-wal-v2r-reverse/ir-to-witness/` |
| Witness | `witness-v2r-reverse-egress` | publish | create-only + own exact receipt | `physical-wal-v2r-reverse/witness-to-fi/` |
| WA-FI | `wa-fi-v2r-recovery-inbox` | consume | fixed-prefix list + exact-version read | `physical-wal-v2r-reverse/witness-to-fi/` |
| WA-FI | `wa-fi-v2r-ack-outbox` | publish | create-only + own exact receipt | `physical-wal-v2r-reverse/fi-to-witness/` |
| Witness | `witness-v2r-ack-ingress` | consume | fixed-prefix list + exact-version read | `physical-wal-v2r-reverse/fi-to-witness/` |
| Witness | `witness-v2r-return-egress` | publish | create-only + own exact receipt | `physical-wal-v2r-reverse/witness-to-ir/` |
| WA-IR | `wa-ir-v2r-return-inbox` | consume | fixed-prefix list + exact-version read | `physical-wal-v2r-reverse/witness-to-ir/` |

The four receiver roles must use the existing anti-replay namespaces only
after a new V2R admission has been checked; the four publisher roles need new
durable create-only state.  No generic role selector, broad prefix, direct
FI↔IR request, or fallback to normal V2 is safe.

## Key, identity, and attestation requirements

For the new V2R mailbox generation, require all of the following as fresh
artifacts, not aliases:

1. Eight distinct Object-Storage mailbox credential identities, one for each
   role above.  They must be disjoint from all four recovery-data identities
   and all eight normal-V2 mailbox identities, even if roles co-reside on a
   host.  The four recovery credential files and normal V2 credential
   directory are deny-pins, never sources for V2R credentials.
2. Four distinct V2R message-signing key roles already named by the V2R wire:
   IR export, Witness forward, FI acknowledgement, and Witness return.
   They remain disjoint from normal V2 signing keys; additionally bind each
   private signer to its owning V2R service rather than treating a public key
   hash as runtime proof.
3. A V2R-specific signed host-role assertion, retention proof, and provider
   route/IAM attestation for each of the eight roles.  Every one must include
   exact host, role, mailbox, child prefix, actions, policy hash, V2R
   deployment binding, and V2R delivery/configuration binding.
4. One fresh V2R eight-role full-bundle attestation containing only public
   projections.  It must reject a V2 bundle, a four-role recovery preflight,
   an old V2R bundle, duplicated identities, and a role/prefix substitution.
5. New per-site V2R manifests: two services on WA-IR, two on WA-FI, and four
   on Witness, each with its own local config and credential path.  They must
   be default-off and admitted only against the fresh V2R bundle and local
   V2R provider/IAM evidence.

## Unsafe reuse cases that must remain rejected

- `fi-publisher`, `ir-receiver`, `ir-publisher`, or `fi-receiver` used as a
  V2R mailbox credential or action profile;
- any normal V2 role such as `ir-durable-ack-outbox` or
  `witness-ir-ingress` substituted for a similarly named V2R hop;
- normal V2 prefix, IAM policy hash, full-bundle attestation, retention proof,
  host-role assertion, deployment binding, signer key, dispatcher, or
  manifest reused by V2R;
- a V2R base prefix with an unbound mailbox child prefix, wildcard list, or
  a publish identity capable of a receive/delete/overwrite action; and
- any claim that Object Storage supplies election, lease, writer, promotion,
  or Phase-5 success authority.

`tests/test_physical_full_matrix_v4r_phase5_reverse_control_mailbox_gap.py`
locks the present fail-closed state: the four recovery-data roles, normal V2
roles, and all eight named V2R endpoint roles are pairwise disjoint, and no
normal V2 deployment surface may import the V2R carrier.  It also records that
the current V2R code has only its wire grammar, receiver-local replay
reservation, local signed admission/profile seam, and public-bundle/manifest
admission verifier plus a claims-only public-bundle issuer and public
site-slice renderer.  It has no provider or deployment execution surface.
When a fresh V2R deployment plane is added, this static gate must be extended
in the same review by full eight-role IAM, adapter, runtime, bundle-issuer,
and manifest-materializer tests; deleting it alone is not a safe migration.

## Implementation order

1. Add fresh V2R Object-Lock/provider route-IAM evidence and a corresponding
   live-provider-backed eight-role bundle/manifest admission layer.
2. Add root-only fixed-role credential/scope/adapter/runtime components,
   first proving create-only and exact-version restrictions locally.
3. Integrate the four existing anti-replay reservations before every receiver
   acceptance, then add the V2R post-effect Phase-5 provenance bridge.
4. Only then design a root-owned Phase-5 adapter and collect live external
   evidence.  None of the earlier steps authorizes a campaign.

## Source trace

| Current artifact | What it proves / what it does not |
| --- | --- |
| `physical_arvan_s3_four_role_preflight_binding.py`, role factories/loaders, and `physical_arvan_s3_role_profiles.py` | Four distinct recovery-data identities and two recovery namespaces; no mailbox control role. |
| `physical_wal_v2_witness_roundtrip_mailbox_admission.py` | The normal V2 eight-role admission/prefix policy only. |
| `physical_wal_v2_witness_roundtrip_s3_mailbox_adapter.py`, `_arvan_s3v4_scope.py`, `_delivery_runtime.py`, `_arvan_s3v4_delivery_dispatcher.py` | Named normal V2 S3 role machinery, never V2R. |
| `physical_wal_v2_witness_roundtrip_deployment_plan.py` and `_full_bundle_*` | Normal V2 three-site deployment/full-bundle artifacts only. |
| `physical_wal_v2r_witness_roundtrip_contract.py` | Fixed V2R signed four-hop message grammar, deny-pins, and four signer roles; no S3 runtime or deployment artifacts. |
| `physical_wal_v2r_witness_roundtrip_durable_anti_replay.py` | Local durable replay reservation for four V2R receivers only; explicitly requires later Object-Storage callback plumbing. |
| `physical_wal_v2r_witness_roundtrip_control_mailbox_admission.py` and `physical_wal_v2r_witness_roundtrip_control_mailbox_profile.py` | Default-off exact eight-role signed local admission plus labeled legacy-deny-pinned host-attestation profile/set checks; no live provider/IAM/adapter/runtime. |
| `physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission.py` | Default-off public bundle and public per-site manifest admission against eight fresh local grants; no provider, credential, adapter, or runtime. |
| `physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer.py` | Pure V2R-only claims bundle preparer/finalizer from the opaque profile set; no raw role projections, normal-V2/recovery import, provider/IAM verification, credential, adapter, runtime, or deployment authority. |
| `physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer.py` | Pure V2R-only default-off renderer of an exact public 2/2/4 site slice from the opaque admitted bundle; no raw role/IAM/provider/path/credential input, installer, service config, runtime, or deployment authority. |
