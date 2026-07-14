export const meta = {
  name: 'issues-creator',
  description: 'Multi-agent codebase audit -- correctness, security, feature gaps, UI/UX (static + driven), and stale docs -- producing a deduplicated, tiered draft issue list',
  phases: [
    { title: 'Recon', detail: 'map the repo, list existing open issues, check UI-driving feasibility' },
    { title: 'Review', detail: 'parallel dimension reviewers, one per audit lens' },
    { title: 'Verify', detail: 'adversarially check every finding before it counts' },
    { title: 'Synthesize', detail: 'dedupe against existing issues, tier, number, split bugs vs feature ideas' },
  ],
}

const repoPath = (args && args.repoPath) || '.'
const requestedKeys = (args && args.dimensions) || null
const driveUI = !(args && args.driveUI === false)

const RECON_SCHEMA = {
  type: 'object',
  properties: {
    repo_summary: { type: 'string', description: 'what this project is, main languages/frameworks, rough size' },
    key_modules: { type: 'array', items: { type: 'string' }, description: 'top-level directories/modules worth knowing about' },
    existing_open_issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          number: { type: 'number' },
          title: { type: 'string' },
          labels: { type: 'array', items: { type: 'string' } },
        },
        required: ['number', 'title'],
      },
    },
    has_github_remote: { type: 'boolean' },
    ui_driveable: { type: 'boolean', description: 'true only if you actually confirmed a device/emulator/browser or dev server is available right now to click through the app' },
    ui_drive_notes: { type: 'string', description: 'what you checked and why it is or is not driveable' },
  },
  required: ['repo_summary', 'existing_open_issues', 'ui_driveable'],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    dimension: { type: 'string' },
    skipped: { type: 'boolean' },
    skip_reason: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          description: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'number' },
          category: { type: 'string' },
          suggested_tier: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          evidence: { type: 'string' },
        },
        required: ['title', 'description', 'category'],
      },
    },
  },
  required: ['dimension', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    confirmed: { type: 'boolean' },
    reasoning: { type: 'string' },
    adjusted_tier: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
  },
  required: ['confirmed', 'reasoning'],
}

const SYNTHESIS_SCHEMA = {
  type: 'object',
  properties: {
    bugs_and_issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          number: { type: 'number' },
          tier: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          category: { type: 'string' },
          title: { type: 'string' },
          body: { type: 'string' },
          file_refs: { type: 'array', items: { type: 'string' } },
        },
        required: ['number', 'tier', 'category', 'title', 'body'],
      },
    },
    feature_ideas: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          number: { type: 'number' },
          rank_tier: { type: 'string', enum: ['P1', 'P2', 'P3'] },
          title: { type: 'string' },
          body: { type: 'string' },
          file_refs: { type: 'array', items: { type: 'string' } },
        },
        required: ['number', 'title', 'body'],
      },
    },
    dropped_as_duplicate: { type: 'array', items: { type: 'string' } },
    skipped_dimensions: { type: 'array', items: { type: 'string' } },
  },
  required: ['bugs_and_issues', 'feature_ideas'],
}

const TIER_RUBRIC = `Tier using this rubric, tiers first then blast-radius within a tier (fix-effort is only a tiebreaker, never the primary driver):
- P0 (Critical): exploitable security issues, data loss, crashes in a core user flow
- P1 (High): functional bugs breaking a main user flow, real accessibility failures, security weaknesses with limited exploitability
- P2 (Medium): edge-case bugs, moderate UX rough edges, missing error handling, hardening opportunities
- P3 (Low): cosmetic issues, minor tech debt, stale docs that don't mislead anyone in a costly way
Feature ideas are never P0 -- they are enhancements, not defects.`

const SECRET_SAFETY = `If confirming or investigating a finding requires looking at an actual secret/credential value (an API key, token, password, private key, or similar), confirm its presence and shape only -- e.g. that a file matching credential format exists at a given path/commit, or that a field name looks like a live key -- WITHOUT decoding, printing, or otherwise reproducing the literal secret value anywhere in your output, evidence field, or tool calls that would echo it back. Reference it by file/commit/field name instead. This applies even when decoding it would make the finding easier to verify -- don't.`

log(`Starting codebase audit on ${repoPath}...`)

phase('Recon')
const recon = await agent(
  `You're kicking off a codebase audit of the repo at ${repoPath}. Produce a short briefing for the reviewers who'll work after you:
1. repo_summary: what this project is, main languages/frameworks/architecture, rough size.
2. key_modules: the handful of top-level directories/modules worth knowing about.
3. existing_open_issues: run \`gh issue list --state open --json number,title,labels --limit 200\` (if this repo has a GitHub remote) and return the full list -- this is used later to avoid proposing duplicates. If there's no GitHub remote or gh isn't authenticated, return an empty array and set has_github_remote=false.
4. ui_driveable: figure out whether it's actually possible right now to launch this project and click through it (a connected device/emulator for a mobile app, a dev server + browser for a web app, etc.). Check for a project-specific launch mechanism first (a README's run instructions, a .claude/launch.json, an existing "run" skill). Only set this true if you've confirmed a way to actually launch it currently exists AND a target (device/emulator/browser) is reachable -- don't assume. Put your reasoning in ui_drive_notes.

${SECRET_SAFETY}`,
  { schema: RECON_SCHEMA, phase: 'Recon', label: 'recon' }
)

