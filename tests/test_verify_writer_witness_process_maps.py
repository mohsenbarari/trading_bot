import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from scripts.verify_writer_witness_process_maps import (
    ProcessMapsError,
    attest_process_maps,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = ROOT / "deploy/writer-witness/python-runtime.json"


class WriterWitnessProcessMapsTests(unittest.TestCase):
    def _spawn(self, source: str, *arguments: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                "-c",
                source,
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop, process)
        return process

    @staticmethod
    def _stop(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def _attest(self, process: subprocess.Popen[str], venv: Path):
        import hashlib

        return attest_process_maps(
            pid=process.pid,
            venv=venv,
            system_runtime_manifest=RUNTIME_MANIFEST,
            expected_manifest_sha256=hashlib.sha256(RUNTIME_MANIFEST.read_bytes()).hexdigest(),
        )

    def test_minimal_pinned_python_maps_only_the_attested_native_closure(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-maps-venv-") as directory:
            venv = Path(directory).resolve()
            process = self._spawn("import time; print('ready', flush=True); time.sleep(30)")
            self.assertEqual(process.stdout.readline().strip(), "ready")
            result = self._attest(process, venv)
            self.assertEqual(result["process_maps_attested"], "yes")
            self.assertGreaterEqual(result["mapped_native_object_count"], 2)

    def test_unattested_copied_shared_object_is_rejected(self):
        runtime = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
        source = next(
            Path(item["path"])
            for item in runtime["elf_objects"]
            if "libz.so" in str(item["path"])
        )
        with tempfile.TemporaryDirectory(prefix="writer-witness-maps-venv-") as directory:
            venv = Path(directory).resolve()
            outside = venv.parent / f"{venv.name}-unattested.so"
            shutil.copyfile(source, outside)
            self.addCleanup(outside.unlink, missing_ok=True)
            process = self._spawn(
                "import ctypes,sys,time; ctypes.CDLL(sys.argv[1]); "
                "print('ready', flush=True); time.sleep(30)",
                str(outside),
            )
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(ProcessMapsError, "unattested native object"):
                self._attest(process, venv)


if __name__ == "__main__":
    unittest.main()
