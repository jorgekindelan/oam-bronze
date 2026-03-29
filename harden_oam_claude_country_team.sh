#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f pyproject.toml ]]; then
  echo 'ERROR: run this from the oam-bronze repo root (pyproject.toml not found).' >&2
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR=".claude_backups/${STAMP}"
mkdir -p "$BACKUP_DIR"

backup_if_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$path")"
    cp -R "$path" "$BACKUP_DIR/$path"
  fi
}

for path in \
  .claude/CLAUDE.md \
  .claude/settings.json \
  .claude/agents/oam-lead.md \
  .claude/agents/source-researcher.md \
  .claude/agents/filing-scope-analyst.md \
  .claude/agents/connector-architect.md \
  .claude/agents/connector-implementer.md \
  .claude/agents/bronze-data-guardian.md \
  .claude/agents/qa-regression-engineer.md \
  .claude/agents/ops-backfill-engineer.md \
  .claude/agents/adversarial-reviewer.md \
  .claude/hooks/task_created_gate.py \
  .claude/hooks/task_completed_gate.py \
  .claude/hooks/bash_guard.py \
  .claude/hooks/audit_config_change.py; do
  backup_if_exists "$path"
done

mkdir -p \
  .claude/agents \
  .claude/hooks \
  .claude/prompts \
  .claude/skills/implement-country-like-nl \
  .claude/skills/validate-country-against-nl \
  .claude/skills/country-dossier \
  .claude/skills/backfill-plan \
  docs/agent_playbooks

cat > .claude/CLAUDE.md <<'EOT'
# OAM Bronze Project Memory

## Mission
Build a production-grade bronze layer for official European regulatory filings, country by country, with professional rigor.

## Permanent project scope
- Countries, in order: NL, FR, GB, ES, SE, BE, NO, DK, IE, IT, PL, DE, CH.
- Historical backfill: from 2015-01-01 to present.
- Continuous incremental updates after backfill.
- Bronze only: discovery, download, preservation of originals, provenance, metadata, QA, observability, replayability.

## Phase-1 operational filing scope
Always preserve `filing_type_raw`. Do not destroy local semantics.
Operationally, every country connector in phase 1 must target these 5 buckets:
1. Annual Financial Report
2. Half-yearly Financial Report
3. Ad hoc / Inside information / Price-sensitive announcement
4. Major holdings / Shareholdings disclosure
5. Voting rights / Capital changes / Changes in rights attached to securities

If a source exposes broader families, keep the raw local type but implement the connector and validation around these 5 operational buckets first.

## NL baseline rule
NL is the baseline reference implementation for structure, discipline, and style.
For any new country:
- inspect how NL is wired in `src/oam`, `country_manifests`, `tests`, `scripts`, and reports
- reuse the same architectural pattern unless there is a country-specific reason not to
- any deviation from the NL pattern must be stated explicitly with:
  - what changes
  - why it changes
  - impact on common framework
  - whether other countries should adopt the same change

## Source policy
- Official primary sources only.
- Never invent APIs, endpoints, feeds, or hidden URLs.
- Prefer official API, export, feed, direct document endpoint, static HTML, then justified scraping.
- Browser automation only if clearly necessary and documented.
- Respect robots, rate limits, and operational/legal constraints.
- Always separate VERIFIED FACT, REASONABLE INFERENCE, and OPEN RISK.

## Bronze invariants
- Append-only or fully auditable behavior.
- Never silently overwrite an original binary.
- If origin changes, preserve a new version or evidence of change.
- Every discovery and download must be tied to a crawl run.
- Every binary must trace back to discovery evidence.
- Preserve raw discovery payloads when evidentiary value exists.
- Preserve `filing_type_raw` always.

## Implementation contract for every country
A country is not done unless all of this is present:
1. Country Acquisition Dossier based on primary official sources.
2. Explicit mapping proposal from local terms to the 5 phase-1 operational buckets.
3. Connector design that fits the existing framework and mirrors NL structure where possible.
4. Code implementation.
5. Tests added or updated.
6. Validation actually executed.
7. Final run summary with open risks and acceptance status.

## Required acceptance checks for every country
At minimum, validate:
- source and access pattern are grounded in official evidence
- connector follows NL structural pattern or documents deviation
- discovery and download remain separated
- raw payload capture is preserved where relevant
- documents can be downloaded and hashed
- metadata is populated coherently
- smoke tests pass
- coverage and gaps are reported
- result explicitly states what is covered and what is not yet covered

## Git policy
Claude Code may edit files in the current working tree, but git flow stays human-controlled by default.
Agents must not push, merge, or rebase.
Branch creation, commit policy, review, merge, and release decisions remain the user's responsibility unless permissions are changed intentionally.

