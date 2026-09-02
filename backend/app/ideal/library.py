"""The NDA workflow in the new shape.

Two corrections from the first attempt, both of which were real errors:

1. **It is not always a drafting job.** A request arrives one of three ways —
   nothing attached (we draft from the template), their paper attached (we
   review theirs), or our template plus specific terms the requester asked for.
   The first step is therefore "get a working document", not "draft". How that
   document comes to exist is an input, not a different workflow.

2. **A counterparty markup is not special.** It is a new version that needs
   reviewing, exactly like our own draft, so it rejoins the ORDINARY review
   path. Redlining is not a separate stage; it is what ``request_changes``
   means. Modelling it separately duplicated the review logic.

The result is one loop that every version goes round — ours or theirs:

        prepare ──► ai_review ──► legal_review ──► commercial ──► counterparty
           ▲                           │                              │
           └───── request_changes ─────┘                              │
           ▲                                                          │
           └──────────── they returned a markup ──────────────────────┘
"""

from __future__ import annotations

from app.ideal.workflow import (
    AGENT,
    APPROVE,
    APPROVE_WITH_COMMENTS,
    COUNTERPARTY,
    END,
    ESCALATE,
    HUMAN,
    NEED_INFO,
    PAUSE,
    REJECT,
    REQUEST_CHANGES,
    SYSTEM,
    Step,
    build,
)

