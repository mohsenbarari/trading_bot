/* Owned, disposable, ordinary filesystem fixture for later vendor CI.
   SOURCE ONLY here: no compiler or executable has been run.
   Run as the genuine unprivileged vendor-CI user, with no privileged mounts.
   This validates the private helper; real cp/mv/installation tests are separate.
   SPDX-License-Identifier: GPL-3.0-or-later */
#include <config.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/acl.h>
#include <acl/libacl.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "gold-acl-fd.h"
#define DECLARATIONS_ONLY
#include "gold-acl/xattrat_compat.h"

static int original_cwd = -1;
static char work[] = "gold-acl-owned-XXXXXX";
static struct gold_acl_handles held = { -1, -1 };
static unsigned checks;
static int entered;

static void
cleanup (void)
{
  gold_acl_close_handles (&held);
  if (original_cwd >= 0)
    {
      /* Fixed known children only; never recursive deletion or a caller path. */
      if (entered)
        {
          unlink ("link");
          rmdir ("source");
          rmdir ("destination");
          rmdir ("destination-held");
        }
      if (fchdir (original_cwd) == 0)
        rmdir (work);
      close (original_cwd);
      original_cwd = -1;
    }
}

static void
check (int ok, char const *code)
{
  if (!ok)
    {
      fprintf (stderr, "owned_fd_acl_refused:%s:errno=%d\n", code, errno);
      exit (1);
    }
  checks++;
}

static void
checked_cleanup (void)
{
  int source_fd = held.source, destination_fd = held.destination;
  gold_acl_close_handles (&held);
  errno = 0;
  check (source_fd < 0
         || (fcntl (source_fd, F_GETFD) == -1 && errno == EBADF),
         "source_handle_physically_closed");
  errno = 0;
  check (destination_fd < 0
         || (fcntl (destination_fd, F_GETFD) == -1 && errno == EBADF),
         "destination_handle_physically_closed");
  check (entered && original_cwd >= 0, "owned_cleanup_context");
  check (unlink ("link") == 0, "owned_symlink_removed");
  check (rmdir ("source") == 0, "owned_source_removed");
  check (rmdir ("destination") == 0, "owned_decoy_removed");
  check (rmdir ("destination-held") == 0, "owned_destination_removed");
  check (fchdir (original_cwd) == 0, "original_cwd_restored");
  entered = 0;
  check (rmdir (work) == 0, "owned_work_directory_removed");
  int cwd_fd = original_cwd;
  original_cwd = -1;
  check (close (cwd_fd) == 0, "original_cwd_handle_closed");
  errno = 0;
  check (fcntl (cwd_fd, F_GETFD) == -1 && errno == EBADF,
         "original_cwd_handle_physically_closed");
}

static int
fd_count (void)
{
  DIR *dir = opendir ("/proc/self/fd");
  check (dir != NULL, "fd_count_open");
  int count = 0;
  struct dirent *entry;
  while ((entry = readdir (dir)))
    if (strcmp (entry->d_name, ".") && strcmp (entry->d_name, ".."))
      count++;
  check (closedir (dir) == 0, "fd_count_close");
  return count;
}

static acl_t
get_acl (int fd, acl_type_t kind)
{
  acl_t acl = acl_get_file_at (fd, "", AT_EMPTY_PATH, kind);
  check (acl != NULL, "acl_get_object");
  return acl;
}

