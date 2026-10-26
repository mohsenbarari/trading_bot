/* Positive owned-object API compatibility only. Root/CI compiles this after
   source review inside the disposable Trixie candidate, uid1001/network-none.
   This is not a race/exploit fixture or proof of all tar callers. */
#define _GNU_SOURCE 1
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/acl.h>
#include <acl/libacl.h>
#include <fcntl.h>
#include <unistd.h>
#include <assert.h>
#include <signal.h>
#include <stdio.h>
#ifdef NDEBUG
# error Positive fixture assertions must not be disabled
#endif

static void same_inode (int fd, struct stat const *expected)
{
  struct stat observed;
  assert (fstat (fd, &observed) == 0);
  assert (observed.st_dev == expected->st_dev);
  assert (observed.st_ino == expected->st_ino);
  assert ((observed.st_mode & S_IFMT) == (expected->st_mode & S_IFMT));
}

static void roundtrip (int fd, acl_type_t type, acl_t value)
{
  assert (acl_set_file_at (fd, "", AT_EMPTY_PATH, type, value) == 0);
  acl_t got = acl_get_file_at (fd, "", AT_EMPTY_PATH, type);
  assert (got != NULL && acl_cmp (got, value) == 0);
  assert (acl_free (got) == 0);
}

int main (void)
{
  assert (getuid () == 1001 && geteuid () == 1001);
  alarm (15);
  umask (077);
  /* EXCL directory creation: no existing path or production operand accepted. */
  assert (mkdir ("/work/acl-positive-fixture", 0700) == 0);
  int root = open ("/work/acl-positive-fixture",
                   O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  assert (root >= 0);
  int file = openat (root, "ordinary-file",
                     O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
  assert (file >= 0 && write (file, "OWNED PUBLIC DATA\n", 18) == 18);
  struct stat file_identity, dir_identity, fifo_identity;
  assert (fstat (file, &file_identity) == 0);
  int file_path = openat (root, "ordinary-file", O_PATH | O_NOFOLLOW | O_CLOEXEC);
  assert (file_path >= 0);
  same_inode (file_path, &file_identity);

  assert (mkdirat (root, "ordinary-directory", 0700) == 0);
  int directory = openat (root, "ordinary-directory",
                          O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  assert (directory >= 0 && fstat (directory, &dir_identity) == 0);
  assert (S_ISDIR (dir_identity.st_mode));

  assert (mkfifoat (root, "ordinary-fifo", 0600) == 0);
  /* No FIFO payload descriptor is opened: O_PATH cannot block on a peer. */
  int fifo = openat (root, "ordinary-fifo", O_PATH | O_NOFOLLOW | O_CLOEXEC);
  assert (fifo >= 0 && fstat (fifo, &fifo_identity) == 0);
  assert (S_ISFIFO (fifo_identity.st_mode));

  acl_t access = acl_from_text (
    "user::rw-,user:12345:r--,group::---,mask::r--,other::---");
  acl_t defaults = acl_from_text (
    "user::rwx,user:12345:r-x,group::---,mask::r-x,other::---");
  assert (access != NULL && defaults != NULL);
  assert (acl_valid (access) == 0 && acl_valid (defaults) == 0);
  roundtrip (file, ACL_TYPE_ACCESS, access);
  roundtrip (file_path, ACL_TYPE_ACCESS, access);
  roundtrip (fifo, ACL_TYPE_ACCESS, access);
  roundtrip (directory, ACL_TYPE_ACCESS, defaults);
  roundtrip (directory, ACL_TYPE_DEFAULT, defaults);
  assert (acl_extended_file_at (file_path, "", AT_EMPTY_PATH) == 1);
  assert (acl_extended_file_at (directory, "", AT_EMPTY_PATH) == 1);
  assert (acl_extended_file_at (fifo, "", AT_EMPTY_PATH) == 1);

  int inherited = openat (directory, "inherited",
                          O_CREAT | O_EXCL | O_RDWR | O_NOFOLLOW | O_CLOEXEC,
                          0600);
  assert (inherited >= 0);
  assert (acl_extended_file_at (inherited, "", AT_EMPTY_PATH) == 1);
  assert (acl_delete_def_file_at (directory, "", AT_EMPTY_PATH) == 0);
  acl_t empty = acl_get_file_at (directory, "", AT_EMPTY_PATH, ACL_TYPE_DEFAULT);
  assert (empty != NULL && acl_entries (empty) == 0);
  assert (acl_free (empty) == 0);
  same_inode (file_path, &file_identity);
  same_inode (directory, &dir_identity);
  same_inode (fifo, &fifo_identity);
  assert (acl_free (access) == 0 && acl_free (defaults) == 0);
  assert (close (inherited) == 0);
  assert (close (fifo) == 0 && close (directory) == 0);
  assert (close (file_path) == 0 && close (file) == 0 && close (root) == 0);
  alarm (0);
  puts ("{\"schema\":\"ordinary_acl_fd_compatibility/1\",\"status\":\"PASS\",\"regular_file\":true,\"directory_default\":true,\"fifo_no_payload_open\":true,\"actual_runtime_inode_checked\":true,\"all_tar_callsites_proven\":false,\"exploit_reproduction_performed\":false,\"production_data_accessed\":false,\"security_accepted\":false}");
  return 0;
}
