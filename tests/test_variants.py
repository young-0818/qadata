import json

import pytest

from qadata.eval.variants import load_variants, run_variants


def test_load_variants_parses_yaml(tmp_path):
    p = tmp_path / "variants.yaml"
    p.write_text(
        "variants:\n  - name: a\n    model: deepseek-chat\n"
        "  - name: b\n    model: qwen-flash\n    base_url: https://x.example\n",
        encoding="utf-8")
    vs = load_variants(str(p))
    assert vs == [{"name": "a", "model": "deepseek-chat"},
                  {"name": "b", "model": "qwen-flash", "base_url": "https://x.example"}]


@pytest.mark.parametrize("content,match", [
    ("variants:\n  - name: a\n", "model"),
    ("variants: []\n", "为空"),
])
def test_load_variants_bad_config_raises(tmp_path, content, match):
    p = tmp_path / "variants.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_variants(str(p))


def test_run_variants_writes_per_variant_and_compares(tmp_path, monkeypatch):
    """全假模型：各变体产出独立 JSONL + 汇总表；不得触网。"""
    from qadata.config import Settings
    from tests.conftest import make_fixture_db
    from tests.fakes import ScriptedLLM

    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    data = [{"question_id": 0, "db_id": "school", "question": "q0", "evidence": "",
             "SQL": "SELECT name FROM students", "difficulty": "simple"}]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("qadata.eval.variants.load_settings",
                        lambda: Settings(api_key="k", base_url="b", model="m"))
    monkeypatch.setattr("qadata.eval.variants.build_llm",
                        lambda s: ScriptedLLM(["q0", "SELECT name FROM students", "ok"]))

    summaries = run_variants(str(tmp_path / "dev.json"), str(tmp_path),
                             [{"name": "a", "model": "m1"}, {"name": "b", "model": "m2"}])
    assert [s["name"] for s in summaries] == ["a", "b"]
    for name in ("a", "b"):
        recs = [json.loads(l) for l in (tmp_path / "runs" / f"eval-{name}.jsonl")
                .read_text(encoding="utf-8").strip().splitlines()]
        assert recs[0]["correct"] is True
