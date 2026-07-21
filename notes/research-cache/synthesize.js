export const meta = {
  name: 'sustag-synthesize',
  description: 'Synthesis-only second half of the sustag future-work research: per-angle synthesis + cross-angle ranking from cached verified claims.',
  whenToUse: 'Run after the verification-only run has banked its results. Pass args = JSON.parse(notes/research-cache/synthesis-input.json).',
  phases: [
    { title: 'Angle synthesis', detail: 'one agent per angle, from surviving claims' },
    { title: 'Cross-angle', detail: 'rank all recommendations against the between-site gap' },
  ],
}

// args = { project, rules, angles: [{angleKey, angleTitle, question, good, alive, dead}] }
// Schemas are inlined below (copied verbatim from the original run) so no JS->JSON parsing is needed.
const PROJECT = args.project
const RULES = args.rules
const ANGLES = (args.angles || []).filter(Boolean)
// Claims verified 3 ways during the original run, recovered from agent transcripts.
// Held to a higher evidentiary bar than the 1-vote claims in ANGLES.
const BANKED = (args.bankedClaims || []).filter((c) => c && c.survivedMajority)

const ANGLE_SCHEMA = {
  type: 'object',
  required: ['angle', 'findings', 'verdict'],
  properties: {
    angle: { type: 'string' },
    verdict: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'confidence'],
        properties: {
          claim: { type: 'string' },
          confidence: { type: 'string' },
          effectSize: { type: 'string' },
          splitProtocol: { type: 'string' },
          nBasins: { type: 'string' },
          sources: { type: 'array', items: { type: 'string' } },
          feasibilityAtN19: { type: 'string' },
          worksAtUngaugedPin: { type: 'string' },
        },
      },
    },
    datasets: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'url'],
        properties: {
          name: { type: 'string' },
          url: { type: 'string' },
          resolution: { type: 'string' },
          years: { type: 'string' },
          access: { type: 'string' },
          license: { type: 'string' },
          coversMultiState: { type: 'string' },
          whyUseful: { type: 'string' },
          howToIncorporate: { type: 'string' },
          limitations: { type: 'string' },
        },
      },
    },
    refuted: { type: 'array', items: { type: 'string' } },
    uncovered: { type: 'array', items: { type: 'string' } },
  },
}

const FINAL_SCHEMA = {
  type: 'object',
  required: ['bottomLine', 'ranked'],
  properties: {
    bottomLine: { type: 'string' },
    ceilingAssessment: { type: 'string' },
    fluidDynamicsAnswer: { type: 'string' },
    ranked: {
      type: 'array',
      items: {
        type: 'object',
        required: ['recommendation', 'category', 'expectedGain', 'effort'],
        properties: {
          recommendation: { type: 'string' },
          category: { type: 'string', description: 'data-new | data-existing | model | reframing | correctness' },
          expectedGain: { type: 'string' },
          effort: { type: 'string' },
          evidence: { type: 'string' },
          feasibilityAtN19: { type: 'string' },
          worksAtUngaugedPin: { type: 'string' },
          howToIncorporate: { type: 'string' },
          risk: { type: 'string' },
        },
      },
    },
    conflicts: { type: 'array', items: { type: 'string' } },
    uncovered: { type: 'array', items: { type: 'string' } },
  },
}

log(`synthesizing ${ANGLES.length} angles from cached verified claims`)