int
main (int argc, char **argv)
{
  if (argc != 2 || strcmp (argv[1], "--run-owned-fd-acl"))
    {
      puts ("UNARMED: no fixture created");
      return 0;
    }
  /* Root could mask the no-read test through DAC override.  Do not call such
     a run a passing unprivileged fixture or synthesize a UID result. */
  if (geteuid () == 0)
    {
      fputs ("genuine_unprivileged_fixture_user_required\n", stderr);
      return 1;
    }
  errno = 0;
  check (gold_acl_qset_fd (-1, 0700) == -1 && errno == EBADF,
         "negative_set_fd_refuses");
  errno = 0;
  check (gold_acl_qcopy_fd ("diagnostic-only", -1, "diagnostic-only", -1,
                            S_IFDIR | 0700) == -1 && errno == EBADF,
         "negative_copy_fd_refuses");

  original_cwd = open (".", O_PATH | O_DIRECTORY | O_CLOEXEC);
  check (original_cwd >= 0, "cwd_hold");
  check (mkdtemp (work) != NULL, "owned_directory_create");
  atexit (cleanup);
  check (chdir (work) == 0, "owned_directory_enter");
  entered = 1;
  check (mkdir ("source", 0700) == 0 && mkdir ("destination", 0700) == 0,
         "owned_children_create");
  struct stat source, destination;
  check (stat ("source", &source) == 0 && stat ("destination", &destination) == 0,
         "actual_admission_stats");

  int before = fd_count ();
  struct stat wrong = source;
  wrong.st_ino++;
  for (int i = 0; i < 32; i++)
    {
      errno = 0;
      check (gold_acl_open_bound (AT_FDCWD, "source", &wrong, false) == -1
             && errno == ESTALE, "wrong_inode_refuses");
    }
  check (fd_count () == before, "mismatch_closes_every_opened_fd");

  check (symlink ("source", "link") == 0, "owned_symlink_create");
  errno = 0;
  check (gold_acl_open_bound (AT_FDCWD, "link", &source, false) == -1
         && errno == ESTALE, "physical_source_does_not_follow");
  int logical = gold_acl_open_bound (AT_FDCWD, "link", &source, true);
  check (logical >= 0, "intended_logical_source_follows_same_inode");
  check (close (logical) == 0, "logical_handle_close");

  /* Real descriptor 0, without replacing a reported stat/UID/hash result. */
  int saved_input = fcntl (STDIN_FILENO, F_DUPFD_CLOEXEC, 3);
  check (saved_input >= 0 || errno == EBADF, "stdin_save_or_original_closed");
  close (STDIN_FILENO);
  int zero = gold_acl_open_bound (AT_FDCWD, "source", &source, false);
  check (zero == STDIN_FILENO, "descriptor_zero_is_valid");
  check (gold_acl_qset_fd (zero, 0700) == 0, "descriptor_zero_set_acl");
  check (close (zero) == 0, "descriptor_zero_close");
  if (saved_input >= 0)
    {
      check (dup2 (saved_input, STDIN_FILENO) == STDIN_FILENO, "stdin_restore");
      check (close (saved_input) == 0, "saved_stdin_close");
    }

  check (chmod ("destination", 0300) == 0, "write_search_only_destination");
  errno = 0;
  int forbidden_read = open ("destination", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (forbidden_read >= 0)
    close (forbidden_read);
  check (forbidden_read == -1 && errno == EACCES,
         "actual_directory_read_permission_denied");
  check (stat ("source", &source) == 0 && stat ("destination", &destination) == 0,
         "current_owned_identity_stats");
  held.source = gold_acl_open_bound (AT_FDCWD, "source", &source, false);
  held.destination = gold_acl_open_bound (AT_FDCWD, "destination", &destination,
                                         false);
  check (held.source >= 0 && held.destination >= 0,
         "opath_requires_no_directory_read_permission");
  check (gold_acl_chown_fd (held.destination, (uid_t) -1, (gid_t) -1) == 0,
         "ownership_operation_on_same_handle");

  acl_t access = acl_from_text ("u::rwx,u:65534:r-x,g::---,m::r-x,o::---");
  acl_t defaults = acl_from_text ("u::rwx,u:65534:rwx,g::---,m::rwx,o::---");
  check (access != NULL && defaults != NULL, "owned_acl_values_parse");
  check (acl_set_file_at (held.source, "", AT_EMPTY_PATH, ACL_TYPE_ACCESS,
                         access) == 0
         && acl_set_file_at (held.source, "", AT_EMPTY_PATH, ACL_TYPE_DEFAULT,
                            defaults) == 0, "owned_source_acl_values_set");
  check (fstat (held.source, &source) == 0, "actual_source_mode_after_acl");

  /* Deterministic, benign owned-name replacement; the metadata operation must
     remain on its admitted object rather than the newly created decoy name. */
  check (rename ("destination", "destination-held") == 0
         && mkdir ("destination", 0700) == 0, "owned_name_replacement");
  check (gold_acl_qcopy_fd ("source", held.source, "destination",
                            held.destination, source.st_mode) == 0,
         "copy_acl_uses_held_destination");
  acl_t observed_access = get_acl (held.destination, ACL_TYPE_ACCESS);
  acl_t observed_default = get_acl (held.destination, ACL_TYPE_DEFAULT);
  check (acl_cmp (access, observed_access) == 0
         && acl_cmp (defaults, observed_default) == 0,
         "actual_access_and_default_acl_preserved");
  check (stat ("destination", &destination) == 0
         && (destination.st_mode & 07777) == 0700,
         "decoy_destination_not_modified");

  check (gold_acl_qset_fd (held.destination, 0700) == 0,
         "permission_only_mode_set");
  acl_t still_default = get_acl (held.destination, ACL_TYPE_DEFAULT);
  check (acl_cmp (still_default, defaults) == 0,
         "no_implicit_directory_type_or_default_deletion");
  check (gold_acl_qset_fd (held.destination, S_IFDIR | 0700) == 0,
         "explicit_directory_mode_set");
  acl_t empty_default = get_acl (held.destination, ACL_TYPE_DEFAULT);
  check (acl_entries (empty_default) == 0,
         "explicit_directory_default_deletion_is_empty_path");

  static char const value[] = "owned-public-fixture";
  check (gold_acl_setxattrat_compat (held.source, "", AT_EMPTY_PATH,
                                    "user.gold-owned", value, sizeof value, 0)
         == 0, "owned_general_xattr_set");
  check (gold_acl_attr_copy_fd ("source", held.source, "destination",
                                held.destination, NULL, NULL) == 0,
         "general_xattr_copy_uses_same_held_objects");
  char actual[sizeof value];
  check (gold_acl_getxattrat_compat (held.destination, "", AT_EMPTY_PATH,
                                    "user.gold-owned", actual, sizeof actual)
         == sizeof actual && memcmp (actual, value, sizeof actual) == 0,
         "general_xattr_value_preserved");
  acl_free (empty_default); acl_free (still_default);
  acl_free (observed_default); acl_free (observed_access);
  acl_free (defaults); acl_free (access);
  /* Success is reported only after real checked removal and FD finalizers.
     atexit cleanup remains best-effort failure handling, never pass evidence. */
  checked_cleanup ();
  puts ("owned_fd_acl_fixture_passed;NFSv4_real_filesystem_NOT_EXERCISED;security_accepted=false");
  printf ("actual_assertions=%u\n", checks);
  return 0;
}