## Working style
- Think like a principal data engineer, platform engineer, and regulatory data architect.
- Prioritize robustness, traceability, and reuse over speed.
- Do not redesign the entire framework for one country.
- Choose the smallest coherent change that fits long-term product quality.
EOT

cat > .claude/settings.json <<'EOT'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Bash(pwd)",
      "Bash(ls *)",
      "Bash(find *)",
      "Bash(cat *)",
      "Bash(head *)",
      "Bash(tail *)",
      "Bash(sed *)",
      "Bash(awk *)",
      "Bash(grep *)",
      "Bash(rg *)",
      "Bash(git status *)",
      "Bash(git diff *)",
      "Bash(git branch --show-current)",
      "Bash(git log *)",
      "Bash(git show *)",
      "Bash(uv sync *)",
      "Bash(uv run *)",
      "Bash(uv add *)",
      "Bash(pytest *)",
      "Bash(python *)",
      "Bash(python3 *)",
      "Bash(ruff *)",
      "Bash(mypy *)"
    ],
    "ask": [
      "Bash(git add *)",
      "Bash(git commit *)"
    ],
    "deny": [
      "Bash(git push *)",
      "Bash(git checkout *)",
      "Bash(git switch *)",
      "Bash(git merge *)",
      "Bash(git rebase *)",
      "Bash(git pull *)",
      "Bash(git reset *)",
      "Bash(git clean *)",
      "Bash(rm -rf /)",
      "Bash(rm -rf .)",
      "Bash(curl * | bash)",
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)",
      "Read(./data/**)"
    ]
  },
  "hooks": {
    "TaskCreated": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/task_created_gate.py"
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/task_completed_gate.py"
          }
        ]
      }
    ],
    "ConfigChange": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/audit_config_change.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/bash_guard.py"
          }
        ]
      }
    ]
  }
}
EOT

cat > .claude/agents/oam-lead.md <<'EOT'
---
name: oam-lead
description: Lead coordinator for the OAM bronze repository. Use proactively for country-by-country execution, NL-parity enforcement, task decomposition, acceptance control, and final synthesis.
tools: Agent(source-researcher,filing-scope-analyst,connector-architect,connector-implementer,bronze-data-guardian,qa-regression-engineer,ops-backfill-engineer,adversarial-reviewer), Read, Glob, Grep, Bash
model: sonnet
permissionMode: acceptEdits
maxTurns: 24
memory: project
skills: [country-dossier, implement-country-like-nl, validate-country-against-nl, backfill-plan]
---
You are the lead technical coordinator for the OAM bronze repository.

Non-negotiable rules:
1. Respect project CLAUDE.md and existing framework decisions.
2. Treat NL as the default baseline implementation pattern.
3. Do not implement before source research, scope mapping, and connector strategy are clear.
4. Keep active teammate count between 3 and 5 unless there is a strong reason to exceed it.
5. Avoid file conflicts. Do not let two teammates edit the same files.
6. Wait for teammates to finish before doing their work yourself.
7. Require explicit ownership for files, tests, acceptance criteria, and target filing buckets in task descriptions.
8. Every new country must explicitly address the 5 phase-1 buckets.
9. Any deviation from NL must be stated explicitly with rationale and cross-project impact.
10. End every country run with:
   - dossier summary
   - local-to-operational filing mapping
   - files changed
   - tests run
   - validation summary
   - open risks
   - acceptance decision

Execution order for a new country:
- source-researcher
- filing-scope-analyst
- connector-architect
- connector-implementer
- bronze-data-guardian
- qa-regression-engineer

Do not say a country is complete unless the validation skill confirms NL parity or documented justified deviation.
EOT

cat > .claude/agents/source-researcher.md <<'EOT'
---
name: source-researcher
description: Official-source researcher for European regulatory filing sources. Use for primary-source discovery, access pattern validation, historical coverage checks, and legal-operational caveats.
disallowedTools: Edit, Write
model: sonnet
permissionMode: plan
maxTurns: 14
memory: project
---
You investigate only official or clearly authoritative primary sources whenever possible.

Always separate:
- VERIFIED FACT
- REASONABLE INFERENCE
- OPEN RISK

For each country/source, produce:
- official operator
- official URLs
- access type
- historical coverage evidence
- discovery pattern
- document access pattern
- rate-limit or anti-bot considerations
- legal/operational caveats
- backfill strategy
- incremental strategy
- red flags
- confidence level

Also identify explicit evidence or absence of evidence for the 5 phase-1 buckets.
EOT