const angleResults = await parallel(
  ANGLES.map((a) => () =>
    agent(
      `${PROJECT}\n${RULES}\n\n## Your assignment: synthesize the findings for angle "${a.angleTitle}"\n\n### Research question\n${a.question}\n\n### What a good answer contains\n${a.good}\n\n### Claims that SURVIVED adversarial verification\n${JSON.stringify(
        (a.alive || []).map((v) => ({
          claim: v.claim?.text,
          url: v.claim?.url,
          effectSize: v.claim?.effectSize,
          splitProtocol: v.claim?.splitProtocol,
          nBasins: v.claim?.nBasins,
          corrections: (v.votes || []).map((x) => x.correction).filter(Boolean),
        })),
        null,
        1
      )}\n\n### Claims that were REFUTED (do not propagate; report them as corrections)\n${JSON.stringify(
        (a.dead || []).map((v) => ({
          claim: v.claim?.text,
          why: (v.votes || []).filter((x) => x.refuted).map((x) => x.evidence).slice(0, 2),
        })),
        null,
        1
      )}\n\n### TIER 3 - UNVERIFIED claims for this angle (${(a.unverified || []).length})\nThese were extracted from real sources but were NEVER adversarially verified - verification was cut to conserve budget. The characteristic failure mode in this corpus is CONFIDENT OVERREACH FROM THIN EVIDENCE, not fabrication: a claim's conclusion is often right while its stated warrant is wrong, its effect size misattributed, or its split protocol misdescribed.\n\nSTRICT RULES for tier 3, which you must follow:\n- USE them for COVERAGE and LEADS: naming a dataset, a URL, an access route, a candidate attribute, or an angle worth pursuing.\n- DO NOT use them as the warrant for any numeric performance figure, any effect size, or the core argument of any recommendation. Those must rest on tier 1/2 claims.\n- Any finding that rests on tier 3 MUST set confidence to "unverified" and say so in the claim text itself.\n- If a tier 3 claim contradicts a verified claim, the verified claim wins; note the conflict.\n${JSON.stringify((a.unverified || []).map((v) => ({ claim: v.claim?.text, url: v.claim?.url, effectSize: v.claim?.effectSize, splitProtocol: v.claim?.splitProtocol, nBasins: v.claim?.nBasins })), null, 1)}\n\nProduce the structured finding set. Merge semantic duplicates. Apply every correction from the verifiers. Rank findings by relevance to the BETWEEN-SITE gap specifically. For each finding give feasibilityAtN19 and worksAtUngaugedPin honestly. Populate "datasets" for every concrete data source, with howToIncorporate written concretely enough to act on - name the feature that would be created and how it would be aggregated to a basin. Populate "uncovered" for any part of the question that produced nothing verifiable - absence of evidence must be reported as absence, never silently dropped. Your "verdict" must state plainly whether this angle is worth pursuing and why.`,
      { label: `synth:${a.angleKey}`, phase: 'Angle synthesis', schema: ANGLE_SCHEMA }
    )
  )
)

const good = (angleResults || []).filter(Boolean)
log(`${good.length}/${ANGLES.length} angles synthesized; running cross-angle ranking`)

const final = await agent(
  `${PROJECT}\n${RULES}\n\n## Your assignment: cross-angle synthesis and ranking\n\nYou have the verified findings from ${good.length} research angles. Produce the final ranked set of recommendations for improving this virtual-sensor classification problem.\n\n### All angle results\n${JSON.stringify(good, null, 1)}\n\n### Additional evidence pool: claims verified by a 3-lens adversarial panel\nThese ${BANKED.length} claims come from the original run and survived majority vote across the primary-source, methodology and transferability lenses. They are held to a HIGHER evidentiary standard than the single-vote claims in the angle results above; where the two conflict, prefer these. Fold them into the ranking rather than reporting them separately.\n${JSON.stringify(BANKED.map((c) => ({ claim: c.claim, url: c.url, effectSize: c.effectSize, kills: c.kills, of: c.nVotes, corrections: (c.votes || []).map((v) => v.correction).filter(Boolean) })), null, 1)}\n\n### How to rank\nThe binding constraint is the BETWEEN-SITE gap (between_r2 0.21, between_rate_r2 0.24) - the model times events well and ranks sites badly. Rank by expected improvement to THAT term, not to aggregate metrics. Weigh effort honestly. Mark anything infeasible at n=19 independent basin families, and mark anything that cannot run at an ARBITRARY UNGAUGED MAP PIN - the latter is disqualifying for the shipped product regardless of how good the method is.\n\nCategorize each recommendation as: data-new (acquire a new dataset), data-existing (better use of what is already on disk), model (architecture or method change), reframing (change the problem formulation or target), correctness (fix something broken).\n\nYou MUST answer the fluid-dynamics question directly and specifically - the user asked whether "basic fluid dynamics additions" would help, and the honest answer must account for the fact that advective travel-time routing was already implemented and already failed, so the answer turns on whether the MASS-BALANCE side (C = load/Q, specific discharge, dilution) is different in kind.\n\nFlag any recommendation that resembles one of the seven known failed ablations, and say what would have to be different for it to work this time.\n\nIMPORTANT PROVENANCE CAVEAT you must state plainly in the bottomLine: this report rests on an UNEVEN evidence base. Of 526 extracted claims, only 37 were adversarially verified (3-lens panel, 30 surviving, 7 refuted); the other 489 are UNVERIFIED and were admitted for coverage only. Verification was cut for budget reasons, not because the remaining claims were judged weak. Two angles - A5-network-topology and A8-target-methodology - have NO verified claims at all and rest entirely on unverified material; say so explicitly. Any recommendation whose support is unverified must be labelled as such in its evidence field.\n\nReport conflicts between angles rather than smoothing them over. Report what remains uncovered.`,
  { label: 'cross-angle-synthesis', phase: 'Cross-angle', schema: FINAL_SCHEMA }
)

return { final, angles: good }
