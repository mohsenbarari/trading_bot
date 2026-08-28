from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
import sqlite3
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "apps" / "coin_rate_estimator"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(APP_ROOT))

from condition_review_page import (  # noqa: E402
    render_condition_review_page,
)
from core.market_intelligence.coin_condition_review import (
    ConditionReviewError,
    ConditionReviewService,
    ConditionReviewStore,
    condition_sample_digest,
)
from core.market_intelligence.coin_offer_conditions import (
    masked_condition_model_text,
)
from scripts.export_coin_condition_owner_reviews import export as export_reviews
from scripts.install_coin_condition_review_assets import install as install_assets


def _sample(
    *,
    text: str = "ف امام 10 تا 120000 فیش تا 2",
    event_time: str = "2026-08-20T08:00:00Z",
) -> dict[str, str]:
    digest = condition_sample_digest(
        group_code="group_1",
        event_time_utc=event_time,
        settlement_term="CASH",
        trade_form="PHYSICAL",
        model_text=masked_condition_model_text(text),
    )
    return {
        "sample_digest": digest,
        "group_code": "group_1",
        "event_time_utc": event_time,
        "settlement_term": "CASH",
        "trade_form": "PHYSICAL",
        "session_phase": "OPENING_FIRST_HOUR",
        "private_offer_text": text,
    }


