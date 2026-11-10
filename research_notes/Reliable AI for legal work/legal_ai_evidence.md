# Legal-domain evidence on LLMs for contract review, clause/obligation extraction and playbook redlining

Labels used below: **[peer-reviewed]** = conference/journal paper; **[preprint]** = arXiv, not (yet) peer-reviewed; **[independent benchmark]** = third-party evaluation, not peer-reviewed; **[vendor claim]** = the vendor's own numbers; **[not re-verified]** = recalled from the source's abstract and not re-fetched in this session; check before quoting.

## 1. Benchmarks and datasets: what LLM performance and failure modes they show

### Takeaway
Contract benchmarks keep finding the same pattern. Models are fairly precise but miss things: they say "no such clause" when one exists, and recall varies a lot by clause type. Locating the exact text is harder than spotting the issue. Retrieval of the right clause is its own unsolved step.

### Cited Findings
- **CUAD** (Hendrycks et al., NeurIPS 2021 Datasets & Benchmarks) **[peer-reviewed][not re-verified]**: 510 commercial contracts, 41 clause categories, 13,000+ expert annotations. The authors describe transformer performance as "nascent", and it varies strongly by category — [arXiv 2103.06268](https://arxiv.org/abs/2103.06268)
- **ContractEval** (Liu et al., NLLP Workshop 2025) **[peer-reviewed workshop]**, built on CUAD's test split (4,128 data points, 41 categories, 102 contracts):
  - Tested 4 proprietary and 15 open-source LLMs on F1, Jaccard overlap, and a "laziness" score (the rate of wrong "no related clause" answers).
  - Proprietary models scored better. Reasoning mode improved effectiveness but **lowered correctness**. Open-source models answered "no related clause" more often when a clause did exist.
  - The authors put most LLMs at roughly junior-legal-assistant level.
  - Sources: [arXiv 2508.03080](https://arxiv.org/abs/2508.03080); [ACL Anthology 2025.nllp-1.19](https://preview.aclanthology.org/setup/2025.nllp-1.19)
- A 2024 undergraduate thesis on CUAD **[non-peer-reviewed thesis]** found:
  - Claude-3.5-Sonnet reached recall 0.77 / precision 0.66 across all categories (zero-shot, with an output example and reduced context).
  - Narrower context and focused extraction raised recall at the cost of precision.
  - Performance varied by clause type.
  - Source: [UFPE repository](https://repositorio.ufpe.br/handle/123456789/58332?locale=en)
- **ContractNLI** (Koreeda & Manning, Findings of EMNLP 2021) **[peer-reviewed][not re-verified]**: 607 NDAs × 17 fixed hypotheses. Each case needs an entailment label plus the evidence spans. The authors identify evidence spread across several spans and negation-by-exception as the hard cases — [arXiv 2110.01799](https://arxiv.org/abs/2110.01799)
- **MAUD** (Wang et al., EMNLP 2023) **[peer-reviewed][not re-verified]**: an expert-annotated reading-comprehension dataset of merger agreements (ABA public-target deal points), with more than 39,000 examples. Model performance varied widely across deal-point questions — [arXiv 2301.00876](https://arxiv.org/abs/2301.00876)
- **LegalBench** (Guha et al., NeurIPS 2023 Datasets & Benchmarks) **[peer-reviewed][not re-verified]**: 162 tasks built by 40 contributors, organized by legal-reasoning type (issue-spotting, rule-recall, interpretation and others). Many contract interpretation tasks come from CUAD and MAUD — [arXiv 2308.11462](https://arxiv.org/abs/2308.11462)
- **LegalBench-RAG** (Pipitone & Houir Alami, 2024) **[preprint]**: the first benchmark aimed at the **retrieval** step of legal RAG. It has 6,858 expert-annotated query/answer pairs over a corpus of more than 79M characters, built by tracing LegalBench queries back to their source spans. It favors retrieving minimal, precise snippets over whole documents or large chunks — [arXiv 2408.10343](https://arxiv.org/abs/2408.10343)
- **ACORD** (Atticus Clause Retrieval Dataset, ACL 2025 Findings) **[peer-reviewed]**:
  - Expert-annotated clause retrieval for drafting: 114 queries and more than 126,000 query–clause pairs rated 1–5 stars.
  - Covers complex clauses such as limitation of liability, indemnification, change of control and most-favored-nation.
  - The best setup was a bi-encoder retriever with pointwise LLM re-rankers, and the authors say substantial improvement is still needed.
  - Source: [arXiv 2501.06582](https://arxiv.org/abs/2501.06582)
- **arXiv 2508.07849** evaluated legal-specific LLMs against general LLMs on contract-understanding tasks, including LEXDEMOD **[preprint]**. It notes that researchers still favor general-purpose LLMs — [arXiv 2508.07849](https://arxiv.org/html/2508.07849v1)

### Inferences
- The dominant failure mode is a **false negative** ("clause not present"), not invented text. This argues for a deterministic, complete segmentation layer as the coverage backbone, with the LLM labeling segments instead of hunting for them. A miss then shows up as "unlabeled segment", not as nothing at all.
- Reasoning or longer-thinking modes are not automatically better for extraction (ContractEval). Each mode should be measured per task.
- Recall depends on the clause type, so an evaluation should report per-category recall, not one F1 number.

### Gaps
- Exact per-model CUAD numbers for frontier 2025–26 models (Claude 4.x, GPT-5) were not found in this session.
- No benchmark measured **label confusion between similar clause types** (e.g. limitation of liability vs indemnity cap) as a named metric. That may be visible in per-category confusion matrices in the papers' appendices, which were not read.
- MAUD, ContractNLI, CUAD and LegalBench figures were not re-fetched.

## 2. Hallucination studies (Stanford RegLab/HAI and follow-ups) and what reduced errors

### Takeaway
Legal research tools built on retrieval-augmented generation (RAG) hallucinate less than general chatbots, but still on roughly 1 in 6 to 1 in 3 queries. The main finding is that an answer can be "grounded" (it cites a source) yet misgrounded: the cited source does not support the claim. Retrieval quality and citation-to-claim checking matter more than the presence of a citation.

### Cited Findings
- Magesh, Surani, Dahl, Suzgun, Manning & Ho, "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools" (2024) **[preprint; later journal publication not re-verified]**:
  - Lexis+ AI, Westlaw AI-Assisted Research and Ask Practical Law AI each hallucinate **between 17% and 33%** of the time.
  - That is fewer than general chatbots such as GPT-4, but vendor claims of "hallucination-free" are overstated.
  - Responsiveness and accuracy differ substantially between systems.
  - The paper proposes a typology for telling hallucinations apart from accurate legal answers.
  - Source: [arXiv 2405.20362](https://arxiv.org/abs/2405.20362)
- Per-tool numbers as reported in Stanford HAI's coverage **[not re-verified]**: roughly 17% for Lexis+ AI, about 33% for Westlaw AI-AR, and more than 40% for GPT-4. The paper defines a hallucination as an answer that is either incorrect or **misgrounded** (citing a source that doesn't support the claim). It attributes errors to retrieval failures, sycophancy toward false premises in the question, and reasoning errors — [Stanford HAI](https://hai.stanford.edu/news/ai-trial-legal-models-hallucinate-1-out-6-or-more-benchmarking-queries)
- The page describes the paper as meant to inform lawyers' duties to **supervise and verify** AI output — [arXiv 2405.20362](https://arxiv.org/abs/2405.20362)

### Inferences
- For a CLM, the analogue of a misgrounded citation is a finding whose quoted span does not actually say what the finding claims. Two design controls follow:
  - Check deterministically that every cited span exists at the stated offsets in the pinned text.
  - Have a separate step check that the span supports the claim, rather than trusting that the model cited something.
- RAG alone is not the fix; retrieval failures were a named cause. Retrieval should be evaluated separately (LegalBench-RAG, ACORD).

### Gaps
- No peer-reviewed hallucination study specific to **contract review** (as opposed to case-law research) was found.
- Exact per-tool percentages and the journal venue of the Magesh paper were not re-fetched.

## 3. LLM contract review vs lawyers, including consistency across runs

### Takeaway
On issue-spotting in a fixed set of contracts, top LLMs roughly match junior lawyers and outsourced reviewers (LPOs) at a tiny fraction of the time and cost. On **redlining**, lawyers clearly beat the AI tools in the only independent benchmark. AI errors are all-or-nothing, and the dominant miss is concluding "no issue".

### Cited Findings
- Martin et al., "Better Call GPT" (2024) **[preprint; authors are developers of the tools evaluated]**:
  - **Setup:** 10 real procurement contracts (US and New Zealand), with ground truth from a majority of senior lawyers.
  - **Issue determination, F-score:** LPO 0.874, GPT-4-1106 0.871, junior lawyer 0.860, Claude 2.1 0.809, GPT-3.5 0.657.
  - **Issue location, F-score:** LPO 0.770, GPT-4-32k 0.740, Claude 2.1 0.701, junior lawyer 0.667.
  - **Time per contract:** junior lawyer 56 min, LPO 201 min, GPT-4-1106 4.7 min.
  - **Cost per contract:** junior lawyer $74.26, GPT-4-1106 $0.25.
  - **Failure pattern:** LLMs favored precision over recall and often missed issues by concluding none existed. They frequently misread specific wording.
  - **Caveats:** senior lawyers' own inter-annotator agreement was only α = 0.719, and LLM setup and prompt tuning took about 16 hours.
  - Sources: [arXiv 2401.16212 (HTML)](https://arxiv.org/html/2401.16212); [abstract](https://arxiv.org/abs/2401.16212)
- Vals Legal AI Report (VLAIR), February 2025 **[independent benchmark; graded by an LLM judge]**:
  - **Redlining:** lawyer baseline 79.7% (±4.8), Harvey Assistant 65.0%, Vincent AI 53.6%. Redlining was one of only two tasks where lawyers beat every AI tool.
  - **Data extraction:** lawyers 71.1%, Harvey 75.1%, CoCounsel 73.2%.
  - **Document Q&A:** lawyers 70.1%, Harvey 94.8%.
  - AI tools did better on clearly labeled plain text than on formatted redlined documents, and mostly **inserted standard text instead of making nuanced edits**.
  - AI answers scored mostly 100% or 0% per question, while lawyers gave partially correct answers.
  - The redlining set was small (20 questions). The report's prose gives Harvey 59.4% on redlining while its table gives 65.0%, an unexplained discrepancy.
  - LexisNexis withdrew from the evaluation.
  - Source: [Vals VLAIR](https://www.vals.ai/vlair)
- The October 2025 Vals legal-research report covered legal research (Alexi, Counsel Stack, Midpage, ChatGPT), not contracts. Many well-known vendors did not take part — [LawNext](https://www.lawnext.com/2025/10/vals-ais-latest-benchmark-finds-legal-and-general-ai-now-outperform-lawyers-in-legal-research-accuracy.html)

### Inferences
- Issue-spotting against fixed criteria is where LLMs are closest to human level. Writing the actual redline is the weakest step. This supports taking change text from the playbook's pre-approved fallback language where possible, rather than free-form model drafting.
- All-or-nothing errors mean a confident, wrong answer gives no partial-credit signal. Per-item human review matters more than an aggregate accuracy figure.

### Gaps
- **Consistency across repeated runs** (same contract, same prompt) was not measured in Better Call GPT or VLAIR as far as found. Better Call GPT used temperature 0.2 on single runs. No published run-to-run agreement figure for contract review was found; this is a real evidence gap.
- The LawGeex 2018 NDA study (AI 94% vs lawyers 85%) was not re-fetched and predates LLMs.

## 4. How commercial tools design playbook review and redlining

### Takeaway
The industry has converged on a common design:
- Playbooks are turned into explicit per-clause rules, each with a preferred position, fallback position(s) and an escalation trigger.
- Review runs clause by clause, and each clause gets a status (acceptable / needs review / unacceptable, often colored green, yellow, red).
- Redlines come out as native Word tracked changes, with a comment ready to send to the counterparty.
- A lawyer always finalizes.

Nearly all accuracy figures here are **vendor claims**.

### Cited Findings
- **Ironclad** Contract Review Agent with Playbooks (generally available April 2026) **[vendor claim]**:
  - Playbooks are converted into explicit rules with preferred positions, fallbacks and fallback triggers.
  - Ironclad's legal SMEs refine each playbook and validate it against a "golden set" of manually redlined agreements before it is certified for production.
  - Review is clause by clause and takes context into account (whose paper, what is being negotiated).
  - Users toggle each redline between preferred, fallback or none. Each redline carries a counterparty-ready comment, and there is a Word add-in with tracked changes.
  - Claimed results: 66 contracts and 473 redlines checked by 5 lawyers, with 99% grounded in the playbook or contract and 98% of position choices aligned with org standards. It also claims to beat GPT, Claude Opus and Gemini by an average of 31 points on 53 contract/playbook pairs.
  - Source: [Ironclad](https://ironcladapp.com/resources/articles/contract-review-agent-redlining-playbook)
- **Spellbook** **[vendor/third-party blog]**: each playbook rule can carry a fallback position, risk level, reviewer note, preferred language, suggested comment and required approver. Redlines appear as native tracked changes in Word — [Spellbook](https://spellbook.com/learn/spellbook-alternatives); [GC AI](https://gc.ai/blog/contract-redlining-software)
- **LegalOn** **[third-party blogs]**: AI Revise proposes edits through Word Track Changes, with attorney-built playbooks. Sources conflict on the count (50+ vs 135+ playbooks) — [guptadeepak.com](https://guptadeepak.com/tools/top-5-ai-legal-contract-tools-2026/); [spotsaas](https://www.spotsaas.com/blog/ai-contract-review-tools)
- **Luminance** "Auto Redline" is described by a vendor-comparison blog as returning redlined routine agreements **without human review**. This is unconfirmed by Luminance — [guptadeepak.com](https://guptadeepak.com/tools/top-5-ai-legal-contract-tools-2026/)
- A practitioner guide recommends that clauses no playbook rule covers be **flagged for manual review rather than passed silently**, and calls this a useful test when evaluating tools — [bogumilluc.pl](https://bogumilluc.pl/en/lab/agent-workflows/legal-playbook-review)
- Ironclad reportedly matches preferred or fallback wording exactly first and only then uses an AI clause model **[third-party summary, not confirmed in the vendor article]** — [vaquill.ai](https://www.vaquill.ai/blog/best-legal-redline-software)

### Inferences
- The shared pattern combines four controls:
  - A fixed playbook (a closed set of positions).
  - Per-clause assessment.
  - Edits from pre-approved language.
  - A mandatory human step.
- Together these narrow the model's job to classifying a clause against a stated position and picking among approved texts. This plays to measured LLM strengths (issue determination) and away from the weakest one (free drafting, per VLAIR).
- "Golden set" validation per playbook (Ironclad) is the industry form of an eval harness. Each playbook needs its own regression test, not one global test.

### Gaps
- Harvey, Kira/Litera, Thomson Reuters CoCounsel and Robin AI design docs were not fetched in this session (tool-call budget). Their specific playbook mechanics are not covered here.
- No vendor publishes run-to-run consistency figures or independent recall figures for playbook review.

## 5. Professional and regulatory requirements that shape design

### Takeaway
ABA Formal Opinion 512 (July 29, 2024) makes **verifying AI output part of the lawyer's duty of competence**, with no fixed checklist. The amount of review scales with the tool and the task. Supervisors must set policies and train staff, and the lawyer owns the output. Tools should therefore make verification cheap: show the source span, show the rule, and record who accepted each output.

### Cited Findings
- **Opinion 512** (issued July 29, 2024) applies Model Rules 1.1 (competence), 1.6 (confidentiality), 1.4 (communication), 1.5 (fees), 3.1/3.3 (candor, meritorious claims) and 5.1/5.3 (supervision) to generative AI.
  - Relying on AI output without an appropriate degree of independent verification can breach competence.
  - The required level of review is fact-specific and depends on the tool and task.
  - Supervisors should establish policies and training.
  - Fees cover only time actually spent.
  - Sources: [ABA Opinion 512 PDF](https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/ethics-opinions/aba-formal-opinion-512.pdf); [National Law Review](https://natlawreview.com/article/american-bar-association-issues-formal-opinion-use-generative-ai-tools)
- A 2026 ABA commentary notes that Opinion 512 is advisory, predates much of the AI caselaw, and leaves gaps for **subtle errors in technical drafting** — [ABA RPTE, Sept/Oct 2026](https://www.americanbar.org/groups/real_property_trust_estate/resources/probate-property/2026-september-october/artificial-ethics-aba-formal-opinion-512-role-ethics-artificial-intelligence)
- The Stanford authors frame their hallucination findings as bearing directly on lawyers' supervision and verification duties — [arXiv 2405.20362](https://arxiv.org/abs/2405.20362)
- **EU AI Act** (Regulation (EU) 2024/1689) **[not re-verified]**: Annex III lists as high-risk AI intended to assist judicial authorities in researching and interpreting facts and law. Commercial contract review by companies is not listed as such. Article 50 transparency duties and general-purpose AI model obligations still apply — [EUR-Lex 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

### Inferences
- Design requirements that follow from Opinion 512:
  - A per-item human accept/reject step.
  - Visible source citation for every finding.
  - An immutable record of who accepted what, and of which model and prompt version produced it.
  - Org-level policy settings, for example switching off automatic sending.
- A "no human review" auto-redline mode (as claimed for Luminance) sits uneasily with the verification duty, at least for US lawyers.
- An in-house CLM for contract review is most likely not EU-AI-Act high-risk, but explainability and logging are still expected by clients and under GDPR-adjacent practice.

### Gaps
- State bar opinions (California, Florida, New York, Texas) and UK SRA or Law Society guidance were not fetched.
- The exact EU AI Act article text was not re-verified this session.

## 6. Obligation extraction: research and practice for obligations and deadlines

### Takeaway
Academic work frames obligation extraction as deontic-modality classification: who must, may, or must not do what, plus the trigger words. There is no standard LLM-era benchmark for contract obligations with deadlines, and especially none for **event-based deadlines** ("within 30 days after notice"). Evaluation in practice has to be built in-house.

### Cited Findings
- **LEXDEMOD** (Sancheti et al., EMNLP 2022) **[peer-reviewed]**:
  - English contracts annotated with deontic modality **with respect to a contracting party** (agent-specific), plus modal trigger spans.
  - Benchmarked on agent-specific multi-label modality classification and trigger-span detection.
  - Generalizes reasonably from leases to employment and rental agreements.
  - Sources: [ACL Anthology 2022.emnlp-main.795](https://aclweb.org/anthology/2022.emnlp-main.795.pdf); [arXiv 2211.12752](https://ar5iv.labs.arxiv.org/html/2211.12752)
- **Earlier work** (arXiv 1805.03871) frames obligation and prohibition extraction as classifying sentences or clauses by deontic type — [arXiv 1805.03871](https://arxiv.org/pdf/1805.03871v1.pdf)
- **EURO-5K** (2026 preprint) **[preprint, results not read]** benchmarks extraction of EU reporting obligations. Its baselines include rule-based matching of deontic modals ("shall", "must") with reporting verbs, and few-shot Llama-3.1-8B — [arXiv 2606.02971](https://arxiv.org/pdf/2606.02971)
- arXiv 2508.07849 includes LEXDEMOD in its contract-understanding evaluation of legal-specific vs general LLMs — [arXiv 2508.07849](https://arxiv.org/html/2508.07849v1)
- Commercial tools (Ironclad, LegalOn and others) were not found publishing obligation or deadline accuracy figures in this session.

### Inferences
- A defensible obligation record has five parts:
  - The obligated party (agent-specific, as in LEXDEMOD).
  - The modality: obligation, permission or prohibition.
  - The action.
  - The trigger span.
  - A deadline that is either a fixed date or explicitly **event-relative** ("waiting on an event").
- "No date" should be its own state, not an empty field.
- The research supports computing dates in code from extracted anchors (the effective date plus N days) rather than asking the model for calendar dates.
- **How to evaluate it:**
  - Measure per-field accuracy: party, modality, deadline type, date.
  - Measure recall of obligations against expert-annotated contracts.
  - Track a "laziness" (false-"none") rate, as ContractEval does for clauses — [arXiv 2508.03080](https://arxiv.org/abs/2508.03080)

### Gaps
- No public benchmark for **event-based deadline extraction** in contracts was found.
- No peer-reviewed measurement of LLM obligation-extraction recall on full commercial contracts was found.
- EURO-5K's results were not read.