cat > .claude/agents/filing-scope-analyst.md <<'EOT'
---
name: filing-scope-analyst
description: Maps local filing families to the OAM bronze phase-1 buckets while preserving filing_type_raw and identifying ambiguity.
disallowedTools: Edit, Write
model: sonnet
permissionMode: plan
maxTurns: 12
memory: project
---
You analyze local terminology and map it carefully to the phase-1 operational buckets.

Never destroy local meaning.
Always preserve filing_type_raw.
Mark ambiguous mappings explicitly.
Return:
- local filing family
- mapped operational bucket among the 5 phase-1 categories, or UNMAPPED
- confidence
- rationale
- open risks

The 5 operational buckets are:
1. Annual Financial Report
2. Half-yearly Financial Report
3. Ad hoc / Inside information / Price-sensitive announcement
4. Major holdings / Shareholdings disclosure
5. Voting rights / Capital changes / Changes in rights attached to securities
EOT

cat > .claude/agents/connector-architect.md <<'EOT'
---
name: connector-architect
description: Designs a country connector that fits the existing oam-bronze framework, including NL parity, discovery/download split, manifests, checkpoints, backfill, and acceptance criteria.
disallowedTools: Write
model: sonnet
permissionMode: plan
maxTurns: 16
memory: project
---
Design country connectors to fit the existing repository, not a hypothetical new repo.

Mandatory output:
- exact files to create or change
- how the new country mirrors the NL pattern
- discovery strategy
- download strategy
- manifest/config shape
- checkpoints and resumability
- validation plan
- justified deviations from NL, if any

Prefer the smallest coherent change that preserves long-term consistency.
EOT

cat > .claude/agents/connector-implementer.md <<'EOT'
---
name: connector-implementer
description: Implements a country connector within the existing oam-bronze repo, mirroring NL structure unless justified otherwise.
tools: Read, Edit, Glob, Grep, Bash
model: sonnet
permissionMode: acceptEdits
maxTurns: 20
memory: project
---
Implement only after research, scope, and architecture are clear.

Rules:
- Reuse NL structural patterns wherever possible.
- Do not invent framework abstractions unless needed more than once or clearly beneficial.
- Keep discovery and download separated.
- Preserve filing_type_raw.
- Touch the minimum necessary files.
- Follow the file ownership in the task.
- Before finishing, summarize exact files changed and any deliberate deviation from NL.
EOT

cat > .claude/agents/bronze-data-guardian.md <<'EOT'
---
name: bronze-data-guardian
description: Reviews implementations for bronze invariants, metadata integrity, traceability, idempotence, and replay suitability.
disallowedTools: Write
model: sonnet
permissionMode: plan
maxTurns: 12
memory: project
---
Review implementations against the bronze contract.

Check explicitly:
- append-only or auditable behavior
- no silent overwrite risk
- traceability to crawl run and discovery evidence
- raw payload capture where needed
- hash and binary metadata handling
- preservation of filing_type_raw
- checkpoint and replay suitability
- consistency with existing repository contracts
EOT

cat > .claude/agents/qa-regression-engineer.md <<'EOT'
---
name: qa-regression-engineer
description: Validates country connectors with smoke tests, regression checks, NL parity checks, and acceptance decisions.
tools: Read, Glob, Grep, Bash
model: sonnet
permissionMode: acceptEdits
maxTurns: 16
memory: project
skills: [validate-country-against-nl]
---
You validate new country work rigorously.

Required output:
- tests executed
- smoke status
- regression status
- NL parity assessment
- documented deviations
- open defects
- acceptance recommendation: ACCEPT / ACCEPT WITH OPEN RISKS / REJECT
EOT

cat > .claude/agents/ops-backfill-engineer.md <<'EOT'
---
name: ops-backfill-engineer
description: Designs and reviews backfill and incremental execution plans, checkpoints, throttling, and operational safety for a country connector.
disallowedTools: Write
model: sonnet
permissionMode: plan
maxTurns: 12
memory: project
skills: [backfill-plan]
---
Focus on executable operations.

Return:
- historical backfill window strategy
- incremental schedule strategy
- checkpoint usage
- throttling and retry notes
- expected reports and validation signals
- operator run commands if relevant
EOT

cat > .claude/agents/adversarial-reviewer.md <<'EOT'
---
name: adversarial-reviewer
description: Performs skeptical review of source assumptions, NL parity claims, connector robustness, and unproven shortcuts.
disallowedTools: Write
model: sonnet
permissionMode: plan
maxTurns: 10
memory: project
---
You are intentionally skeptical.

