/* Exercise the actual patched static cp parent frontend and real copy worker.
   No replacement syscalls, LD_PRELOAD, malformed ACLs, or privileged targets.
   SOURCE ONLY until the admitted vendor CI compiles and runs this companion.
   SPDX-License-Identifier: GPL-3.0-or-later */
#define main gold_parent_unused_cp_main
#include "src/cp.c"
#undef main
#include <sys/resource.h>
#include <sys/acl.h>
#include <acl/libacl.h>

static char parent_work[] = "gold-parent-owned-XXXXXX";
static int parent_cwd = -1, parent_target = -1, parent_input = -1;
static int parent_fillers[64];
static size_t parent_fill_count;
static struct dir_attr *parent_active;
static bool parent_entered;
static unsigned parent_checks, parent_cases;

static void
parent_failure_cleanup (void)
{
  while (parent_active)
    {
      struct dir_attr *p = parent_active;
      parent_active = p->next;
      gold_acl_close_handles (&p->handles);
      free (p);
    }
  while (parent_fill_count)
    {
      int fd = parent_fillers[--parent_fill_count];
      if (fd >= 0) close (fd);
    }
  if (parent_target >= 0) close (parent_target);
  if (parent_input >= 0)
    { dup2 (parent_input, 0); close (parent_input); }
  if (parent_entered)
    {
      char const *names[] = { "out", "pressure1", "pressure2", "failed" };
      for (size_t i = 0; i < sizeof names / sizeof *names; i++)
        {
          char path[128];
          snprintf (path, sizeof path, "%s/source/a", names[i]); chmod (path, 0700);
          snprintf (path, sizeof path, "%s/source/a/item", names[i]); unlink (path);
          snprintf (path, sizeof path, "%s/source/a", names[i]); rmdir (path);
          snprintf (path, sizeof path, "%s/source", names[i]); chmod (path, 0700); rmdir (path);
          rmdir (names[i]);
        }
      chmod ("source/a", 0700); unlink ("source/a/item");
      rmdir ("source/a"); rmdir ("source");
    }
  if (parent_cwd >= 0)
    { if (fchdir (parent_cwd) == 0) rmdir (parent_work); close (parent_cwd); }
}

static void
parent_need (bool value, char const *code)
{
  if (!value || parent_checks >= 512)
    {
      fprintf (stderr, "owned_parent_refused:%s:errno=%d\n", code, errno);
      exit (1);
    }
  parent_checks++;
}

static void
parent_close_target (void)
{
  int fd = parent_target; parent_target = -1;
  parent_need (close (fd) == 0, "target_close");
  errno = 0;
  parent_need (fcntl (fd, F_GETFD) == -1 && errno == EBADF,
               "target_physically_closed");
}

static void
parent_drain (void)
{
  while (parent_active)
    {
      struct dir_attr *p = parent_active;
      int source_fd = p->handles.source, dest_fd = p->handles.destination;
      parent_active = p->next;
      gold_acl_close_handles (&p->handles);
      parent_need (p->handles.source == -1 && p->handles.destination == -1,
                   "pair_retired");
      if (source_fd >= 0)
        {
          errno = 0;
          parent_need (fcntl (source_fd, F_GETFD) == -1 && errno == EBADF,
                       "source_physically_closed");
        }
      if (dest_fd >= 0)
        {
          errno = 0;
          parent_need (fcntl (dest_fd, F_GETFD) == -1 && errno == EBADF,
                       "destination_physically_closed");
        }
      free (p);
    }
}

static void
parent_remove_destination (char const *name, bool item)
{
  char path[128]; struct stat absent;
  snprintf (path, sizeof path, "%s/source/a", name);
  if (lstat (path, &absent) == 0)
    {
      parent_need (S_ISDIR (absent.st_mode), "owned_cleanup_directory_type");
      parent_need (chmod (path, 0700) == 0, "owned_cleanup_search");
      if (item)
        { snprintf (path, sizeof path, "%s/source/a/item", name);
          parent_need (unlink (path) == 0, "owned_leaf_removed"); }
      snprintf (path, sizeof path, "%s/source/a", name);
      parent_need (rmdir (path) == 0, "owned_inner_removed");
    }
  snprintf (path, sizeof path, "%s/source", name);
  if (lstat (path, &absent) == 0)
    { parent_need (S_ISDIR (absent.st_mode), "owned_prefix_type");
      parent_need (chmod (path, 0700) == 0 && rmdir (path) == 0,
                   "owned_prefix_removed"); }
  parent_need (rmdir (name) == 0, "owned_destination_removed");
  errno = 0;
  parent_need (lstat (name, &absent) == -1 && errno == ENOENT,
               "owned_destination_absent");
}

