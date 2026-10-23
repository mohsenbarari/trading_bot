# Security remediation

- 2026-10-23 | Security fixes are an architecture-independent workstream; preserve
  Web/Bot contracts and do not silently mix them into topology-only acceptance.
  Source/test completion does not mean running services are patched. Current
  evidence and pending activation gates: `docs/security/2026-10-23-remediation.md`.
- Upgrade FastAPI/Starlette/Pydantic and aiogram/aiohttp as a resolver-verified
  family. Old FastAPI and aiogram bounds exclude fixed transitive versions.
  Require actual parser/upload tests, not only a clean dependency resolver.
- Resolve every frontend file response, including SPA/root fallback, inside the
  trusted immutable build directory. Upload clients must not write that directory.
- Pillow 13 fixes pre-check APNG canvas allocation. Retain bounded input bytes,
  preflight ordinary PNG dimensions and check decoded pixels before EXIF/load;
  safety failures must never fall back to persisting the original image.
- Update both image OS and separate certifi CA stores; host updates alone do not
  fix immutable containers. The offline build gate checks known CA distrust only,
  not all image CVEs. Security verification never disables TLS validation.
- Node vendor backports can retain an old Brotli version string. Verify the exact
  fixed Node release, not that dependency version alone. Repository/CI pins do not
  upgrade already-running host tools.
- Kernel/Docker/containerd maintenance must preserve active DB, Bot/Executor,
  Writer and sync ownership. Never reboot or upgrade the daemon as a side effect
  of a CLI-only update; plan quiescence, restore and smoke checks separately.
