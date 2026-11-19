"""仕様のサンプルに対する再検証。実行環境の依存は jsonschema のみ。"""
import copy
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from statistics import median
from fractions import Fraction

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BASE = "8ad57cf750f7ef905452df1549a4b60736496f1c"


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def check_contract():
    schema = load("schemas/run.schema.json")
    run = load("examples/business-run.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    validator.validate(run)
    invalid = load("examples/invalid-review.json")
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
    context_versions = {record["版"] for record in [run["Context"], *run["旧Context"]]}
    decision_ids = {decision["ID"] for decision in run["判断"]}
    assert len(evidence_ids) == len(run["証拠"]), "証拠IDが重複"
    assert len(decision_ids) == len(run["判断"]), "判断IDが重複"
    assert len(context_versions) == 1 + len(run["旧Context"]), "Context版が重複"
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
        assert decision["Context版"] in context_versions
        assert decision["Goal版"] == run["Goal"]["版"]
        assert decision["合意版"] == run["合意"]["版"]
    assert run["引継ぎ"]["Context版"] == run["Context"]["版"]

def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_history_counterexamples():
    run = load("examples/business-run.json")
    run["履歴"][0]["時刻"] = "2026-11-01T13:00:00+02:00"
    check_history(run)
    run["履歴"][0]["時刻"] = "2026-11-01T08:00:00-05:00"
    try:
        check_history(run)
    except AssertionError as error:
        assert str(error) == "履歴が時刻順でない"
    else:
        raise AssertionError("時刻逆転を拒否しなかった")

    run = load("examples/business-run.json")
    run["判断"][0]["候補"][1]["ID"] = run["判断"][0]["候補"][2]["ID"]
    run["判断"][0]["採用"] = run["判断"][0]["候補"][2]["ID"]
    try:
        check_history(run)
    except AssertionError as error:
        assert str(error) == "候補IDが重複"
    else:
        raise AssertionError("候補IDの重複を拒否しなかった")


def check_simulation():
    data = load("examples/business-metrics.json")
    conditions = data["条件"]
    paid = [case for case in data["案件"] if case["支払額円"] > 0]
    shortening = median(case["通常分"] - case["支援後分"] for case in paid)
    revenue = sum(case["支払額円"] for case in paid)
    labor = Fraction(sum(data["週稼働分"]) * data["時給円"], 60)
    initial_cost = data["直接費円"]
    revised_cost = initial_cost - data["改定前料金円"] + data["改定後料金円"]

    def meets_conditions(cost, labor_cost):
        return all((
            len(paid) >= conditions["最低有料件数"],
            revenue - cost - labor_cost >= conditions["最低寄与利益円"],
            shortening >= conditions["最低中央値短縮分"],
            cost <= conditions["最大直接費円"],
            max(data["週稼働分"]) <= conditions["最大週稼働分"],
            data["重大事故件数"] <= conditions["最大重大事故件数"],
            data["外部共有件数"] <= conditions["最大外部共有件数"],
        ))

    assert (len(paid), shortening, revenue - initial_cost - labor) == (3, 23, 4900)
    assert meets_conditions(initial_cost, labor)
    assert revised_cost == 4000 and revenue - revised_cost - labor == 2300
    assert not meets_conditions(revised_cost, labor)
    assert revenue - initial_cost - 2 * labor == 2200
    assert not meets_conditions(initial_cost, 2 * labor)
    print("模擬試行の成功、料金改定と人件費増加での未達を確認")


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
    check_simulation()
    check_documents()
