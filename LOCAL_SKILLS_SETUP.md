# Local Agent Skills Setup

This project has local Agent Skills installed for both Claude and Codex-compatible agents.

## Install Locations

- Claude skills: `.claude/skills/<skill-name>/`
- Codex skills: `.agents/skills/<skill-name>/`

No symbolic links are used. Each skill was copied into both locations so the setup works reliably on Windows without requiring symlink privileges.

## Environment

- OS: Windows
- Shell: PowerShell 7.5.5
- Git: available, `git version 2.39.1.windows.1`
- Workspace root: `D:\工作\infinite social\voice agent\aisearch-openai-rag-audio`

## Anthropic Skills Installed

These were installed from `https://github.com/anthropics/skills`, copied from the repository's `skills/` directory.

| Skill | Source repo | Claude path | Codex path |
|---|---|---|---|
| `skill-creator` | `anthropics/skills` | `.claude/skills/skill-creator/` | `.agents/skills/skill-creator/` |
| `webapp-testing` | `anthropics/skills` | `.claude/skills/webapp-testing/` | `.agents/skills/webapp-testing/` |
| `frontend-design` | `anthropics/skills` | `.claude/skills/frontend-design/` | `.agents/skills/frontend-design/` |
| `doc-coauthoring` | `anthropics/skills` | `.claude/skills/doc-coauthoring/` | `.agents/skills/doc-coauthoring/` |
| `pdf` | `anthropics/skills` | `.claude/skills/pdf/` | `.agents/skills/pdf/` |
| `docx` | `anthropics/skills` | `.claude/skills/docx/` | `.agents/skills/docx/` |
| `pptx` | `anthropics/skills` | `.claude/skills/pptx/` | `.agents/skills/pptx/` |
| `xlsx` | `anthropics/skills` | `.claude/skills/xlsx/` | `.agents/skills/xlsx/` |
| `mcp-builder` | `anthropics/skills` | `.claude/skills/mcp-builder/` | `.agents/skills/mcp-builder/` |

## Awesome Agent Skills Installed

These were selected from `https://github.com/VoltAgent/awesome-agent-skills`, then downloaded from their actual source repositories instead of copying the index repository.

| Skill | Source repo | Claude path | Codex path |
|---|---|---|---|
| `react-best-practices` | `vercel-labs/agent-skills` | `.claude/skills/react-best-practices/` | `.agents/skills/react-best-practices/` |
| `composition-patterns` | `vercel-labs/agent-skills` | `.claude/skills/composition-patterns/` | `.agents/skills/composition-patterns/` |
| `web-design-guidelines` | `vercel-labs/agent-skills` | `.claude/skills/web-design-guidelines/` | `.agents/skills/web-design-guidelines/` |
| `frontend-design-review` | `microsoft/skills` | `.claude/skills/frontend-design-review/` | `.agents/skills/frontend-design-review/` |
| `playwright` | `openai/skills` | `.claude/skills/playwright/` | `.agents/skills/playwright/` |
| `modern-python` | `trailofbits/skills` | `.claude/skills/modern-python/` | `.agents/skills/modern-python/` |
| `insecure-defaults` | `trailofbits/skills` | `.claude/skills/insecure-defaults/` | `.agents/skills/insecure-defaults/` |
| `error-analysis` | `hamelsmu/evals-skills` | `.claude/skills/error-analysis/` | `.agents/skills/error-analysis/` |
| `evaluate-rag` | `hamelsmu/evals-skills` | `.claude/skills/evaluate-rag/` | `.agents/skills/evaluate-rag/` |
| `skill-optimizer` | `hqhq1025/skill-optimizer` | `.claude/skills/skill-optimizer/` | `.agents/skills/skill-optimizer/` |

## Azure Skills Installed

These Azure SDK skills were selected because this project uses Azure OpenAI, Azure AI Search, Azure Blob Storage, Container Apps deployment, Azure identity, observability, and voice/realtime services.

| Skill | Source repo | Claude path | Codex path |
|---|---|---|---|
| `azure-search-documents-py` | `microsoft/skills` | `.claude/skills/azure-search-documents-py/` | `.agents/skills/azure-search-documents-py/` |
| `azure-identity-py` | `microsoft/skills` | `.claude/skills/azure-identity-py/` | `.agents/skills/azure-identity-py/` |
| `azure-storage-blob-py` | `microsoft/skills` | `.claude/skills/azure-storage-blob-py/` | `.agents/skills/azure-storage-blob-py/` |
| `azure-containerregistry-py` | `microsoft/skills` | `.claude/skills/azure-containerregistry-py/` | `.agents/skills/azure-containerregistry-py/` |
| `azure-monitor-opentelemetry-py` | `microsoft/skills` | `.claude/skills/azure-monitor-opentelemetry-py/` | `.agents/skills/azure-monitor-opentelemetry-py/` |
| `azure-monitor-query-py` | `microsoft/skills` | `.claude/skills/azure-monitor-query-py/` | `.agents/skills/azure-monitor-query-py/` |
| `azure-ai-voicelive-py` | `microsoft/skills` | `.claude/skills/azure-ai-voicelive-py/` | `.agents/skills/azure-ai-voicelive-py/` |
| `azure-ai-voicelive-ts` | `microsoft/skills` | `.claude/skills/azure-ai-voicelive-ts/` | `.agents/skills/azure-ai-voicelive-ts/` |

## Verification

Each installed skill was checked for:

- Directory exists in `.claude/skills/`
- Directory exists in `.agents/skills/`
- `SKILL.md` exists in both copies
- Skill directory name is kebab-case

## Skipped Or Adjusted Candidates

- No requested candidate skill was skipped for missing `SKILL.md` or incompatible structure.
- The awesome index entry for `hamelsmu/error-analysis` and `hamelsmu/evaluate-rag` pointed at `hamelsmu/prompts`, which was not available via GitHub clone. The actual usable source was resolved to `hamelsmu/evals-skills`, and both skills were installed from there.
- Pure visual/art/GIF/branding/marketing-only skills were not installed.
- Anthropic-specific API workflow skills that were not relevant to this Azure OpenAI project were not installed.

## Claude Usage

Claude-compatible agents should load project-local skills from:

```text
.claude/skills/
```

If Claude does not pick them up immediately, restart the Claude session or reload the project workspace.

## Codex Usage

Codex-compatible agents should load project-local skills from:

```text
.agents/skills/
```

If Codex does not pick them up immediately, restart Codex or reload the project workspace.

## Recommended Custom Skills To Create Next

The next best step is to create project-specific skills for this codebase:

- `voice-rag-debug`
- `acs-call-flow-debug`
- `salesforce-quote-flow`
- `azure-containerapps-deploy-runbook`

Recommended first custom skill: `acs-call-flow-debug`, because this project has complex ACS webhook, recognition, TTS, transfer, and GPT Realtime call paths that benefit from a repeatable debugging runbook.
