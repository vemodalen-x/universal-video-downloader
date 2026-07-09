# Diagnostic Prompting in VEMO

Diagnostic prompts are useful when an agent must understand a user's current state before it can safely advise, teach,
or plan. Human 3.0 and Mr. Ranedeer are useful reference patterns: one emphasizes structured self-discovery and
constraint location; the other emphasizes configurable tutoring, commands, and feedback loops.

VEMO treats these as workflow patterns, not as prompt text to copy.

## Framework Rule

If a task asks the agent to advise, coach, tutor, onboard, or diagnose a complex human/project state, the workflow should
separate:

1. **Intake** — what must be asked before advice is valid.
2. **Map** — the dimensions used to interpret the situation.
3. **Constraint** — the current bottleneck or misconception.
4. **Plan** — the minimum effective next step and evidence target.
5. **Loop** — how progress is assessed and how the agent adapts.
6. **Boundary** — what the agent refuses, escalates, or leaves to qualified humans.

## Where It Lives

- Generic reusable procedures belong in VEMO_SKILLS, especially
  `orchestration/designing-diagnostic-prompts`.
- Project acceptance gates remain in VEMO: a diagnostic agent can propose a plan, but the consuming project task file
  and `vemo verify` decide whether implementation is accepted.
- Project-specific dimensions, commands, language policy, and evidence thresholds belong in the consuming repo's task
  file, config, or profile.

## Design Checklist

- Does the agent ask before advising?
- Are dimensions few, domain-specific, and actionable?
- Can the user configure tone, depth, language, pace, and assessment?
- Is the binding constraint stated with evidence and uncertainty?
- Is the plan measurable and revisable?
- Are high-stakes boundaries explicit?
- Is any source inspiration cited as provenance rather than copied?

## Anti-Patterns

- Long monolithic prompts with no state model.
- Persona-heavy prompts that hide missing intake.
- "Scores" that do not change the next action.
- Advice that arrives before the user's situation is mapped.
- Hidden chain-of-thought or secret internal notes as a core mechanism.
- Copying external prompt text into VEMO or VEMO_SKILLS.
