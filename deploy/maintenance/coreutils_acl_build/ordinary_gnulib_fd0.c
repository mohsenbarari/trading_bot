/* Actual gnulib qcopy_acl regular-FD0 regression, isolated vendor CI only.
   No pathname can serve as an operation authority: both diagnostic names are
   deliberately nonexistent.  Link the actual built lib/libcoreutils.a.
   SOURCE ONLY until Root's admitted vendor CI compiles/runs it.
   SPDX-License-Identifier: GPL-3.0-or-later */
#include <config.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/acl.h>
#include <acl/libacl.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "acl.h"

static char work[] = "gold-gnulib-fd0-owned-XXXXXX";
static int cwd_fd = -1, source_fd = -1, dest_fd = -1, saved_input = -1;
static int entered;
static unsigned checks;

static void
failure_cleanup (void)
{
  if (source_fd >= 0) close (source_fd);
  if (dest_fd >= 0) close (dest_fd);
  if (saved_input >= 0) { dup2 (saved_input, 0); close (saved_input); }
  if (entered) { unlink ("source"); unlink ("destination"); }
  if (cwd_fd >= 0)
    { if (fchdir (cwd_fd) == 0) rmdir (work); close (cwd_fd); }
}

static void
need (int value, char const *code)
{
  if (!value)
    {
      fprintf (stderr, "owned_gnulib_fd0_refused:%s:errno=%d\n", code, errno);
      exit (1);
    }
  checks++;
}

int
main (int argc, char **argv)
{
  if (argc != 2 || strcmp (argv[1], "--run-owned-gnulib-fd0"))
    { puts ("UNARMED: no fixture created"); return 0; }
  need (geteuid () == 10001 && getegid () == 10001,
        "genuine_unprivileged10001_required");
  cwd_fd = open (".", O_PATH | O_DIRECTORY | O_CLOEXEC);
  need (cwd_fd >= 0, "cwd_hold");
  need (mkdtemp (work) != NULL, "owned_root_create");
  atexit (failure_cleanup);
  need (chdir (work) == 0, "owned_root_enter"); entered = 1;
  saved_input = fcntl (0, F_DUPFD_CLOEXEC, 3);
  need (saved_input >= 0 || errno == EBADF, "stdin_save_or_original_closed");
  close (0);
  source_fd = open ("source", O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  need (source_fd == 0, "actual_regular_source_fd_zero");
  dest_fd = open ("destination", O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  need (dest_fd > 0, "actual_regular_destination_fd_positive");
  acl_t reference = acl_from_text ("u::rw-,u:65534:r--,g::---,m::r--,o::---");
  need (reference != NULL && acl_set_fd (source_fd, reference) == 0,
        "actual_nontrivial_source_access_acl");
  struct stat before_source, before_dest, after_source, after_dest, unused;
  need (fstat (source_fd, &before_source) == 0
        && fstat (dest_fd, &before_dest) == 0
        && S_ISREG (before_source.st_mode) && S_ISREG (before_dest.st_mode),
        "actual_regular_inode_admission");
  errno = 0;
  need (lstat ("deliberately-missing-source-diagnostic", &unused) == -1
        && errno == ENOENT, "source_diagnostic_really_absent");
  errno = 0;
  need (lstat ("deliberately-missing-destination-diagnostic", &unused) == -1
        && errno == ENOENT, "destination_diagnostic_really_absent");

  /* This is the real exported gnulib function from the vendor build, not the
     new private gold_acl_qset/qcopy helper and not a substituted callback. */
  int result = qcopy_acl ("deliberately-missing-source-diagnostic", source_fd,
                         "deliberately-missing-destination-diagnostic", dest_fd,
                         before_source.st_mode);
  need (result == 0, "actual_gnulib_qcopy_acl_fd0_no_path_fallback");
  acl_t actual = acl_get_fd (dest_fd);
  need (actual != NULL && acl_cmp (reference, actual) == 0,
        "actual_nontrivial_acl_copied_to_destination_fd");
  need (fstat (source_fd, &after_source) == 0
        && fstat (dest_fd, &after_dest) == 0
        && before_source.st_dev == after_source.st_dev
        && before_source.st_ino == after_source.st_ino
        && before_dest.st_dev == after_dest.st_dev
        && before_dest.st_ino == after_dest.st_ino
        && (after_dest.st_mode & 07777) == (before_source.st_mode & 07777),
        "actual_same_inodes_and_mode_preserved");
  acl_free (actual); acl_free (reference);
  int a = source_fd, b = dest_fd;
  source_fd = dest_fd = -1;
  need (close (a) == 0 && close (b) == 0, "regular_descriptors_closed");
  errno = 0; need (fcntl (a, F_GETFD) == -1 && errno == EBADF, "source_fd_physically_closed");
  errno = 0; need (fcntl (b, F_GETFD) == -1 && errno == EBADF, "destination_fd_physically_closed");
  if (saved_input >= 0)
    {
      int prior = saved_input; saved_input = -1;
      need (dup2 (prior, 0) == 0 && close (prior) == 0, "original_stdin_restored");
    }
  need (unlink ("source") == 0 && unlink ("destination") == 0,
        "exact_owned_regular_files_removed");
  need (fchdir (cwd_fd) == 0, "original_cwd_restored"); entered = 0;
  need (rmdir (work) == 0, "owned_root_removed");
  int prior_cwd = cwd_fd; cwd_fd = -1;
  need (close (prior_cwd) == 0, "cwd_descriptor_closed");
  errno = 0; need (fcntl (prior_cwd, F_GETFD) == -1 && errno == EBADF,
                   "cwd_descriptor_physically_closed");
  printf ("{\"schema\":\"ordinary_gnulib_regular_fd0/1\",\"status\":\"PASS\",\"actual_uid\":10001,\"actual_gid\":10001,\"actual_assertions\":%u,\"actual_gnulib_qcopy_acl_called\":true,\"source_fd_zero\":true,\"nonexistent_diagnostic_names\":true,\"actual_acl_and_mode_copied\":true,\"owned_cleanup_complete\":true,\"security_accepted\":false}\n", checks);
  return fflush (stdout) == 0 ? 0 : 1;
}
