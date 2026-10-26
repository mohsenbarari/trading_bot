# Normal Compose command inheritance

Security acceptance remains false. This change corrects the normal configuration
validator; it does not activate an image, restore production data, or change a
Writer. Native ordinary restore attempt `6c9af4d142c642efb8db172ddecce6e9`
remains failed and cannot be resumed. Its three owned containers were observed
stopped, PID zero, exit zero, no OOM and no restarts after their original expiry
at 04:56 UTC. A fresh read-only comparison found all 17 Config fields equal to
the currently admitted normal configuration; this does not establish the
historical HostConfig, HTTP readiness, or restore compatibility.

## Problem and behavior

The F79 normal observation had compared a rendered `entrypoint: null` to the
runtime Entrypoint literally. Compose uses the image entrypoint in that case.
The shared typed expectation now handles missing/null image inheritance,
explicit list overrides, and explicit empty lists separately. Runtime operands
are not modified. Strings, booleans, integers and mixed lists are refused.

For an inherited command with a nonempty explicit entrypoint, the expected Cmd
is null. Empty entrypoint combined with inherited command remains unsupported
and fails closed as `normal_command_coupling`; no runtime null/empty equivalence
was introduced. Market still requires its exact `service --role <role>` command.
All complete Config, environment, HostConfig, mounts and role checks remain.

Primary references:

- [Compose command](https://docs.docker.com/reference/compose-file/services/#command)
- [Compose entrypoint](https://docs.docker.com/reference/compose-file/services/#entrypoint)
- [Compose v5.6.1 create](https://github.com/docker/compose/blob/v5.6.1/pkg/compose/create.go)
- [Moby 29.9.2 config merge](https://github.com/moby/moby/blob/docker-v29.9.2/daemon/commit.go)
- [Moby 29.9.2 container creation](https://github.com/moby/moby/blob/docker-v29.9.2/daemon/create.go)

## Scope and evidence

The shared helper is used by normal App, Market and datastore baselines. Normal
admission, F79, Market role admission and App activation pins were refreshed;
the ordinary restore producer changes only its admission source pin. Historical
staged sources, requests and receipts retain their original bytes and identities.
The normal admission error classifier contains the additional static literal;
it never exposes arbitrary exception text.

Independent source review covered the helper, cross-field cases and consumer
pin-only changes. The new public Root packet has 321 tests, real UID/GID zero,
only CAP_CHOWN, no-new-privileges, separate init, a read-only root filesystem and
no network or production inputs. First packet `6f3d3c62…` ran 321 tests and
failed one exact-classifier test; its result `c47c2495…` remains retained. The
classifier omission was corrected before sealing a distinct second packet
`cbe2bd207b74137d80e0814aa51658622628d9166a3bcdea899fec3d903327b9`.
That second packet actually passed all 321 tests with zero failures, errors or
skips and actual return code zero after final checks. Its result is
`10509660322b3ceac71ec30488077aea543febb0cc081145470f3545a95b9e24`.

The separate App gate assembler remains unarmed until both sites have actual
successful ordinary restores and all cleanup/outer-controller finalizers have
completed with real exit status zero. An inner producer terminal alone cannot
prove that completion. No serving-role acceptance is granted by these fixtures.
