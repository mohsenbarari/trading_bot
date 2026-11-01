"""Offline regression coverage for public APT wire from CI run41322957404.

These exact stdout fixtures are real failed-CI evidence, not reconstructed
solver output. Rollback plans below are explicitly derived parser fixtures;
no APT command, package install, operational configuration or Env is read.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import os
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/ubuntu_family6_rehearsal/worker.py"
FIXTURES = Path(__file__).parent / "fixtures/ubuntu_family6_solver_wire"
CASES = {
    "ubuntu239": ("2.39-0ubuntu8.9", 1168,
                  "cf69045e2a4812f0965f1dc87c833c54fe0aa2c803e10712ee7dbf3b456c7bd3"),
    "ubuntu243": ("2.43-2ubuntu2.4", 1145,
                  "09d4ac4b1c95a74513d629b9f9f38a0c93106b052086ed401d5c770c6671cc7d"),
}

def bounded_file(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        size = os.fstat(fd).st_size
        if not 0 < size <= maximum:
            raise AssertionError("bounded public test input")
        raw = os.pread(fd, size + 1, 0)
        if len(raw) != size:
            raise AssertionError("public test input changed")
        return raw
    finally:
        os.close(fd)


def load_worker():
    module = ModuleType("_offline_family6_solver_wire")
    module.__file__ = str(SOURCE)
    exec(compile(bounded_file(SOURCE, 131072), str(SOURCE), "exec"), module.__dict__)
    return module


class Family6SolverWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()
        cls.raw = {}
        for profile, (_, size, digest) in CASES.items():
            raw = bounded_file(FIXTURES / (profile + "-actual-forward.txt"), 4096)
            if (len(raw), sha256(raw).hexdigest()) != (size, digest):
                raise AssertionError("actual CI fixture identity")
            cls.raw[profile] = raw

    def solve(self, profile, raw=None, version=None):
        version = version or CASES[profile][0] + "+gold1"
        return self.worker.solver(self.raw[profile] if raw is None else raw,
                                  self.worker.FAMILIES[profile], version)

    def changed(self, profile, kind, transform):
        lines = self.raw[profile].decode().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith(kind + " "))
        lines[index] = transform(lines[index])
        return ("\n".join(lines) + "\n").encode()

    def core(self, row):
        return row[:row.rfind(")") + 1]

    def refused(self, profile, raw):
        with self.assertRaises((RuntimeError, UnicodeError)):
            self.solve(profile, raw)

    def test_actual_noble_forward(self):
        self.assertEqual(self.solve("ubuntu239"),
                         dict.fromkeys(self.worker.FAMILIES["ubuntu239"], CASES["ubuntu239"][0] + "+gold1"))

    def test_actual_resolute_forward_including_conf_status(self):
        self.assertIn(b"Conf libc-gconv-modules-extra", self.raw["ubuntu243"])
        self.assertIn(b") [libc6:amd64 ]", self.raw["ubuntu243"])
        self.assertEqual(self.solve("ubuntu243"),
                         dict.fromkeys(self.worker.FAMILIES["ubuntu243"], CASES["ubuntu243"][0] + "+gold1"))

    def test_derived_rollback_plans_are_exact_six(self):
        for profile, (old, _, _) in CASES.items():
            with self.subTest(profile=profile):
                new = old + "+gold1"
                derived = self.raw[profile].replace((" [" + old + "]").encode(),
                                                    (" [" + new + "]").encode())
                derived = derived.replace(("(" + new + " ").encode(), ("(" + old + " ").encode())
                self.assertEqual(self.solve(profile, derived, old),
                                 dict.fromkeys(self.worker.FAMILIES[profile], old))

    def test_known_well_formed_annotations_are_only_status(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                for annotation in (" []", " [libc6:amd64]", " [libc6:amd64 ]",
                                   " [libc6:amd64 libc6-dev:amd64 ]", " [locales:all ]"):
                    with self.subTest(profile=profile, kind=kind, annotation=annotation):
                        raw = self.changed(profile, kind,
                                           lambda row: self.core(row) + annotation)
                        self.assertEqual(set(self.solve(profile, raw)), set(self.worker.FAMILIES[profile]))

    def test_unknown_or_malformed_status_never_becomes_install_authority(self):
        invalid = (" [postgresql:amd64 ]", " [libc6:arm64 ]", " [libc6:i386 ]",
                   " [libc6:all ]", " [locales:amd64 ]", " [libc6 ]", " [libc6: ]",
                   " [ ]", " [ libc6:amd64 ]", " [libc6:amd64  libc6-dev:amd64 ]",
                   " [libc6:amd64 libc6:amd64 ]", " [libc6:amd64;echo ]",
                   " [libc6:amd64\t]", " [[libc6:amd64]]", " [libc6:amd64", " [] []",
                   " []junk", " trailing", " (unknown)")
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                for annotation in invalid:
                    with self.subTest(profile=profile, kind=kind, annotation=annotation):
                        self.refused(profile, self.changed(profile, kind,
                                     lambda row: self.core(row) + annotation))

    def test_missing_inst_cannot_be_supplied_by_a_status_annotation(self):
        for profile in CASES:
            with self.subTest(profile=profile):
                raw = b"\n".join(line for line in self.raw[profile].splitlines()
                                  if not line.startswith(b"Inst libc6-dev ")) + b"\n"
                self.assertIn(b"Conf libc6-dev ", raw)
                self.refused(profile, raw)

    def test_missing_conf_is_rejected(self):
        for profile in CASES:
            with self.subTest(profile=profile):
                self.refused(profile, b"\n".join(line for line in self.raw[profile].splitlines()
                                                if not line.startswith(b"Conf libc6 ")))

    def test_duplicate_inst_and_conf_are_rejected(self):
        for profile in CASES:
            for kind in (b"Inst ", b"Conf "):
                with self.subTest(profile=profile, kind=kind):
                    duplicate = next(line for line in self.raw[profile].splitlines() if line.startswith(kind))
                    self.refused(profile, self.raw[profile] + b"\n" + duplicate + b"\n")

    def test_nonfamily_action_is_rejected(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                with self.subTest(profile=profile, kind=kind):
                    version = CASES[profile][0] + "+gold1"
                    self.refused(profile, self.raw[profile] +
                                 ("\n" + kind + " unrelated (" + version + " local-deb [amd64])\n").encode())

    def test_any_removal_or_purge_is_rejected(self):
        for profile in CASES:
            for action in (b"Remv libc6 [prior]", b"Purg ordinary-helper [prior]"):
                with self.subTest(profile=profile, action=action):
                    self.refused(profile, self.raw[profile] + b"\n" + action + b"\n")

    def test_wrong_target_version_is_rejected(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                with self.subTest(profile=profile, kind=kind):
                    version = CASES[profile][0] + "+gold1"
                    self.refused(profile, self.changed(profile, kind,
                                 lambda row: row.replace("(" + version, "(" + version + "-other", 1)))

    def test_unsupported_main_package_architecture_is_rejected(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                with self.subTest(profile=profile, kind=kind):
                    self.refused(profile, self.changed(profile, kind,
                                 lambda row: re.sub(r"^(Inst|Conf) ([^ ]+)", r"\1 \2:arm64", row)))

    def test_unsupported_or_wrong_parenthesized_architecture_is_rejected(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                with self.subTest(profile=profile, kind=kind):
                    self.refused(profile, self.changed(profile, kind,
                                 lambda row: row.replace("[amd64])", "[arm64])", 1)))
            with self.subTest(profile=profile, package="locales"):
                self.refused(profile, self.raw[profile].replace(b"[all])", b"[amd64])"))

    def test_unknown_bracket_or_parenthesis_shapes_are_rejected(self):
        for profile in CASES:
            for kind in ("Inst", "Conf"):
                for mutate in (lambda row: self.core(row) + " ]",
                               lambda row: row.replace("local-deb", "local(deb)", 1),
                               lambda row: self.core(row).replace("[amd64]", "[amd64] [all]")):
                    with self.subTest(profile=profile, kind=kind, mutate=mutate):
                        self.refused(profile, self.changed(profile, kind, mutate))

    def test_invalid_utf8_is_rejected(self):
        for profile in CASES:
            with self.subTest(profile=profile):
                self.refused(profile, self.raw[profile] + b"\xff")

    def test_profile_family_and_requested_version_remain_bound(self):
        with self.assertRaises(RuntimeError):
            self.worker.solver(self.raw["ubuntu239"], self.worker.FAMILIES["ubuntu243"], CASES["ubuntu239"][0] + "+gold1")
        with self.assertRaises(RuntimeError):
            self.solve("ubuntu239", version=CASES["ubuntu239"][0])

    def test_empty_or_headers_only_plan_is_rejected(self):
        for raw in (b"", b"Reading package lists...\n0 upgraded.\n"):
            with self.subTest(raw=raw):
                self.refused("ubuntu239", raw)


if __name__ == "__main__":
    unittest.main()