def _pack(path: Path, samples: list[dict[str, str]]) -> Path:
    payload = {
        "schema_version": "coin-offer-condition-owner-review-v1",
        "source_fingerprint": "a" * 64,
        "selection": {"sample_count": len(samples)},
        "samples": samples,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _service(root: Path, samples: list[dict[str, str]]) -> ConditionReviewService:
    return ConditionReviewService(
        conversation_db=root / "missing-conversation.sqlite3",
        staging_db=None,
        review_db=root / "review.sqlite3",
        owner_pack_path=_pack(root / "owner-pack.json", samples),
        model_path=root / "missing-model.joblib",
    )


def test_sealed_queue_is_blind_until_owner_decision(tmp_path: Path) -> None:
    sample = _sample()
    service = _service(tmp_path, [sample])

    pending = service.list_queue(queue="SEALED")

    assert pending["progress"]["sealed_total"] == 1
    assert pending["progress"]["sealed_reviewed"] == 0
    assert pending["items"][0]["private_offer_text"] == sample["private_offer_text"]
    assert pending["items"][0]["analysis"] is None
    assert pending["items"][0]["analysis_blinded_until_review"] is True
    assert pending["claim_boundary"]["shadow_only"] is True
    assert pending["claim_boundary"]["offer_runtime_effect"] is False
    assert pending["claim_boundary"]["estimator_runtime_effect"] is False

    recorded = service.record(
        {
            "sample_digest": sample["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["PAYMENT_DEADLINE", "PAYMENT_ACCOUNT"],
            "owner_settlement": "CASH",
            "owner_condition_text": "فیش تا 2",
            "owner_deadline": "14:00",
        },
        reviewer="owner",
    )
    assert recorded["runtime_effect"] is False
    assert recorded["review_revision"] == 1

    reviewed = service.list_queue(queue="SEALED")
    item = reviewed["items"][0]
    assert item["analysis_blinded_until_review"] is False
    assert item["analysis"]["shadow_only"] is True
    assert item["review"]["owner_condition_text"] == "فیش تا 2"
    assert reviewed["progress"]["sealed_reviewed"] == 1


def test_review_store_persists_spans_not_private_offer_text(tmp_path: Path) -> None:
    sample = _sample(text="خ ربع 4 تا 51000 تک حساب")
    service = _service(tmp_path, [sample])
    service.record(
        {
            "sample_digest": sample["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["PAYMENT_ACCOUNT"],
            "owner_settlement": "CASH",
            "owner_condition_text": "تک حساب",
            "owner_deadline": "",
        },
        reviewer="owner",
    )

    database_bytes = (tmp_path / "review.sqlite3").read_bytes()
    assert "خ ربع".encode("utf-8") not in database_bytes
    assert "تک حساب".encode("utf-8") not in database_bytes
    with sqlite3.connect(tmp_path / "review.sqlite3") as connection:
        row = connection.execute(
            "SELECT owner_condition_spans_json,reviewer_digest "
            "FROM coin_offer_condition_reviews"
        ).fetchone()
    assert json.loads(row[0]) == [[17, 24]]
    assert len(row[1]) == 32


def test_review_accepts_semantic_night_account_for_raw_abbreviation(
    tmp_path: Path,
) -> None:
    sample = _sample(text="95500 خ نیم ده تا ش ح")
    service = _service(tmp_path, [sample])

    recorded = service.record(
        {
            "sample_digest": sample["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["SETTLEMENT_PROCESS"],
            "owner_settlement": "CASH",
            "owner_condition_text": "شب حساب",
            "owner_deadline": "",
        },
        reviewer="owner",
    )

    assert recorded["owner_condition_spans"] == [[18, 21]]
    reviewed = service.list_queue(queue="SEALED")["items"][0]
    assert reviewed["review"]["owner_condition_text"] == "ش ح"
    database_bytes = (tmp_path / "review.sqlite3").read_bytes()
    assert "شب حساب".encode("utf-8") not in database_bytes


@pytest.mark.parametrize(
    "entered_text",
    [
        "تک حساب؛ شب حساب",
        "تک حساب; شب حساب",
        "تک حساب\nشب حساب",
        "تک حساب | شب حساب",
        "تک حساب، شب حساب",
        "شرط خرید:\nتک\u200e حساب؛ شب\u2066 حساب",
    ],
)
def test_review_accepts_multiple_semantic_conditions_for_raw_abbreviations(
    tmp_path: Path,
    entered_text: str,
) -> None:
    sample = _sample(text="15 تا خ تک ح 190200 شب ح")
    service = _service(tmp_path, [sample])

    recorded = service.record(
        {
            "sample_digest": sample["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["PAYMENT_ACCOUNT", "SETTLEMENT_PROCESS"],
            "owner_settlement": "CASH",
            "owner_condition_text": entered_text,
            "owner_deadline": "",
        },
        reviewer="owner",
    )

    assert recorded["owner_condition_spans"] == [[8, 12], [20, 24]]
    reviewed = service.list_queue(queue="SEALED")["items"][0]
    assert reviewed["review"]["owner_condition_text"] == "تک ح | شب ح"
    database_bytes = (tmp_path / "review.sqlite3").read_bytes()
    assert "تک حساب".encode("utf-8") not in database_bytes
    assert "شب حساب".encode("utf-8") not in database_bytes


def test_review_rejects_semantic_alias_for_unrelated_family(tmp_path: Path) -> None:
    sample = _sample(text="95500 خ نیم ده تا ش ح")
    service = _service(tmp_path, [sample])

    with pytest.raises(
        ConditionReviewError,
        match="condition_review_alias_family_mismatch",
    ):
        service.record(
            {
                "sample_digest": sample["sample_digest"],
                "expected_revision": 0,
                "owner_status": "CONDITIONAL",
                "owner_families": ["PAYMENT_ACCOUNT"],
                "owner_settlement": "CASH",
                "owner_condition_text": "شب حساب",
                "owner_deadline": "",
            },
            reviewer="owner",
        )


def test_review_validation_and_optimistic_concurrency(tmp_path: Path) -> None:
    sample = _sample()
    service = _service(tmp_path, [sample])
    base = {
        "sample_digest": sample["sample_digest"],
        "expected_revision": 0,
        "owner_status": "UNCONDITIONAL",
        "owner_families": [],
        "owner_settlement": "CASH",
        "owner_condition_text": "",
        "owner_deadline": "",
    }
    service.record(base, reviewer="owner")
    with pytest.raises(ConditionReviewError, match="condition_review_revision_conflict"):
        service.record(base, reviewer="owner")
    invalid = {**base, "expected_revision": 1, "owner_condition_text": "متن بیرونی"}
    with pytest.raises(ConditionReviewError, match="condition_review_span_not_allowed"):
        service.record(invalid, reviewer="owner")


def test_live_queue_reads_private_source_without_copying_identifiers(tmp_path: Path) -> None:
    database = tmp_path / "conversation.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE messages(
                import_id INTEGER,message_id INTEGER,source_html_file TEXT,
                event_time_utc TEXT
            );
            CREATE TABLE offers(
                id INTEGER PRIMARY KEY,import_id INTEGER,message_id INTEGER,
                settlement TEXT,trade_form TEXT,source_text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO messages VALUES(1,88,'group_2','2026-08-20T09:00:00Z')"
        )
        connection.execute(
            "INSERT INTO offers VALUES(1,1,88,'TOMORROW','PHYSICAL',?)",
            ("ف امام 2 تا 120000 تک حساب",),
        )
        connection.commit()
    service = ConditionReviewService(
        conversation_db=database,
        staging_db=None,
        review_db=tmp_path / "review.sqlite3",
        owner_pack_path=None,
        model_path=None,
        # This test exercises privacy projection, not the default three-day
        # live-window boundary. Keep its fixed historical fixture in scope.
        live_recent_days=30,
    )

    result = service.list_queue(queue="LIVE")

    assert result["total"] == 1
    assert result["items"][0]["analysis"] is not None
    assert result["items"][0]["analysis"]["runtime_effect"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert '"message_id"' not in serialized
    assert "88" not in result["items"][0]["sample_digest"]


def test_reviewed_live_offer_remains_resolvable_after_live_window(tmp_path: Path) -> None:
    database = tmp_path / "conversation.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE messages(
                import_id INTEGER,message_id INTEGER,source_html_file TEXT,
                event_time_utc TEXT
            );
            CREATE TABLE offers(
                id INTEGER PRIMARY KEY,import_id INTEGER,message_id INTEGER,
                settlement TEXT,trade_form TEXT,source_text TEXT
            );
            INSERT INTO messages VALUES(
                1,99,'group_1','2026-08-10T09:00:00Z'
            );
            INSERT INTO offers VALUES(
                1,1,99,'CASH','PHYSICAL','خ ربع 3 تا 51000 تک حساب'
            );
            """
        )
        connection.commit()
    review_db = tmp_path / "review.sqlite3"
    wide = ConditionReviewService(
        conversation_db=database,
        staging_db=None,
        review_db=review_db,
        owner_pack_path=None,
        model_path=None,
        live_recent_days=30,
    )
    item = wide.list_queue(queue="LIVE")["items"][0]
    wide.record(
        {
            "sample_digest": item["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["PAYMENT_ACCOUNT"],
            "owner_settlement": "CASH",
            "owner_condition_text": "تک حساب",
            "owner_deadline": "",
        },
        reviewer="owner",
    )
    narrow = ConditionReviewService(
        conversation_db=database,
        staging_db=None,
        review_db=review_db,
        owner_pack_path=None,
        model_path=None,
        live_recent_days=1,
    )

    reviewed = narrow.list_queue(queue="REVIEWED")

    assert reviewed["total"] == 1
    assert reviewed["items"][0]["private_offer_text"].endswith("تک حساب")
    assert reviewed["items"][0]["review"]["owner_condition_text"] == "تک حساب"


def test_authenticated_page_shell_uses_safe_dynamic_text_rendering() -> None:
    body = render_condition_review_page(
        home_path="/estimate",
        data_path="/estimate/condition-review.json",
        decision_path="/estimate/condition-review/decision.json",
        logout_path="/estimate/logout",
        user_session='<owner onload="bad()">',
    ).decode("utf-8")

    assert "مجموعهٔ ۲۴۰تایی" in body
    assert "آفرهای زنده" in body
    assert "بررسی‌شده‌ها" in body
    assert "&lt;owner onload=&quot;bad()&quot;&gt;" in body
    assert "private_offer_text.slice" in body
    assert "textContent=text" in body
    assert "innerHTML=item.private_offer_text" not in body


def test_review_database_mode_is_private(tmp_path: Path) -> None:
    store = ConditionReviewStore(tmp_path / "review.sqlite3")
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_raw_free_export_keeps_sealed_and_live_truth_separate(tmp_path: Path) -> None:
    sample = _sample()
    pack_path = _pack(tmp_path / "owner-pack.json", [sample])
    service = ConditionReviewService(
        conversation_db=tmp_path / "missing.sqlite3",
        staging_db=None,
        review_db=tmp_path / "review.sqlite3",
        owner_pack_path=pack_path,
        model_path=None,
    )
    service.record(
        {
            "sample_digest": sample["sample_digest"],
            "expected_revision": 0,
            "owner_status": "CONDITIONAL",
            "owner_families": ["PAYMENT_DEADLINE"],
            "owner_settlement": "CASH",
            "owner_condition_text": "فیش تا 2",
            "owner_deadline": "14:00",
        },
        reviewer="owner",
    )
    output = tmp_path / "export.json"

    result = export_reviews(
        Namespace(
            review_db=tmp_path / "review.sqlite3",
            owner_pack=pack_path,
            output=output,
        )
    )

    assert result["status"] == "COMPLETE"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["raw_text_retained"] is False
    assert payload["annotations"][0]["owner_condition_spans"]
    assert sample["private_offer_text"] not in output.read_text(encoding="utf-8")


def test_asset_install_requires_explicit_staging_flag(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match="condition_review_runtime_staging_flag_required"
    ):
        install_assets(
            Namespace(
                owner_pack=tmp_path / "missing-pack.json",
                model=tmp_path / "missing-model.joblib",
                runtime_dir=tmp_path / "runtime",
                runtime_staging=False,
            )
        )