const existingIssuesList = (recon.existing_open_issues || [])
  .map((i) => `#${i.number} "${i.title}"${i.labels && i.labels.length ? ' [' + i.labels.join(', ') + ']' : ''}`)
  .join('\n') || '(none)'

log(`Recon done: ${(recon.existing_open_issues || []).length} existing open issues, UI driveable = ${recon.ui_driveable}`)

const ALL_DIMENSIONS = [
  {
    key: 'bugs',
    label: 'Correctness & Bugs',
    isFeatureTrack: false,
    prompt: `Audit the repo at ${repoPath} for genuine correctness bugs: logic errors, race conditions, off-by-one mistakes, incorrect state handling, null/crash risks, broken edge cases. Read real code, don't guess -- and if there's a test suite or linter you can run to raise your confidence, run it. Repo context: ${recon.repo_summary}. Key modules: ${(recon.key_modules || []).join(', ')}.
For each finding: category='bug', suggested_tier per this rubric (${TIER_RUBRIC}), and evidence (the actual snippet or concrete reasoning, not a vibe). Do not flag stylistic nitpicks as bugs -- only things that produce actually-wrong behavior. If you genuinely find nothing, return an empty findings array rather than inventing something.

${SECRET_SAFETY}`,
  },
  {
    key: 'security',
    label: 'Security',
    isFeatureTrack: false,
    prompt: `Audit the repo at ${repoPath} for security issues: hardcoded secrets/credentials, injection risks, insecure storage of sensitive data, missing auth/permission checks, insecure network calls, unsafe deserialization, over-broad permissions. Repo context: ${recon.repo_summary}.
For each finding: category='security', suggested_tier per this rubric (${TIER_RUBRIC}), and evidence. Only flag things with a real, explainable exploit path or genuine data exposure -- not generic "security is important" advice.

${SECRET_SAFETY}`,
  },
  {
    key: 'features',
    label: 'Feature Gaps / Ideas',
    isFeatureTrack: true,
    prompt: `Look through the repo at ${repoPath} (code, README, TODO comments, any developer notes) for genuine feature gaps or enhancement opportunities: missing functionality a user would reasonably expect, explicit TODOs, natural extensions of what already exists. Repo context: ${recon.repo_summary}.
These are not defects -- category='feature', and suggested_tier is a relative VALUE ranking only (P1=high value, P2=medium, P3=nice-to-have), never P0.
Do not propose anything that duplicates one of these existing open issues:\n${existingIssuesList}

${SECRET_SAFETY}`,
  },
  {
    key: 'ui_static',
    label: 'UI/UX (static)',
    isFeatureTrack: false,
    prompt: `Review the UI-layer code in the repo at ${repoPath} (layouts, components, styling) for issues visible from source alone: missing accessibility labels/contentDescriptions, hardcoded user-facing strings that bypass localization, inconsistent theming/spacing, hardcoded colors bypassing the app's theme system, missing error/empty/loading states. Repo context: ${recon.repo_summary}.
category='ui', suggested_tier per this rubric (${TIER_RUBRIC}) -- an accessibility failure is P1, cosmetic inconsistency is P2/P3.

${SECRET_SAFETY}`,
  },
  ...(driveUI
    ? [
        {
          key: 'ui_driven',
          label: 'UI/UX (driven)',
          isFeatureTrack: false,
          prompt: `Recon already checked UI-driving feasibility: ui_driveable=${recon.ui_driveable}. Notes: ${recon.ui_drive_notes || '(none)'}.
If ui_driveable is false, set skipped=true, skip_reason explaining why (quote the recon notes), and return an empty findings array -- do not attempt to guess at UI bugs from code, that's a different dimension's job.
If ui_driveable is true, actually launch the app/site (use whatever mechanism recon identified) and click through its main flows. Take screenshots where useful. Look for REAL visual/interaction bugs you can only catch by running it: overlapping elements, unreadable/cut-off text, broken navigation, crashes on interaction, layout breaking at different sizes. category='ui', suggested_tier per this rubric (${TIER_RUBRIC}).

${SECRET_SAFETY}`,
        },
      ]
    : []),
  {
    key: 'docs',
    label: 'Stale Documentation',
    isFeatureTrack: false,
    prompt: `Compare this project's own documentation (CLAUDE.md/README/AGENTS.md/code comments making specific factual claims) against the actual current code in the repo at ${repoPath}. Repo context: ${recon.repo_summary}.
Flag claims that are now false: outdated version numbers, references to deleted files/functions, "known issues" that were already fixed, setup instructions that no longer work, architectural descriptions that no longer match the code.
category='docs'. Tier: P1 if the stale doc would actively mislead someone into breaking something or wasting significant time, P2 for typical staleness, P3 for trivial/cosmetic doc drift.

${SECRET_SAFETY}`,
  },
]