NDA = build(
    key="nda",
    name="NDA",
    start="prepare",
    steps=[
        Step(
            id="prepare",
            name="Prepare the working document",
            owner="Legal Ops",
            asks=(
                "Produce the version to work from — draft it from our template, "
                "take the counterparty's attached paper, or apply the requester's "
                "terms to our template. Whichever applies."
            ),
            kind=SYSTEM,
            outcomes={
                APPROVE: "ai_review",
                # Nothing usable arrived and we cannot proceed without it.
                NEED_INFO: PAUSE,
            },
        ),
        Step(
            id="ai_review",
            name="Automated review",
            owner="Legal Ops",
            asks=(
                "Compare this version against our standard position and flag what "
                "differs. Runs on OUR draft and on anything the counterparty sends back."
            ),
            kind=AGENT,
            sla_hours=4,
            outcomes={
                APPROVE: "legal_review",
                ESCALATE: "legal_review",   # unsure — a human looks
            },
        ),
        Step(
            id="legal_review",
            name="Legal review",
            owner="Legal & IP",
            asks="Review the flagged points. Approve, mark up for changes, escalate, or reject.",
            kind=HUMAN,
            sla_hours=24,
            mandatory=True,
            outcomes={
                APPROVE: "commercial",
                APPROVE_WITH_COMMENTS: "commercial",
                REQUEST_CHANGES: "prepare",   # the redline — a new version gets made
                NEED_INFO: PAUSE,
                ESCALATE: "gc_review",
                REJECT: END,
            },
        ),
        Step(
            id="commercial",
            name="Commercial review",
            owner="Legal Ops",
            asks="Finance and Quality review together.",
            kind=SYSTEM,
            parallel=("finance", "quality"),
            complete_when="all",
            outcomes={APPROVE: "counterparty", REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="finance",
            name="Finance review",
            owner="Finance & Tax",
            asks="Value, payment terms, transfer pricing.",
            kind=HUMAN,
            sla_hours=24,
            outcomes={
                APPROVE: "counterparty",
                APPROVE_WITH_COMMENTS: "counterparty",
                REQUEST_CHANGES: "prepare",
            },
        ),
        Step(
            id="quality",
            name="Quality & regulatory review",
            owner="Quality & Compliance",
            asks="Any GxP or dossier impact? Does this need a quality agreement?",
            kind=HUMAN,
            sla_hours=24,
            outcomes={
                APPROVE: "counterparty",
                APPROVE_WITH_COMMENTS: "counterparty",
                REQUEST_CHANGES: "prepare",
            },
        ),
        Step(
            id="gc_review",
            name="General Counsel review",
            owner="Legal & IP",
            asks="A gate or an unusual term needs senior sign-off.",
            kind=HUMAN,
            sla_hours=48,
            mandatory=True,
            outcomes={
                APPROVE: "commercial",
                REQUEST_CHANGES: "prepare",
                REJECT: END,
            },
        ),
        Step(
            id="counterparty",
            name="With the counterparty",
            owner="Legal & IP",
            asks="Sent to them. They either accept it, or return a marked-up version.",
            kind=COUNTERPARTY,
            outcomes={
                APPROVE: "sign",
                # Their markup is just another version — it goes round the SAME
                # review loop our own draft does. No separate redlining stage.
                REQUEST_CHANGES: "ai_review",
                REJECT: END,
            },
        ),
        Step(
            id="sign",
            name="Signature",
            owner="Legal & IP",
            asks="Both sides agreed. Collect signatures.",
            kind=HUMAN,
            sla_hours=48,
            outcomes={APPROVE: END, REJECT: END},
        ),
    ],
)

LIBRARY = {NDA.key: NDA}


# ---------------------------------------------------------------------------
# MSA — the harder case, and the one that forced conditional steps.
#
# An NDA is the same shape every time. An MSA is not:
#   * Quality reviews it only if the scope touches GxP work
#   * Privacy reviews it only if personal data is involved
#   * Security reviews it only if the vendor gets systems access
#   * WHO signs depends on the value — legal head, GC, or the board
#
# None of that could be expressed before `Step.when`. Every step in a workflow
# always ran, which is fine for an NDA and wrong for everything bigger.
# ---------------------------------------------------------------------------

CR = 10_000_000  # one crore, in rupees

MSA = build(
    key="msa",
    name="Master Services Agreement",
    start="prepare",
    steps=[
        Step(
            id="prepare",
            name="Prepare the working document",
            owner="Legal Ops",
            asks="Our template, their paper, or our template with the agreed terms.",
            kind=SYSTEM,
            outcomes={APPROVE: "ai_review", NEED_INFO: PAUSE},
        ),
        Step(
            id="ai_review",
            name="Automated review",
            owner="Legal Ops",
            asks=(
                "Flag deviations, and identify the scope signals the later reviews "
                "depend on: GxP work, personal data, systems access."
            ),
            kind=AGENT,
            sla_hours=8,
            outcomes={APPROVE: "legal_review", ESCALATE: "legal_review"},
        ),
        Step(
            id="legal_review",
            name="Legal review",
            owner="Legal & IP",
            asks="Liability, indemnities, IP ownership, termination. Approve, mark up, escalate or reject.",
            kind=HUMAN,
            sla_hours=48,
            mandatory=True,
            outcomes={
                APPROVE: "reviews",
                APPROVE_WITH_COMMENTS: "reviews",
                REQUEST_CHANGES: "prepare",
                NEED_INFO: PAUSE,
                ESCALATE: "gc_review",
                REJECT: END,
            },
        ),
        # Up to six reviewers, but only the ones this deal actually needs.
        Step(
            id="reviews",
            name="Specialist reviews",
            owner="Legal Ops",
            asks="Everyone whose remit this contract touches, in parallel.",
            kind=SYSTEM,
            parallel=("finance", "tax", "procurement", "quality", "privacy", "security"),
            complete_when="all",
            outcomes={APPROVE: "authority", REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="finance", name="Finance review", owner="Finance & Tax",
            asks="Value, payment terms, credit exposure.", kind=HUMAN, sla_hours=48,
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="tax", name="Tax review", owner="Finance & Tax",
            asks="Withholding, permanent-establishment risk, transfer pricing.",
            kind=HUMAN, sla_hours=48,
            # Cross-border only.
            when={"cross_border": True},
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="procurement", name="Procurement review", owner="Procurement",
            asks="Vendor terms, benchmarking, exit provisions.", kind=HUMAN, sla_hours=48,
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="quality", name="Quality & regulatory review", owner="Quality & Compliance",
            asks="GxP impact. Is a separate quality agreement required?",
            kind=HUMAN, sla_hours=72,
            # Only when the scope is GxP — the single most common source of
            # pointless review requests in a pharma CLM.
            when={"gxp": True},
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="privacy", name="Data privacy review", owner="Risk & Compliance",
            asks="Is a DPA needed? Cross-border transfer basis?",
            kind=HUMAN, sla_hours=72,
            when={"personal_data": True},
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="security", name="IT security review", owner="IT / Digital",
            asks="Systems access, data residency, breach notification terms.",
            kind=HUMAN, sla_hours=72,
            when={"systems_access": True},
            outcomes={APPROVE: "authority", APPROVE_WITH_COMMENTS: "authority",
                      REQUEST_CHANGES: "prepare"},
        ),
        # Who signs depends on the money. Three steps, three value bands —
        # exactly one of them applies.
        Step(
            id="authority",
            name="Signing authority",
            owner="Legal Ops",
            asks="Route to whoever may commit the company to this amount.",
            kind=SYSTEM,
            parallel=("approve_head", "approve_gc", "approve_board"),
            complete_when="all",
            outcomes={APPROVE: "counterparty", REQUEST_CHANGES: "prepare"},
        ),
        Step(
            id="approve_head", name="Head of Legal approval", owner="Legal & IP",
            asks="Under ₹1cr — head of legal may commit.", kind=HUMAN, sla_hours=24,
            when={"value_inr": {"lt": 1 * CR}},
            outcomes={APPROVE: "counterparty", REQUEST_CHANGES: "prepare", REJECT: END},
        ),
        Step(
            id="approve_gc", name="General Counsel approval", owner="Legal & IP",
            asks="₹1cr to ₹5cr — the GC signs off.", kind=HUMAN, sla_hours=48,
            when={"value_inr": {"gte": 1 * CR, "lt": 5 * CR}},
            outcomes={APPROVE: "counterparty", REQUEST_CHANGES: "prepare", REJECT: END},
        ),
        Step(
            id="approve_board", name="Board approval", owner="Legal & IP",
            asks="Over ₹5cr — board sign-off required.", kind=HUMAN, sla_hours=120,
            when={"value_inr": {"gte": 5 * CR}},
            outcomes={APPROVE: "counterparty", REQUEST_CHANGES: "prepare", REJECT: END},
        ),
        Step(
            id="gc_review", name="General Counsel review", owner="Legal & IP",
            asks="An unusual term needs senior legal judgement.", kind=HUMAN, sla_hours=48,
            mandatory=True,
            outcomes={APPROVE: "reviews", REQUEST_CHANGES: "prepare", REJECT: END},
        ),
        Step(
            id="counterparty", name="With the counterparty", owner="Legal & IP",
            asks="Sent out. They accept, or return a marked-up version.",
            kind=COUNTERPARTY,
            outcomes={APPROVE: "sign", REQUEST_CHANGES: "ai_review", REJECT: END},
        ),
        Step(
            id="sign", name="Signature", owner="Legal & IP",
            asks="Both sides agreed. Collect signatures.", kind=HUMAN, sla_hours=72,
            outcomes={APPROVE: END, REJECT: END},
        ),
    ],
)

LIBRARY = {NDA.key: NDA, MSA.key: MSA}
