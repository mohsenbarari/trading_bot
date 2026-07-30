# WA-IR Bootstrap Receive Renderer

`scripts/render_webapp_ir_stage_bootstrap_receive.py` is a controller-local
renderer for the first WA-IR artifact-stage consumer. It validates the
root-only prepared package, its canonical preparation receipt, and the
transient root-only `publish-bootstrap` receipt, then prints one SSH control
command for the fixed WA-IR target `root@95.38.164.29`.

It never invokes SSH, Object Storage, curl, age, tar, Docker, or a service.
The only FI-to-IR artifact route in the rendered command is the exact
private/versioned Object Storage URL already present in the publish receipt.
That URL is the last remote argument; it is not embedded in the Python payload
or the installed receipt.

## Render

```bash
python3 scripts/render_webapp_ir_stage_bootstrap_receive.py \
  --publish-receipt /root/secure-envs/trading-bot/current/publish-bootstrap.json \
  --bootstrap-package-directory /root/secure-envs/trading-bot/current/bootstrap-package \
  --preparation-receipt /root/secure-envs/trading-bot/current/bootstrap-package/bootstrap-preparation-receipt.json \
  --bootstrap-root /srv/trading-bot-three-site-staging-data/wa-ir-bootstrap
```

All metadata inputs must be absolute, canonical, root-owned, non-symlink
regular files with no group or other permissions. The package directory must
be root-private. The preparation receipt must be the canonical receipt inside
that directory. The renderer verifies the package archive locally before it
prints anything: archive bytes and hash, receipt self-hash, archive-embedded
`bootstrap-package.json`, every declared regular member hash, packaged consumer
configuration, control commit/tree, and the immutable Object Storage key.

The printed line contains a live presigned URL. Treat the line as transient
operational input: do not place it in a ticket, evidence file, source control,
or durable terminal log. The remote receiver only receives it as its final
argument and writes a root-only receipt without it.

## Remote Effect Of The Printed Command

Only an already-authorized operator should execute the rendered line. It asks
WA-IR to use `/usr/bin/curl`, `/usr/bin/age`, `/usr/bin/python3`, and
`/usr/bin/tar` to download one exact version without redirects, verify headers,
bytes and hashes, decrypt with the fixed root-only bootstrap identity, verify
the archive before extraction, and extract to one fresh root-only candidate.
It neither activates the candidate nor starts, stops, recreates, or modifies a
service, current pointer, deployment, volume, application data, or existing
Object Storage object.

The current publisher/preparer receipt schema does not carry age-recipient
metadata. The receiver therefore does not claim a pre-decryption recipient
metadata check. Its receiver-side binding is successful decryption by the
fixed pinned WA-IR identity followed by exact archive, manifest, consumer
configuration, control, and preparation-receipt verification.
