"""仕様のサンプルに対する再検証。実行環境の依存は jsonschema のみ。"""
import copy
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BASE = "8ad57cf750f7ef905452df1549a4b60736496f1c"


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def check_contract():
    schema = load("schemas/run.schema.json")
    run = load("examples/contract-run.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    validator.validate(run)
    revised = copy.deepcopy(run)
    for name in ("Goal", "合意"):
        revised["旧" + name] = [copy.deepcopy(revised[name])]
        revised[name]["版"] += "-更新"
        revised["引継ぎ"][name + "版"] = revised[name]["版"]
    validator.validate(revised)
    check_history(revised)
    for name in ("Goal", "合意"):
        missing = copy.deepcopy(revised)
        del missing["旧" + name]
        expect_invalid_history(missing, f"判断の{name}版が存在しない")
        duplicate = copy.deepcopy(revised)
        duplicate["旧" + name].append(duplicate[name])
        expect_invalid_history(duplicate, f"{name}版が重複")
    invalid = copy.deepcopy(run)
    invalid["判断"][0]["状態"] = "実行可"
    errors = validator.iter_errors(invalid)
    assert {tuple(error.path)[:3] for error in errors} == {
        ("判断", 0, "独立レビュー"),
        ("判断", 0, "Context付きレビュー"),
    }
    invalid = copy.deepcopy(run)
    invalid["履歴"][0]["後"] = "運用"
    assert not validator.is_valid(invalid)

    check_history(run)
    assert run["区分"] == "模擬"
    assert all(record["種類"] == "模擬" for record in run["証拠"])
    print("スキーマ、適合例、拒否例、履歴・参照を確認")


def check_history(run):
    history = run["履歴"]
    evidence_ids = {record["ID"] for record in run["証拠"]}
    versions = {}
    for name in ("Goal", "Context", "合意"):
        records = [run[name], *run.get("旧" + name, [])]
        versions[name] = {record["版"] for record in records}
        assert len(versions[name]) == len(records), f"{name}版が重複"
    decision_ids = {decision["ID"] for decision in run["判断"]}
    assert len(evidence_ids) == len(run["証拠"]), "証拠IDが重複"
    assert len(decision_ids) == len(run["判断"]), "判断IDが重複"
    assert history[0]["前"] == "準備" and history[-1]["後"] == run["現在"]
    for previous, following in zip(history, history[1:]):
        assert previous["後"] == following["前"]
        assert parse_time(previous["時刻"]) <= parse_time(following["時刻"]), "履歴が時刻順でない"
    for entry in history + run["判断"]:
        assert set(entry["根拠"]) <= evidence_ids
    for decision in run["判断"]:
        options = {option["ID"] for option in decision["候補"]}
        assert len(options) == len(decision["候補"]), "候補IDが重複"
        assert decision["採用"] in options
        for name, known in versions.items():
            assert decision[name + "版"] in known, f"判断の{name}版が存在しない"
        for review_name in ("独立レビュー", "Context付きレビュー"):
            assert set(decision[review_name]["根拠"]) <= evidence_ids, "レビューの根拠が存在しない"
    for record in ("Goal", "Context", "合意"):
        assert run["引継ぎ"][record + "版"] == run[record]["版"], f"引継ぎの{record}版が現行版と異なる"


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_history_counterexamples():
    run = load("examples/contract-run.json")
    run["履歴"][0]["時刻"] = "2026-11-01T13:00:00+02:00"
    check_history(run)
    run["履歴"][0]["時刻"] = "2026-11-01T08:00:00-05:00"
    expect_invalid_history(run, "履歴が時刻順でない")

    run = load("examples/contract-run.json")
    run["判断"][0]["候補"][1]["ID"] = run["判断"][0]["候補"][2]["ID"]
    run["判断"][0]["採用"] = run["判断"][0]["候補"][2]["ID"]
    expect_invalid_history(run, "候補IDが重複")

    original = load("examples/contract-run.json")
    for record in ("Goal", "Context", "合意"):
        run = copy.deepcopy(original)
        run["引継ぎ"][record + "版"] = "存在しない版"
        expect_invalid_history(run, f"引継ぎの{record}版が現行版と異なる")
    for review_name in ("独立レビュー", "Context付きレビュー"):
        run = copy.deepcopy(original)
        run["判断"][0][review_name]["根拠"] = ["存在しない証拠"]
        expect_invalid_history(run, "レビューの根拠が存在しない")


def expect_invalid_history(run, expected):
    try:
        check_history(run)
    except AssertionError as error:
        assert str(error) == expected
    else:
        raise AssertionError(f"拒否されるべき履歴が通過: {expected}")


def check_documents():
    original_files = subprocess.check_output(
        ["git", "ls-tree", "--name-only", BASE], cwd=ROOT, text=True
    ).splitlines()
    assert len(original_files) == 7
    for name in original_files:
        original = subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT)
        assert (ROOT / "foundations" / name).read_bytes() == original, name
    for path in ROOT.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+\.(?:md|json))\)", path.read_text()):
            if "://" not in target:
                assert (path.parent / target).is_file(), (path, target)
    print("基礎7文書の原文一致と相対リンクを確認")


if __name__ == "__main__":
    check_contract()
    check_history_counterexamples()
    check_documents()
