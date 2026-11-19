"""仕様のサンプルに対する再検証。実行環境の依存は jsonschema のみ。"""
import copy
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

if not __debug__:
    raise SystemExit("検証にはアサーションが必要です。Pythonの最適化を無効にしてください。")


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


    if run["区分"] == "実運用":
        check_execution_authorization(run)


def check_execution_authorization(run):
    decisions = {record["ID"]: record for record in run["判断"]}
    agreements = {record["版"]: record for record in [run["合意"], *run.get("旧合意", [])]}
    current_plan = run["測定"]
    previous_plans = run.get("旧測定", [])
    assert not previous_plans or "版" in current_plan, "現行の測定版がない"
    plans = {plan.get("版"): plan for plan in [current_plan, *previous_plans]}
    assert len(plans) == len(previous_plans) + 1, "測定版が重複"
    operational_evidence = {
        record["ID"]: parse_time(record["取得"])
        for record in run["証拠"]
        if record["種類"] in ("資料", "実測")
    }
    for event in run["履歴"]:
        if event["後"] != "実行":
            continue
        decision = decisions.get(event.get("判断ID"))
        assert decision is not None, "実行の判断IDが存在しない"
        assert decision["状態"] == "実行可", "実行可の判断がない"
        timestamp = parse_time(event["時刻"])
        agreement = agreements[decision["合意版"]]
        expires = parse_time(agreement["期限"])
        if "発効" in agreement:
            effective = parse_time(agreement["発効"])
            assert effective <= expires, "合意の発効が期限より後"
            assert effective <= timestamp, "実行時に合意が未発効"
        assert timestamp <= expires, "実行時に合意が期限切れ"
        if "撤回" in agreement:
            assert timestamp < parse_time(agreement["撤回"]), "実行時に合意が撤回済み"
        plan = plans.get(decision.get("測定版"))
        assert plan is not None, "実行判断の測定版が存在しない"
        assert parse_time(plan["事前確定"]) <= timestamp, "測定計画が実行後に確定"
        decision_cutoff = parse_time(decision.get("実行可確定", event["時刻"]))
        assert decision_cutoff <= timestamp, "判断が実行後に確定"
        available = {
            ref for ref, observed in operational_evidence.items() if observed <= decision_cutoff
        }
        assert available.intersection(decision["根拠"]), "実行時点で参照できる資料・実測がない"
        if decision["重要"]:
            context_review = decision["Context付きレビュー"]
            author = run["責任AI"]["企業"].strip().casefold()
            reviewer = context_review["企業"].strip().casefold()
            assert author and reviewer and author != reviewer, "Context付きレビューは別企業が必要"
            for name in ("独立レビュー", "Context付きレビュー"):
                review = decision[name]
                assert review["状態"] == "実施", f"{name}が未実施"
                assert available.intersection(review["根拠"]), f"{name}の実行時点の根拠がない"


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