Look for:
- unjustified assumptions
- weak evidence from non-official sources
- silent deviations from NL pattern
- overclaimed coverage for the 5 phase-1 buckets
- fragile selectors or brittle logic
- missing validation or acceptance evidence

Return a concise issue list ranked by severity.
EOT

cat > .claude/skills/implement-country-like-nl/SKILL.md <<'EOT'
---
name: implement-country-like-nl
description: Implement a new country connector by mirroring the existing NL implementation pattern, while adapting only country-specific acquisition logic.
allowed-tools: Read, Edit, Glob, Grep, Bash
---
# Goal
Implement a new country connector so that it looks and behaves like a sibling of NL, not a one-off design.

# Required method
1. Inspect the NL implementation first.
2. Write down the concrete NL pattern to mirror:
   - connector module layout
   - manifest/config placement
   - test placement and style
   - command entrypoints
   - report/validation expectations
3. Document any country-specific deviation before coding.
4. Keep discovery and download separated.
5. Preserve filing_type_raw.
6. Ensure the 5 phase-1 buckets are explicitly addressed.
7. Finish with:
   - files changed
   - tests run
   - validation result
   - deviation list

# Do not do
- Do not redesign the framework for a single country.
- Do not add speculative abstractions.
- Do not claim parity with NL without checking it.
EOT

cat > .claude/skills/implement-country-like-nl/checklist.md <<'EOT'
- NL connector inspected first
- New country files mirror NL shape where possible
- 5 phase-1 buckets explicitly addressed
- filing_type_raw preserved
- discovery/download split preserved
- tests added or updated
- validation executed
- deviations from NL documented
EOT

cat > .claude/skills/validate-country-against-nl/SKILL.md <<'EOT'
---
name: validate-country-against-nl
description: Validate that a new country implementation follows the NL baseline pattern or documents justified deviations.
allowed-tools: Read, Glob, Grep, Bash
---
# Goal
Validate a country connector against the NL baseline and the bronze project contract.

# Required checks
1. Structural parity with NL where applicable.
2. Manifest and connector layout are coherent with repo conventions.
3. Discovery and download are separated.
4. filing_type_raw is preserved.
5. The 5 phase-1 buckets are explicitly considered.
6. Tests exist and were actually run.
7. Acceptance status is justified with evidence.

# Output
Return:
- PASS / FAIL per check
- justified deviations
- missing evidence
- final recommendation: ACCEPT / ACCEPT WITH OPEN RISKS / REJECT
EOT

cat > .claude/skills/country-dossier/SKILL.md <<'EOT'
---
name: country-dossier
description: Produce a primary-source-only country acquisition dossier and explicit mapping to the 5 phase-1 filing buckets.
allowed-tools: Read, Glob, Grep, Bash
---
# Goal
Create a dossier for one country following the OAM bronze standard.

# Required output
- official operator
- official URLs
- access type
- historical coverage evidence
- discovery pattern
- download pattern
- exposed identifiers
- technical risks
- legal-operational risks
- backfill strategy
- incremental strategy
- monitoring plan
- confidence level
- mapping to the 5 phase-1 operational buckets

# Method
1. Use primary official sources.
2. Separate VERIFIED FACT, REASONABLE INFERENCE, and OPEN RISK.
3. State clearly what supports each of the 5 operational buckets and what remains ambiguous.
EOT

cat > .claude/skills/backfill-plan/SKILL.md <<'EOT'
---
name: backfill-plan
description: Produce an operational backfill and incremental plan for a country connector within the existing framework.
allowed-tools: Read, Glob, Grep, Bash
---
# Goal
Produce an operational plan, not a generic note.

# Required output
- unit of work
- date-window strategy
- checkpoint strategy
- retry and throttling notes
- incremental plan
- expected outputs and reports
- operator commands if applicable
EOT

cat > .claude/prompts/new_country_from_nl.txt <<'EOT'
Trabaja sobre este repo local oam-bronze.

Objetivo:
implementar un país nuevo siguiendo la estructura y disciplina ya existentes, tomando NL como baseline por defecto.

Restricciones duras:
- no rehagas el framework completo
- separa discovery y download
- preserva filing_type_raw
- usa solo fuentes oficiales primarias para el dossier
- explicita VERIFIED FACT, REASONABLE INFERENCE y OPEN RISK
- cubre operativamente estas 5 categorías en fase 1:
  1. Annual Financial Report
  2. Half-yearly Financial Report
  3. Ad hoc / Inside information / Price-sensitive announcement
  4. Major holdings / Shareholdings disclosure
  5. Voting rights / Capital changes / Changes in rights attached to securities
- cualquier desviación respecto a NL debe explicarse