const DIMENSIONS = requestedKeys ? ALL_DIMENSIONS.filter((d) => requestedKeys.includes(d.key)) : ALL_DIMENSIONS

phase('Review')
const perDimension = await pipeline(
  DIMENSIONS,
  (d) => agent(d.prompt, { schema: FINDINGS_SCHEMA, phase: 'Review', label: `review:${d.key}` }),
  (review, d) => {
    const findings = (review && review.findings) || []
    if (!findings.length) {
      return {
        dimension: d.key,
        label: d.label,
        isFeatureTrack: d.isFeatureTrack,
        skipped: !!(review && review.skipped),
        skip_reason: (review && review.skip_reason) || null,
        verifiedFindings: [],
      }
    }
    return parallel(
      findings.map((f) => () => {
        const verifyPrompt = d.isFeatureTrack
          ? `Sanity-check this proposed feature idea against the repo at ${repoPath}. Idea: "${f.title}" -- ${f.description}${f.file ? ` (near ${f.file})` : ''}.
Set confirmed=false if it is already implemented in the codebase, or if it clearly duplicates one of these existing open issues:\n${existingIssuesList}\nOtherwise set confirmed=true. Default to confirmed=false if genuinely uncertain about a duplicate.`
          : `Adversarially verify this finding from a codebase audit by reading the actual file/behavior in the repo at ${repoPath} yourself -- try to REFUTE it, don't just take it on faith. Finding: "${f.title}" -- ${f.description}${f.file ? ` (file: ${f.file}${f.line ? ':' + f.line : ''})` : ''}. Evidence given: ${f.evidence || '(none provided)'}.
Set confirmed=true only if you independently checked and it holds up. Default to confirmed=false if you can't verify it or the evidence doesn't actually support the claim -- the burden of proof is on the finding.

${SECRET_SAFETY}`
        return agent(verifyPrompt, { schema: VERDICT_SCHEMA, phase: 'Verify', label: `verify:${d.key}` }).then((v) => ({
          ...f,
          dimension: d.key,
          isFeatureTrack: d.isFeatureTrack,
          verdict: v,
        }))
      })
    ).then((verifiedFindings) => ({
      dimension: d.key,
      label: d.label,
      isFeatureTrack: d.isFeatureTrack,
      skipped: false,
      skip_reason: null,
      verifiedFindings: verifiedFindings.filter(Boolean),
    }))
  }
)

const skippedDimensions = perDimension.filter(Boolean).filter((r) => r.skipped).map((r) => `${r.label}: ${r.skip_reason || 'skipped'}`)
const confirmed = perDimension
  .filter(Boolean)
  .flatMap((r) => r.verifiedFindings)
  .filter((f) => f && f.verdict && f.verdict.confirmed)

log(`Review + verify done: ${confirmed.length} confirmed findings across ${perDimension.filter(Boolean).length} dimensions (${skippedDimensions.length} skipped)`)

phase('Synthesize')
const findingsForSynthesis = confirmed
  .map(
    (f) =>
      `- [${f.isFeatureTrack ? 'feature' : f.dimension}] "${f.title}" -- ${f.description}${f.file ? ` (${f.file}${f.line ? ':' + f.line : ''})` : ''}. Suggested tier: ${f.suggested_tier || 'unspecified'}. Verifier tier adjustment: ${f.verdict.adjusted_tier || '(none)'}. Verifier reasoning: ${f.verdict.reasoning}`
  )
  .join('\n')

const synthesis = await agent(
  `You have verified findings from a multi-dimension codebase audit of ${repoPath}. Produce the final draft issue list.

${TIER_RUBRIC}

Verified findings:
${findingsForSynthesis || '(none)'}

Existing open GitHub issues (final duplicate check -- drop anything that clearly duplicates one of these, and list what you dropped in dropped_as_duplicate):
${existingIssuesList}

Produce two SEPARATE numbered lists:
1. bugs_and_issues -- everything that is NOT a feature idea (bugs, security, ui, docs), numbered 1..N in priority order: tier first (P0 > P1 > P2 > P3), then by blast radius within a tier (how many users/flows it affects), using fix-effort only as a last-resort tiebreaker.
2. feature_ideas -- everything from the feature dimension, numbered separately 1..N by relative value, never assigned P0.

For every item write a clear, specific title and a body ready to paste into \`gh issue create --body\` (include concrete file references and enough context that someone could act on it without re-reading this whole audit). Also report skipped_dimensions (merge in this note if relevant: ${skippedDimensions.join('; ') || 'none'}).

${SECRET_SAFETY} If any finding or its evidence contains what looks like an actual secret value, reference it only by file/commit/field name in the issue body -- never reproduce the literal value, since these bodies may end up posted publicly via \`gh issue create\`.`,
  { schema: SYNTHESIS_SCHEMA, label: 'synthesis' }
)

log(`Synthesis done: ${synthesis.bugs_and_issues.length} bugs/issues, ${synthesis.feature_ideas.length} feature ideas, ${(synthesis.dropped_as_duplicate || []).length} dropped as duplicates`)

return synthesis
