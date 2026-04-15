---
name: sync-readme
description: Use when README.md or README-ja.md has been updated and the counterpart needs to follow. Trigger whenever the user says things like "update README.md to match README-ja.md", "sync the English README", "README-ja.md を更新したので README.md も更新して", "README を同期して", or when one README was just edited and the other should reflect the same changes. The primary flow is README-ja.md → README.md (Japanese is usually edited first), but this skill handles both directions.
---

# README Sync

This skill keeps `README.md` (English) and `README-ja.md` (Japanese) in structural and content parity after one is updated.

## Project context

- `README.md` — English, primary for external users
- `README-ja.md` — Japanese, typically the first-edited file

The two files share the same document structure: section order, code blocks, tables, and mermaid diagrams must be identical. Only the human-readable prose (headings, descriptions, callout text, table cell descriptions) differs by language.

## Step 1: Clarify source and target

Default: `README-ja.md` → `README.md`.

If the user specifies the other direction, follow their instruction.

## Step 2: Get the exact diff of the source file

Run `git diff HEAD -- <source-file>` to see precisely what lines were added, removed, or changed. This is the ground truth for what needs to be reflected in the target — do not guess or infer from comparing the two files holistically.

```bash
git diff HEAD -- README-ja.md   # or README.md for en→ja
```

If the file is already staged (no unstaged diff), use:
```bash
git diff HEAD -- <source-file>   # shows both staged and unstaged vs last commit
```

If there is no git diff (e.g., not a git repo, or changes were already committed), fall back to asking the user: "What did you change? Please describe the sections you edited."

## Step 3: Interpret the diff

Read the diff carefully. For each hunk:

- `+` lines are additions in the source — need to be translated and added to the target
- `-` lines are removals from the source — need to be removed (or the translated equivalent removed) from the target
- Context lines (no prefix) show where the change sits — use them to locate the right place in the target file

**Deletions matter.** A `-` line means the user intentionally removed that content. Reflect it in the target even if it is a small wording change or a single sentence deletion.

**Wording changes matter.** If a sentence changed subtly (a `-` line replaced by a `+` line), update the target's corresponding sentence — do not leave the old translation if the source wording changed.

## Language-specific differences (do not sync these)

Some content intentionally differs between the two files. Do not overwrite or remove these when syncing.

| Location | README.md (English) | README-ja.md (Japanese) |
|----------|---------------------|-------------------------|
| Zenn article link (end of Motivation section) | Appended with ` *(Japanese only — sorry, English speakers!)*` | No such note |

When syncing ja→en: if the source adds or changes the Zenn article link line, preserve the English-only ` *(Japanese only — sorry, English speakers!)*` suffix in the target.
When syncing en→ja: if the Zenn article link line changes, do not carry over the ` *(Japanese only ...)* ` suffix to the Japanese file.

## Step 4: Apply changes to the target

For each diff hunk, find the corresponding location in the target file and edit it:

**Prose (descriptions, callout text, bullet explanations)**
Translate faithfully, preserving meaning and tone.
- English: clear and professional technical writing
- Japanese: natural and conversational for a technical audience (casual register, no honorifics)

**Code blocks (JSON, YAML, bash, mermaid)**
Copy verbatim — do not translate anything inside code blocks unless the user explicitly asks.
Exception: mermaid `Note over` and `alt` block labels count as prose and should be translated.

**Tables**
Keep column structure identical. Translate header text and description cells. Never translate field/input/output names (e.g., `dry_run`, `config_file`).

**Section headings**
Translate to match the target language's existing heading style.

**Language switcher (line 3)**
Never touch — `**Language: English | [日本語](README-ja.md)**` in README.md and the reverse in README-ja.md.

**File paths, URLs, GitHub Action step names**
Leave unchanged.

## Step 5: Write edits

Use the Edit tool to make targeted edits that mirror exactly what the diff showed — nothing more, nothing less. Do not rewrite unrelated sections.

## Step 6: Summarize

List each diff hunk and what you did in the target to reflect it.

---

## Translation reference

| Japanese | English |
|----------|---------|
| リリース構成 | release configuration(s) |
| ワークフロー構成 | workflow configuration(s) |
| 冪等 | idempotent |
| デプロイ | deploy / applied (context-dependent) |
| クイックスタート | Quick start |
| 処理フロー | Processing flow |
| コントリビューター向け: 開発手順 | Contributor Guide |
| ブランチ戦略 | Branch Strategy |
| ライセンス | License |
| 必須 | Required |
| 説明 | Description |

Dataform-specific terms to keep untranslated in both languages: `release_configs`, `workflow_configs`, `compile_override`, `sync_delete`, `releaseConfig`, `workflowConfig`, `compilationResults`, `releaseCompilationResult`.