static void
parent_prepare (char const *name)
{
  parent_need (mkdir (name, 0700) == 0, "destination_create");
  parent_target = open (name, O_PATH | O_DIRECTORY | O_CLOEXEC);
  parent_need (parent_target >= 0, "destination_hold");
}

static void
parent_case (char const *name, bool missing_leaf, bool fd_zero)
{
  parent_prepare (name);
  if (fd_zero)
    {
      parent_input = fcntl (0, F_DUPFD_CLOEXEC, 3);
      parent_need (parent_input >= 0 || errno == EBADF, "stdin_saved_or_absent");
      close (0);
    }
  char destination[128];
  snprintf (destination, sizeof destination, "%s/source/a/%s", name,
            missing_leaf ? "missing" : "item");
  size_t offset = strlen (name) + 1;
  struct cp_options x; cp_option_init (&x);
  x.dereference = DEREF_ALWAYS; x.preserve_mode = true;
  bool new_dst;
  parent_need (make_dir_parents_private (destination, offset, parent_target,
                                        NULL, &parent_active, &new_dst, &x),
               "actual_make_dir_parents_private");
  unsigned pairs = 0; bool saw_zero = false;
  for (struct dir_attr *p = parent_active; p; p = p->next)
    {
      pairs++;
      parent_need (parent_acl_handle_matches (p->handles.source, &p->st)
                   && parent_acl_handle_matches (p->handles.destination,
                                                  &p->destination_st),
                   "actual_retained_directory_pair");
      parent_need ((fcntl (p->handles.source, F_GETFD) & FD_CLOEXEC)
                   && (fcntl (p->handles.destination, F_GETFD) & FD_CLOEXEC)
                   && (fcntl (p->handles.source, F_GETFL) & O_PATH)
                   && (fcntl (p->handles.destination, F_GETFL) & O_PATH),
                   "actual_OPATH_CLOEXEC");
      saw_zero |= p->handles.source == 0;
    }
  parent_need (pairs == 2 && (!fd_zero || saw_zero), "complete_prefix_and_FD0");
  bool into_self;
  bool copied = copy (missing_leaf ? "source/a/missing" : "source/a/item",
                      destination, parent_target, destination + offset,
                      new_dst, &x, &into_self, NULL);
  parent_need (copied == !missing_leaf, "actual_copy_status");
  parent_need (re_protect (destination, destination + offset, parent_target,
                          destination + offset, parent_active, &x),
               "actual_re_protect");
  acl_t reference = acl_get_file ("source/a", ACL_TYPE_ACCESS);
  char path[128]; snprintf (path, sizeof path, "%s/source/a", name);
  acl_t actual = acl_get_file (path, ACL_TYPE_ACCESS);
  parent_need (reference && actual && acl_cmp (reference, actual) == 0,
               "actual_parent_access_acl");
  acl_free (reference); acl_free (actual);
  reference = acl_get_file ("source/a", ACL_TYPE_DEFAULT);
  actual = acl_get_file (path, ACL_TYPE_DEFAULT);
  parent_need (reference && actual && acl_cmp (reference, actual) == 0,
               "actual_parent_default_acl");
  acl_free (reference); acl_free (actual);
  parent_drain ();
  if (fd_zero && parent_input >= 0)
    { int fd = parent_input; parent_input = -1;
      parent_need (dup2 (fd, 0) == 0 && close (fd) == 0, "stdin_restored"); }
  parent_close_target ();
  parent_remove_destination (name, !missing_leaf);
  parent_cases++;
}

