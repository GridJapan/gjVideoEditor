# AGENTS.md

**このリポの規約は [CLAUDE.md](CLAUDE.md) です。作業前に必ず読んでください。**

```bash
cat CLAUDE.md
```

Claude Code は起動時に自動で読み込みますが、他のエージェント（codex 等）は自分で読んでください。
実装は素のPython＋HTMLのみなので、bashが叩けるエージェントなら同じように扱えます。

## 規約は3層に分かれています

CLAUDE.md には「どのセッションでも必要な事実」だけが入っています。
残りは**作業に応じて読む**形にしてあるので、必要なものを自分で開いてください。

| 何をするか | 読むもの |
|---|---|
| 動画を作る・演出を直す | `.claude/skills/making-short-videos/SKILL.md`（詳細は同ディレクトリの `reference/`） |
| `ui/editor.html` を直す | `.claude/rules/ui-editor.md` |
| `renderer/render.py` を直す | `.claude/rules/renderer.md` |
| `ui/server.py` を直す | `.claude/rules/server.md` |
| `tools/` の Python を書く | `.claude/rules/python-tools.md` |
| project.json のキーを調べる | `schema/project.schema.json`（**全キー定義の正**） |

Claude Code の場合、`.claude/rules/*.md` は該当ファイルを開いた時点で自動的に読み込まれ、
スキルは「動画作って」等の依頼で自動的に起動します。

**規約をここに書き写さないこと**（二重管理になり、必ず食い違います）。
