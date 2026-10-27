/* Descriptor-bound metadata for cp/mv's nonregular objects only.
   GPLv3-or-later, like Coreutils.  This private integration is a SOURCE
   proposal; compilation and ordinary runtime verification remain required.  */
#include <config.h>
#include "system.h"
#include <fcntl.h>
#include <sys/acl.h>
#include <acl/libacl.h>
#include <attr/libattr.h>
#include "acl.h"
#include "acl-internal.h"
#include "gold-acl-fd.h"
#include "gold-acl/nontrivial.h"

#if !defined __linux__ || !GOLD_DESCRIPTOR_ACL
# error This private backport requires its admitted GNU/Linux configuration.
#endif
_Static_assert (O_PATH != 0 && AT_EMPTY_PATH != 0
                && O_NOFOLLOW != 0 && AT_SYMLINK_NOFOLLOW != 0,
                "Object-handle flags must never be zero fallbacks");

int
gold_acl_open_bound (int parent, char const *name,
                     struct stat const *expected, bool follow)
{
  int flags = O_PATH | O_CLOEXEC | (follow ? 0 : O_NOFOLLOW);
  int fd = openat (parent, name, flags);
  if (fd < 0)
    return -1;
  struct stat actual;
  if (fstat (fd, &actual) != 0)
    {
      int e = errno;
      close (fd);
      errno = e;
      return -1;
    }
  if (actual.st_dev != expected->st_dev
      || actual.st_ino != expected->st_ino
      || (actual.st_mode & S_IFMT) != (expected->st_mode & S_IFMT))
    {
      close (fd);
      errno = ESTALE;
      return -1;
    }
  return fd;
}

void
gold_acl_close_handles (struct gold_acl_handles *handles)
{
  int e = errno;
  /* Linux close consumes the descriptor even on EINTR: do not retry a
     possibly reused descriptor.  Fatal process exit closes all owned FDs.  */
  if (0 <= handles->destination)
    { close (handles->destination); handles->destination = -1; }
  if (0 <= handles->source)
    { close (handles->source); handles->source = -1; }
  errno = e;
}

int
gold_acl_chmod_fd (int fd, mode_t mode)
{
  if (fd < 0)
    { errno = EBADF; return -1; }
  /* Empty-path fchmodat2/glibc support is a real runtime requirement.
     There is no caller-path fallback when it is unavailable.  */
  return fchmodat (fd, "", mode, AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW);
}

int
gold_acl_chown_fd (int fd, uid_t uid, gid_t gid)
{
  if (fd < 0)
    { errno = EBADF; return -1; }
  return fchownat (fd, "", uid, gid, AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW);
}

int
gold_acl_qset_fd (int fd, mode_t mode)
{
  if (fd < 0)
    { errno = EBADF; return -1; }
  /* The Linux mode-only qset_acl -> set_permissions semantics, including
     special-bit ordering, chmod on ACL failure, original errno, and ONLY
     the caller-supplied mode's directory bit selecting default deletion.
     Never add S_IFDIR merely because the held object is a directory.  */
  bool early = (!MODE_INSIDE_ACL || (mode & (S_ISUID | S_ISGID | S_ISVTX)));
  if (early && gold_acl_chmod_fd (fd, mode) != 0)
    return -1;

  acl_t acl = acl_from_mode (mode);
  if (acl == NULL)
    {
      int e = errno;
      if (!early)
        gold_acl_chmod_fd (fd, mode);
      errno = e;
      return -1;
    }
  int ret = acl_set_file_at (fd, "", AT_EMPTY_PATH, ACL_TYPE_ACCESS, acl);
  int e = errno;
  acl_free (acl);
  if (ret == 0)
    return S_ISDIR (mode) ? acl_delete_def_file_at (fd, "", AT_EMPTY_PATH) : 0;

  bool unsupported = !acl_errno_valid (e);
  int chmod_ret = early ? 0 : gold_acl_chmod_fd (fd, mode);
  if (unsupported)
    return chmod_ret;
  errno = e;
  return -1;
}

/* Same permission predicate as Debian9.7-3 qcopy_acl, including opaque NFSv4
   values and configured ATTR_ACTION_PERMISSIONS entries.  */
static int
gold_acl_permission_attr (char const *name, struct error_context *ctx)
{
  return strcmp (name, "system.posix_acl_access") == 0
         || strcmp (name, "system.posix_acl_default") == 0
         || strcmp (name, "system.nfs4_acl") == 0
         || attr_copy_action (name, ctx) == ATTR_ACTION_PERMISSIONS;
}

int
gold_acl_qcopy_fd (char const *source, int source_fd,
                   char const *dest, int dest_fd, mode_t mode)
{
  if (source_fd < 0 || dest_fd < 0)
    { errno = EBADF; return -1; }
  int ret = gold_acl_chmod_fd (dest_fd, mode);
  if (ret == 0)
    {
      ret = gold_acl_attr_copy_fd (source, source_fd, dest, dest_fd,
                                   gold_acl_permission_attr, NULL);
      if (ret < 0 && errno == EOPNOTSUPP)
        {
          /* Exact Debian fallback classification, using only the held source
             object, including NFSv4 precedence and its unchanged XDR parser.
             No acl_equiv_mode approximation and no original-path fallback.  */
          if (!gold_acl_fd_has_nontrivial (source_fd, mode))
            ret = 0;
          errno = EOPNOTSUPP;
        }
    }
  return ret;
}
