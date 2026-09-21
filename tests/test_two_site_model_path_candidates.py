from __future__ import annotations

from pathlib import Path

from scripts.two_site_proposal_runner import _repository_model_class_paths


def test_repository_model_class_paths_indexes_one_canonical_top_level_model(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    user_file = models_dir / "user.py"
    user_file.write_text("class User:\n    pass\n", encoding="utf-8")

    mapping = _repository_model_class_paths(tmp_path)

    assert mapping == {"User": {"models/user.py"}}
