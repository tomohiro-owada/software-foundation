# Software Foundation

既存のAIエージェントが人間のGoalに向けて判断し、結果を検証し、改善を続けるための共通規約です。

1. [原則と判断規範](spec/principles.md)を読む。
2. [実行契約と状態遷移](spec/workflow.md)で現在地と次の行動を決める。
3. [意思決定・証拠・改善の手順](spec/protocols.md)に従う。
4. [実行記録スキーマ](schemas/run.schema.json)と[自己検証](examples/validation.md)で記録を点検する。

[基礎7文書](foundations/README.md)は議論・合意・未決事項の原文です。Markdownを判断規範の正本とし、JSON Schemaは実行記録の構造を検査します。実行仕様は人間のレビューに付す提案です。
