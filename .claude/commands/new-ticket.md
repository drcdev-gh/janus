You are helping the user spec out a new feature ticket for janus.

Your job is to run an interactive conversation that extracts enough detail to write a well-formed ticket, then write the file and offer to start implementation.

## Step 1 — gather information

Ask the following questions **one at a time**, in order. Wait for the answer before asking the next. Do not ask all at once.

1. "What do you want to build or fix? Give me a rough description."
2. "Why is this needed — what problem does it solve or what's driving it?"
3. "What's explicitly out of scope for this ticket? (Anything you want to defer?)"
4. "Any constraints on how it should be built? (e.g. no new tables, specific module, must match existing patterns) — or should I propose an approach?"
5. "Anything else I should know — edge cases, open questions, dependencies on other tickets?"

## Step 2 — draft the ticket

Based on the answers, produce a full draft using the structure below. Use your knowledge of the codebase (from `docs/architecture.md` and the source) to:
- Write a concrete **Proposed approach** if the user didn't specify one
- Write specific, testable **Acceptance criteria**
- Surface any genuine **Open questions** that need resolving before or during implementation
- Scan the existing files in `docs/tickets/` and add a **Related tickets** section listing any tickets that share scope, are dependencies, or are explicitly called out as future follow-ups. Link by filename (e.g. `001-org-audit.md`). Omit the section if there are no related tickets.

Show the draft to the user and ask: "Does this look right, or do you want to change anything?"

Iterate until the user approves.

## Step 3 — write the file

1. Count the existing files in `docs/tickets/` (excluding `_template.md`) to determine the next number.
2. Derive a short kebab-case slug from the title (e.g. `rate-limit-cleanup`).
3. Write the file to `docs/tickets/NNN-slug.md`.
4. Confirm the path to the user.

## Step 4 — offer to implement

Ask: "Want me to start implementing this now, or are you saving it for later?"

If they want to start: read `docs/architecture.md`, re-read the ticket, read any tickets listed in the **Related tickets** section for additional context, then implement.

---

## Ticket format

```markdown
# [Title]

**Status:** open

## Goal

[One paragraph: what this does and why.]

## Background

[Context needed to implement without re-reading the whole codebase.
Reference docs/architecture.md sections where relevant.]

## Scope

[Bullet list of what is in scope.]

## Out of scope

[Bullet list of what is explicitly deferred.]

## Proposed approach

[Concrete implementation plan: which files to touch, what to add/change.
If DB changes are needed, describe the migration.
If new routes are needed, describe them.]

## Acceptance criteria

- [ ] ...
- [ ] ...

## Open questions

[Things to resolve before or during implementation. Delete section if none.]

## Related tickets

[Links to tickets that share scope, are dependencies, or are called out as follow-ups.
Format: `- NNN-slug.md — one-line note on the relationship`
Delete section if none.]
```
