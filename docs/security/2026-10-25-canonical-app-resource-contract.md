# Canonical App/Web resource accounting v3

This source packet introduces a separate App v3 image-store entrypoint. It does
not run an operation, activate a role, change an old worker or reuse a failed
request. The actual df941 failure and 2ddd closure remain history. Root must
produce fresh authority and fresh successful NoApply observations before a new
request. Serving/security/full-matrix acceptance remains false.

The exact 452719104-byte e100 App archive has 452635136 expanded layer bytes.
Root's read-only unchanged-validator measurement peaked at 35815424 Python RSS
bytes; the separate 33-layer metadata count peaked at 35999744 bytes. The largest
layer has 10949 entries, 1647 directories, 869792 path bytes and 299 PAX bytes in
four records; xattrs are zero. Both runs performed no LOAD and measured no Go
daemon. The four exact public observations are tracked under
`tests/fixtures/canonical_app_resources/`; CI does not depend on ignored local
operational files.

The installed Engine 29.9.2 commit is 0367cbe (full
0367cbeb49b9d98cb0b4f51e95f7b54b4803e76c), with containerd 2.4.2 commit
b7a25261e029d68978bbf290df0a62fc819b3c51. The exact containerd-backed import
streams blobs into content storage, then applies layers sequentially. Its
unpacker retains directory headers and path maps; its payload copy buffers do
not hold entire layer bodies. Configured content/snapshot roots were projected
from the public plugin exports and independently measured on the root disk.
No daemon Env/config was read. Native's overlay2 importer has a different path
and keeps the complete existing policy.

The exact source paths are [Engine import](https://github.com/moby/moby/blob/0367cbeb49b9d98cb0b4f51e95f7b54b4803e76c/daemon/containerd/image_exporter.go),
[containerd archive import](https://github.com/containerd/containerd/blob/b7a25261e029d68978bbf290df0a62fc819b3c51/core/images/archive/importer.go),
[layer application](https://github.com/containerd/containerd/blob/b7a25261e029d68978bbf290df0a62fc819b3c51/pkg/archive/tar.go)
and [temporary snapshot mounts](https://github.com/containerd/containerd/blob/b7a25261e029d68978bbf290df0a62fc819b3c51/core/mount/temp.go).

Web's new engineering RAM floor is:

```
2,216,689,664 existing base headroom
  536,870,912 hard aggregate controller/Docker/SSH client cgroup
  268,435,456 metadata/runtime/stream allowance
-------------
3,021,996,032 bytes, plus any additional tmpfs payload-write budget
```

The allowance starts with 128 MiB and adds 4 KiB per observed entry, directory,
PAX record and xattr record, plus 16 times path/PAX/xattr bytes for the largest
layer. This produces 199748784 bytes, rounded upward to 256 MiB. These are explicit
conservative engineering margins, **not a formal Go heap quota**. The existing
base headroom is retained; no arbitrary 119 MiB deduction is used. The fixed
backend writes payload to the proven disk roots; unexpected versions, drivers,
configured roots or filesystem identity changes refuse. Live public version and
plugin-root queries run before any image request. No opaque daemon override
absence or total-system hard memory guarantee is claimed.

Every disk check keeps its original 8 GiB store/2 GiB root/2 GiB temporary floor,
64 MiB margin, 2 MiB output reserve and full 1810708480-byte prospective import
budget. Web NoApply checks the complete prospective budget before any owned
guard or attempt. RAM and disk are separate operands. Additional tmpfs payload
writes raise the RAM requirement; they cannot lower a disk or RAM floor.

The 512 MiB bound uses cgroup `MemoryMax`, with swap 0, CPU 100% and TasksMax 64.
**No Go process receives an address-space limit.** The Python-only read probes
and owned failure-test children use AS limits; the actual worker does not.
Scope name, cgroup limits, 900-second lifetime, 5-second stop timeout,
control-group kill and SIGKILL policy are admitted. Snapshot lifetime is checked
once before backend queries; recurring capacity checks contain no commands.

The new entrypoint is `canonical_app_candidate_images_v3.py`; its resource
helper is `canonical_app_resource_contract.py`. Control, baseline, archive
admission, preheader refusal and loaded-image schemas are /3. Both baselines and
each fresh control bind the resource contract SHA. The resource helper itself
has an exact cold source pin and all four evidence FDs remain checked. Base 1041
load, stream, hash, framing and unknown-outcome algorithms stay unchanged.
Native uses its original capacity and scope admission. Unknown daemon requests
retain their owned guards and cannot be automatically retried.

All Web phases, including the stream subprocess launched by Native LOAD, use
512 MiB. Web snapshot additionally requires `--snapshot-nonce <fresh32hex>`.
The producer should consume `scope_arguments(phase, nonce)` from the pinned
resource helper. Existing operational producers are not silently changed by
this packet; a producer retaining old Web 2 GiB flags fails the new admission.
No actual activation-receipt validator is updated until Root reviews and
freezes this source packet.

Register `test_canonical_app_resource_contract` in the required genuine-root
inventory. `test_canonical_app_resource_children` belongs to the ordinary lane:
its two tests need no UID0, launch only owned Python children, enforce a real
512 MiB Python AS bound and check closed failure/reap/unchanged-parent limits.
They do not claim to exercise a Docker daemon or emulate root ownership.