static void
parent_pressure_case (char const *name, unsigned slots)
{
  parent_prepare (name);
  struct rlimit prior, limited;
  parent_need (getrlimit (RLIMIT_NOFILE, &prior) == 0 && prior.rlim_cur >= 64,
               "actual_fd_limit_admitted");
  limited = prior; limited.rlim_cur = 64;
  parent_need (setrlimit (RLIMIT_NOFILE, &limited) == 0, "bounded_fd_limit");
  while (parent_fill_count < 64)
    {
      int fd = open ("/dev/null", O_RDONLY | O_CLOEXEC);
      if (fd < 0) { parent_need (errno == EMFILE, "actual_fd_exhaustion"); break; }
      parent_fillers[parent_fill_count++] = fd;
    }
  parent_need (parent_fill_count >= slots && parent_fill_count < 64,
               "finite_owned_fillers");
  for (unsigned i = 0; i < slots; i++)
    { int fd = parent_fillers[--parent_fill_count];
      parent_need (close (fd) == 0, "bounded_admission_slot"); }
  char destination[128]; snprintf (destination, sizeof destination,
                                  "%s/source/a/item", name);
  struct cp_options x; cp_option_init (&x);
  x.dereference = DEREF_ALWAYS; x.preserve_mode = true;
  bool new_dst;
  bool made = make_dir_parents_private (destination, strlen (name) + 1,
                                        parent_target, NULL, &parent_active,
                                        &new_dst, &x);
  parent_need (!made && errno == EMFILE && parent_active != NULL,
               "actual_partial_prefix_EMFILE_refusal");
  bool held = false;
  for (struct dir_attr *p = parent_active; p; p = p->next)
    held |= p->handles.source >= 0 || p->handles.destination >= 0;
  parent_need (held, "actual_partial_acquisition_retained");
  while (parent_fill_count)
    parent_need (close (parent_fillers[--parent_fill_count]) == 0,
                 "owned_filler_closed");
  parent_need (setrlimit (RLIMIT_NOFILE, &prior) == 0, "original_fd_limit_restored");
  parent_drain (); parent_close_target ();
  parent_remove_destination (name, false);
  parent_cases++;
}

int
main (int argc, char **argv)
{
  if (argc != 2 || strcmp (argv[1], "--run-owned-parent-acl"))
    { puts ("UNARMED: no fixture created"); return 0; }
  parent_need (getuid () == 10001 && geteuid () == 10001
               && getgid () == 10001 && getegid () == 10001,
               "genuine_UID10001");
  parent_need (getenv ("LD_PRELOAD") == NULL
               && getenv ("LD_LIBRARY_PATH") == NULL, "ordinary_loader");
  set_program_name (argv[0]);
  parent_cwd = open (".", O_PATH | O_DIRECTORY | O_CLOEXEC);
  parent_need (parent_cwd >= 0 && mkdtemp (parent_work), "owned_root");
  atexit (parent_failure_cleanup);
  parent_need (chdir (parent_work) == 0, "owned_enter"); parent_entered = true;
  parent_need (mkdir ("source", 0750) == 0 && mkdir ("source/a", 0750) == 0,
               "owned_source_directories");
  int fd = open ("source/a/item", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0640);
  parent_need (fd >= 0 && write (fd, "owned\n", 6) == 6 && close (fd) == 0,
               "ordinary_source_file");
  acl_t reference = acl_from_text ("u::rwx,u:10002:r-x,g::r-x,m::r-x,o::---");
  parent_need (reference && acl_set_file ("source/a", ACL_TYPE_ACCESS, reference) == 0
               && acl_set_file ("source/a", ACL_TYPE_DEFAULT, reference) == 0,
               "valid_owned_source_ACLs"); acl_free (reference);
  parent_case ("out", false, true);
  parent_pressure_case ("pressure1", 1);
  parent_pressure_case ("pressure2", 2);
  parent_case ("failed", true, false);
  parent_need (unlink ("source/a/item") == 0 && rmdir ("source/a") == 0
               && rmdir ("source") == 0, "all_owned_source_removed");
  parent_need (fchdir (parent_cwd) == 0, "original_cwd_restored");
  parent_entered = false;
  parent_need (rmdir (parent_work) == 0, "owned_root_removed");
  fd = parent_cwd; parent_cwd = -1;
  parent_need (close (fd) == 0, "cwd_closed"); errno = 0;
  parent_need (fcntl (fd, F_GETFD) == -1 && errno == EBADF, "cwd_physically_closed");
  printf ("{\"schema\":\"ordinary_cp_parent_frontend/1\",\"status\":\"PASS\",\"actual_uid\":10001,\"actual_gid\":10001,\"actual_assertions\":%u,\"actual_case_count\":%u,\"actual_frontend_and_copy_called\":true,\"actual_OPATH_pairs\":true,\"source_fd_zero\":true,\"actual_EMFILE_partial_prefixes\":2,\"physical_pair_cleanup_verified\":true,\"owned_cleanup_complete\":true,\"skip_count\":0,\"security_accepted\":false}\n", parent_checks, parent_cases);
  return fflush (stdout) == 0 ? 0 : 1;
}
