# Final Finland installed-kernel reboot only

Root's public inventory at02:59:02Z25October observed Final65.109.214.203 on
Ubuntu26.04, kernel7.0.0-29, reboot-required true. Kernel image/modules
7.0.0-38.38 and both boot images were already installed. This host receives
ciphertext backups and has no application stack. Source preparation grants no
application, package, Writer or DNS changes.

The new fixed inventory uses the existing Finland primary SSH identity with
strict known-host checking and the approved ED25519 fingerprint
`SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`. Default action is `check`.
The read-only probe verifies the release, current29-or38kernel, exact installed
38packages/boot files and generated GRUB first/default selection with no
one-shot override. Unknown boot selection refuses; this playbook never changes
GRUB or installs packages. Backup verification traverses only metadata with
retained directory/file descriptors, a15s/4096entry bound and no file-content
read. It emits a metadata digest and count, not ciphertext names or values.

Root may execute the reviewed playbook with the fixed inventory and
`final_kernel_action=apply` plus confirmation
`ONE-FINAL-KERNEL-REBOOT-20261025-01`. The single standard Ansible reboot runs
only if the old kernel or reboot-required flag remains. It has300s boot/SSH
phases and a600s task bound. Postcheck requires kernel38, reboot-required false,
a changed boot ID when reboot was needed, and exact preserved backup metadata.
Already38 with no pending reboot verifies and skips the reboot. Check mode never
reboots. No backup file, runtime Env, key/session content, package or application
is copied or modified. Normal SSH alone consumes the existing private identity.
Root must serialize this maintenance against any new ciphertext transfer; the
metadata comparison does not claim to hold an upload lock across reboot.

No SSH, reboot or package operation was performed while authoring this packet.
Actual host preflight, reboot and postboot receipts remain Root's work. Global
security acceptance remains false.
