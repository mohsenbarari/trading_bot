# App ed59: explicit fresh finding reconciliation

The child `sha256:ed59f14519b7329306f334e05d8666f808ac2c37da78afa922ddd21c60f3cedd`
was built and exported by run40165530395 from9a4bec1e. Its new raw report contains
0CRITICAL,44HIGH,68MEDIUM,82LOW and1UNKNOWN. The raw HIGH gate failed and stays
failed. At dossier creation the image had not been loaded or activated.
The later [actual two-host LOAD record](2026-10-25-app-ed59-two-host-loads.md)
proves image-store admission and LOAD; no serving role is activated.

Root independently checked the complete advisory/package/installed-version/
fixed-version/severity/PURL multiset against the actual parent report: every
row matches, including the eight HIGH advisory groups. The new image preserves
the parent's ordered32-layer prefix. Actual export inspection verified its
33rd layer: an `/app` header with unchanged mode/UID/GID and exactly three new root-owned0755
directories, with no regular file, link, whiteout, special entry or other change.

The new evidence also compares all14173existing `/app` and `/usr/local` entries,
including12269regular files, and verifies the857Python sources against the
unchanged public payload manifest. All99package versions and libc bytes match.
All56historical tests passed in the actual child; normal native trust, SQLite,
psycopg2, component presence and static ACL-consumer checks ran on that same
identity. Full Config equality in RAM permits only the four explicit provenance
labels. These are actual image proofs, not proof of serving-role behavior.

The [reconciliation record](evidence/2026-10-25-app-ed59-finding-reconciliation.json)
binds the new raw report, SBOM, composition, physical export scope, file parity,
component/ACL receipts, parent dossier and every new HIGH row. It records the
parent's assessment explicitly rather than changing the old8da record or
silently equating two images. The same physical package/source files support
the prior source/vendor assessments; fresh component-absence evidence belongs
to this child. Historical parent condition templates remain visibly historical.
Separate child conditions name ed59 and all remain unverified.

The ACL legacy-callsite and dynamic/native closure limits remain OPEN. Installed
legacy ACL consumers are still present. No whole-library FIXED claim follows
from a newer library, unchanged files, a scan, static symbols or successful
historical tests. Actual five-role contracts, complete private configuration
parity in RAM, mount/writer/path boundaries, source checks and the normal
API/Bot/browser/database/backup matrix still require verification before any
serving-scope assessment or final acceptance. This dossier changes no runtime
guard, COW policy, controller or operation window.

MEDIUM/LOW/UNKNOWN findings remain visible; this reconciliation does not claim
that every raw finding is repaired or that untested routes are safe. It grants
no activation authority, finding waiver, raw-gate pass, global security
eligibility or deployment eligibility. The original failed build40164897246,
its corrected-format preflight and both raw reports stay retained.