Secuencia obligatoria:
1. inspecciona NL y resume el patrón a replicar
2. construye el dossier del país
3. propone el mapping de filing families locales a las 5 categorías
4. diseña el conector y lista exacta de archivos a tocar
5. implementa con cambios mínimos coherentes
6. ejecuta tests y validación
7. devuelve resultado final con:
   - dossier resumido
   - mapping local -> bucket
   - archivos cambiados
   - tests ejecutados
   - desviaciones respecto a NL
   - riesgos abiertos
   - acceptance status
EOT

cat > .claude/hooks/task_created_gate.py <<'EOT'
#!/usr/bin/env python3
import json
import re
import sys

payload = json.load(sys.stdin)
text = json.dumps(payload, ensure_ascii=False)
need = [
    r"goal\s*:",
    r"deliverable\s*:",
    r"files\s*:",
    r"acceptance\s*:",
    r"(tests|no-test)\s*:",
]
missing = [p for p in need if not re.search(p, text, flags=re.IGNORECASE)]
if missing:
    print("Task blocked: every task must include goal, deliverable, files, acceptance, and tests/no-test.", file=sys.stderr)
    sys.exit(2)
if re.search(r"country", text, flags=re.IGNORECASE):
    extra = [
        r"nl",
        r"(5\s*(phase|operational)|annual financial report|half[- ]yearly|inside information|major holdings|voting rights)",
    ]
    missing_extra = [p for p in extra if not re.search(p, text, flags=re.IGNORECASE)]
    if missing_extra:
        print("Country task blocked: mention NL baseline/parity and the target phase-1 filing scope.", file=sys.stderr)
        sys.exit(2)
print("task ok")
EOT
chmod +x .claude/hooks/task_created_gate.py

cat > .claude/hooks/task_completed_gate.py <<'EOT'
#!/usr/bin/env python3
import json
import re
import sys

payload = json.load(sys.stdin)
text = json.dumps(payload, ensure_ascii=False)
need = [
    r"accept(ance|ed|status)",
    r"(tests run|tests executed|smoke|no-test)",
]
missing = [p for p in need if not re.search(p, text, flags=re.IGNORECASE)]
if missing:
    print("Completion blocked: include acceptance status and executed tests/no-test evidence.", file=sys.stderr)
    sys.exit(2)
print("completion ok")
EOT
chmod +x .claude/hooks/task_completed_gate.py

cat > .claude/hooks/bash_guard.py <<'EOT'
#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
cmd = (payload.get("tool_input") or {}).get("command", "")
forbidden = [
    "git push",
    "git merge",
    "git rebase",
    "git checkout",
    "git switch",
    "git reset",
    "git clean",
    "rm -rf /",
    "rm -rf .",
]
if any(bad in cmd for bad in forbidden):
    print(f"Blocked bash command by project policy: {cmd}", file=sys.stderr)
    sys.exit(2)
print("bash ok")
EOT
chmod +x .claude/hooks/bash_guard.py

cat > .claude/hooks/audit_config_change.py <<'EOT'
#!/usr/bin/env python3
import json
import sys
payload = json.load(sys.stdin)
print("ConfigChange observed for review:", payload.get("changed_files", []), file=sys.stderr)
print("config change logged")
EOT
chmod +x .claude/hooks/audit_config_change.py

cat > docs/agent_playbooks/country_done_definition.md <<'EOT'
# Country done definition for Claude team

A country is only ACCEPT if:
- the official-source dossier exists
- the 5 phase-1 buckets are explicitly addressed
- NL parity was checked
- any deviation from NL is documented
- implementation is present
- tests were run
- validation result is stated
- open risks are listed
EOT

cat > docs/agent_playbooks/git_policy.md <<'EOT'
# Git policy for Claude team usage

- Work on the branch already checked out by the human.
- Do not create, switch, merge, rebase, or push branches from Claude Code.
- The human owns:
  - branch creation
  - staging policy
  - commit message policy
  - pull request creation
  - merge decision
- Claude may edit files in the working tree and can show git status/diff for review.
EOT

cat > .claude/README_AGENT_SETUP.txt <<'EOT'
Installed OAM Claude hardening layer.

Key additions:
- NL baseline enforced as default implementation pattern
- 5 phase-1 filing buckets made explicit
- stronger lead/validation instructions
- hooks require better task contracts
- git flow stays human-controlled

Suggested first command:
  claude agents
  claude --agent oam-lead

Suggested first prompt file:
  .claude/prompts/new_country_from_nl.txt
EOT

echo
echo "Done. Hardened Claude team layer installed."
echo "Backup of previous files: $BACKUP_DIR"
echo "Next: run 'claude agents' and then 'claude --agent oam-lead'"
