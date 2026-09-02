"""``ideal`` — the redesigned engine, built beside the running application.

Nothing in this package is imported by ``app.main`` or any existing module. It
has no routes, no tables and no side effects. You can delete the directory and
the application is unchanged. That isolation is the point: the redesign can be
built and judged without putting the live system at risk.

See REDESIGN_PROPOSAL.md at the repo root for the reasoning. The short version:

    The application models legal work as a pipeline. It is a negotiation.

The existing engine (``app/flows``) walks an ordered list forwards — its step
index is incremented in nine places and decremented in none — so a reviewer who
says "change clause 7" cannot send the work back. The run is simply marked
failed. Rework, escalation and negotiation rounds are unrepresentable.

What this package does differently, in one sentence:

    A step is a DECISION WITH TYPED OUTCOMES, and each outcome names where it goes.

That single change buys send-backs, rework rounds and escalation without a
workflow product. Parallel review and recorded deviation are the other two
pieces.

Deliberately built as a PURE state machine — no database, no framework, no I/O.
``advance()`` takes a state and returns a new state. That makes the whole engine
testable without infrastructure, and means persistence can be chosen later
rather than baked in.

Layout:
    workflow.py   the spec (what a workflow IS) and the engine (how it moves)
    library.py    the NDA workflow expressed in the new shape, for comparison
"""
