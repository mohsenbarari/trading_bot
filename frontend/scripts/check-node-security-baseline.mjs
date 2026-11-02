// Keep this floor aligned with package.json and the October 2026 Node security fixes.
// Vendor-patched Node can retain the old Brotli version string, so test Node itself.
export function isSupportedSecureNode(version) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) return false
  const [major, minor, patch] = version.split('.').map(Number)
  if (major === 22) return minor > 23 || (minor === 23 && patch >= 4)
  if (major === 24) return minor > 22 || (minor === 22 && patch >= 1)
  return major === 26 && (minor > 12 || (minor === 12 && patch >= 1))
}

if (!isSupportedSecureNode(process.versions.node)) {
  console.error(`Unsupported or unpatched Node ${process.versions.node}; use 22.23.4+, 24.22.1+, or 26.12.1+ on a supported release line.`)
  process.exitCode = 1
}
