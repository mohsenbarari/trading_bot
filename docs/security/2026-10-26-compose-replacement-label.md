# Exact Compose replacement labels for implicit service names

The fresh read-only F79 comparison `4e9bfca9b98f44328c1867096394f0b3`
returned actual exit0 after source finalizers. All 14 known label checks matched
except `com.docker.compose.replace`; unknown Compose labels were zero. It
printed only field names, types and equality, never label values, Env or private
hashes. No diagnostic or serving change was applied.

The official [Compose5.6.1 setter](https://github.com/docker/compose/blob/v5.6.1/pkg/compose/executor_ops.go)
and [Compose2.40.3 setter](https://github.com/docker/compose/blob/v2.40.3/pkg/compose/convergence.go)
use an explicit service container name when declared, otherwise the service
name plus instance number. The implicit replacement label excludes the project
prefix, although the actual Docker container name includes it.

Normal Market and datastore validation now give the label checker that exact
creation name in a validation-only copy. Actual container identity, hostname,
network aliases, image/command/Env, complete configuration and replacement-mode
classification remain unchanged. Arbitrary prefixes, container IDs, wrong
instance numbers and replacement labels without replacement classification still
refuse. Original operands are not modified.

Public source audit confirmed all five App roles and the native/web App
PostgreSQL/Redis services have explicit names. Shared C/A/S and successful O10
evidence were unchanged. Market PostgreSQL uses an implicit name and receives
the same correction. F79/Market admission source pins were refreshed; old
controls, failed observations and staged bytes remain preserved.

Root ran 129 public tests for Market/datastore validation and the F79/Market
admission/role consumers with genuine UID/GID0, CAP_CHOWN-only, NNP, separate
init, read-only root and no network/production inputs. Manifest:
`5a567f08dcaa9674666cc6712d82b87203dfe7f249859e4d224ba215973be063`.
Result: `d9417675cea4e827737fc4c2e21ded1c64e0a04c74dd54b97e28f00de261fa9d`.
Actual final status0, zero failures/errors/skips. The first generated caller was
rejected before any Docker request because a broad text substitution changed
its capture pin; its unexecuted files remain. The accepted caller used targeted
substitutions and explicit constant validation.

The earlier label-comparator request `637ea1ea…` failed before comparison because
its caller used a service unit/300-second budget instead of the existing exact
scope-name/900-second contract. That failure remains retained. A separately
staged v2 comparator, fresh control and the unchanged exact caller contract
produced the successful comparison above. No admission condition was bypassed.

Global security acceptance and all serving/Market functional acceptance remain
false until their respective actual operations complete.