def check_execution_counterexamples():
    run = load("examples/contract-run.json")
    # 実運用の分岐を通すための合成記録。実行や承認の事実を表さない。
    run["区分"] = "実運用"
    decision = run["判断"][0]
    decision.update(重要=False, 状態="実行可")
    event = next(entry for entry in run["履歴"] if entry["後"] == "実行")
    expect_invalid_history(run, "実行の判断IDが存在しない")
    event["判断ID"] = decision["ID"]
    run["合意"]["期限"] = event["時刻"]
    expect_invalid_history(run, "実行時点で参照できる資料・実測がない")
    evidence = next(record for record in run["証拠"] if record["ID"] in decision["根拠"])
    evidence["種類"] = "資料"
    evidence["取得"] = "2026-11-02T00:00:00Z"
    expect_invalid_history(run, "実行時点で参照できる資料・実測がない")
    evidence["取得"] = event["時刻"]
    check_history(run)
    agreement = run["合意"]
    agreement["期限"] = "2026-11-02T00:00:00Z"
    agreement["発効"] = "2026-11-01T12:00:01Z"
    expect_invalid_history(run, "実行時に合意が未発効")
    agreement["発効"] = "2026-11-03T00:00:00Z"
    expect_invalid_history(run, "合意の発効が期限より後")
    agreement["発効"] = event["時刻"]
    Draft202012Validator(load("schemas/run.schema.json")).validate(run)
    check_history(run)
    agreement["期限"] = event["時刻"]

    decision["状態"] = "保留"
    expect_invalid_history(run, "実行可の判断がない")
    decision["状態"] = "実行可"
    run["合意"]["期限"] = "2026-10-31T23:59:59Z"
    del agreement["発効"]
    expect_invalid_history(run, "実行時に合意が期限切れ")
    run["合意"]["期限"] = event["時刻"]
    run["測定"]["事前確定"] = "2026-11-02T00:00:00Z"
    expect_invalid_history(run, "測定計画が実行後に確定")

    run["測定"]["事前確定"] = event["時刻"]
    decision["重要"] = True
    independent = decision["独立レビュー"]
    contextual = decision["Context付きレビュー"]
    independent.update(状態="実施", 根拠=[evidence["ID"]])
    contextual.update(状態="実施", 企業=f" {run['責任AI']['企業']} ", 根拠=[evidence["ID"]])
    expect_invalid_history(run, "Context付きレビューは別企業が必要")
    contextual["企業"] = "別の検証組織"
    independent["状態"] = "未実施"
    expect_invalid_history(run, "独立レビューが未実施")
    independent["状態"] = "実施"
    later = copy.deepcopy(evidence)
    later.update(ID="レビューの事後資料", 取得="2026-11-02T00:00:00Z")
    run["証拠"].append(later)
    contextual["根拠"] = [later["ID"]]
    expect_invalid_history(run, "Context付きレビューの実行時点の根拠がない")
    contextual["根拠"] = [evidence["ID"]]
    check_history(run)

    old_plan = dict(run["測定"], 版="M1")
    run["旧測定"] = [old_plan]
    run["測定"].update(版="M2", 事前確定="2026-11-02T00:00:00Z")
    expect_invalid_history(run, "実行判断の測定版が存在しない")
    decision["測定版"] = "M2"
    expect_invalid_history(run, "測定計画が実行後に確定")
    decision["測定版"] = "M1"
    Draft202012Validator(load("schemas/run.schema.json")).validate(run)
    check_history(run)
    old_plan["版"] = "M2"
    expect_invalid_history(run, "測定版が重複")
    old_plan["版"] = "M1"
    decision["実行可確定"] = "2026-11-01T12:00:01Z"
    expect_invalid_history(run, "判断が実行後に確定")
    decision["実行可確定"] = "2026-11-01T11:59:59Z"
    expect_invalid_history(run, "実行時点で参照できる資料・実測がない")
    decision["実行可確定"] = event["時刻"]
    Draft202012Validator(load("schemas/run.schema.json")).validate(run)
    check_history(run)

    agreement["撤回"] = "2026-11-01T12:00:01Z"
    Draft202012Validator(load("schemas/run.schema.json")).validate(run)
    check_history(run)
    agreement["撤回"] = event["時刻"]
    expect_invalid_history(run, "実行時に合意が撤回済み")
    agreement["撤回"] = "2026-11-01T11:59:59Z"
    expect_invalid_history(run, "実行時に合意が撤回済み")


def check_exception_transitions():
    schema = load("schemas/run.schema.json")
    validator = Draft202012Validator(schema["properties"]["履歴"]["items"])
    template = load("examples/contract-run.json")["履歴"][0]
    routes = (
        ("運用", "改善", "調査", "相談待ち", "再開", "調査"),
        ("実行", "失敗", "相談待ち"),
        ("実行", "失敗", "実行"),
        ("検証", "改善", "調査"),
        ("相談待ち", "中断", "再開", "相談待ち"),
    )
    for route in routes:
        for before, after in zip(route, route[1:]):
            validator.validate(dict(template, 前=before, 後=after))
    for before, after in (("運用", "実行"), ("相談待ち", "実行"), ("中断", "実行")):
        assert not validator.is_valid(dict(template, 前=before, 後=after)), (before, after)
    print("異常系の復帰経路と、再評価・再開を飛ばす遷移の拒否を確認")


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
    check_execution_counterexamples()
    check_exception_transitions()
    check_documents()
