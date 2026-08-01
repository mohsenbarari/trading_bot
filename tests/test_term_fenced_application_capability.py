"""Adversarial tests for non-authorizing term-fenced source evidence."""

from __future__ import annotations

import copy
import hashlib
from unittest import TestCase

from core import term_fenced_application_capability as subject


class TermFencedApplicationCapabilityTests(TestCase):
    def _document(self, **changes: object) -> bytes:
        value: dict[str, object] = {
            "schema": subject.TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA,
            "status": subject.TERM_FENCED_APPLICATION_CAPABILITY_STATUS,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "source_files": {
                name: hashlib.sha256(name.encode("ascii")).hexdigest()
                for name in subject.TERM_FENCED_APPLICATION_CAPABILITY_FILES
            },
            "capabilities": list(subject.TERM_FENCED_APPLICATION_CAPABILITIES),
            "writer_authorized": False,
            "promotion_authorized": False,
            "deployment_authorized": False,
            "execution_authorized": False,
            "full_matrix_authorized": False,
            "full_matrix_executed": False,
        }
        value.update(changes)
        return subject.canonical_term_fenced_application_capability_json_bytes(value)

    def test_valid_evidence_is_non_authorizing_and_yields_exact_image_labels(self) -> None:
        document = self._document()
        evidence = subject.verify_term_fenced_application_capability(document)
        self.assertEqual("a" * 40, evidence.release_sha)
        self.assertFalse(evidence.writer_authorized)
        self.assertFalse(evidence.deployment_authorized)
        self.assertIs(evidence, subject.require_verified_term_fenced_application_capability(evidence))
        labels = subject.expected_term_fenced_image_labels(evidence)
        self.assertEqual("a" * 40, labels["org.opencontainers.image.revision"])
        self.assertEqual("b" * 40, labels[subject.TERM_FENCED_IMAGE_LABEL_SOURCE_TREE])
        self.assertEqual(hashlib.sha256(document).hexdigest(), labels[subject.TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256])
        subject.verify_term_fenced_image_labels(labels, evidence=evidence)

    def test_authorization_tag_noncanonical_and_incomplete_file_set_are_refused(self) -> None:
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "AUTHORIZATION_FORBIDDEN",
        ):
            subject.verify_term_fenced_application_capability(
                self._document(writer_authorized=True)
            )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "CANONICAL_REQUIRED",
        ):
            subject.verify_term_fenced_application_capability(
                self._document().replace(b',"status"', b', "status"')
            )
        files = {
            name: hashlib.sha256(name.encode("ascii")).hexdigest()
            for name in subject.TERM_FENCED_APPLICATION_CAPABILITY_FILES
        }
        files.pop(next(iter(files)))
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "SOURCE_FILES_INVALID",
        ):
            subject.verify_term_fenced_application_capability(
                self._document(source_files=files)
            )

    def test_forgery_copy_mutation_and_image_label_mismatch_are_refused(self) -> None:
        evidence = subject.verify_term_fenced_application_capability(self._document())
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.copy(evidence)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            evidence.__reduce_ex__(4)
        object.__setattr__(evidence, "release_sha", "0" * 40)
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "UNVERIFIED",
        ):
            subject.require_verified_term_fenced_application_capability(evidence)

        fresh = subject.verify_term_fenced_application_capability(self._document())
        labels = subject.expected_term_fenced_image_labels(fresh)
        labels[subject.TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256] = "0" * 64
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "IMAGE_LABEL_MISMATCH",
        ):
            subject.verify_term_fenced_image_labels(labels, evidence=fresh)

    def test_duplicate_field_is_refused(self) -> None:
        with self.assertRaisesRegex(
            subject.TermFencedApplicationCapabilityError,
            "DUPLICATE_FIELD",
        ):
            subject.verify_term_fenced_application_capability(
                b'{"schema":"x","schema":"x"}'
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
