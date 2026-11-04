# Practitioner guides to building AI agents for law firms and legal departments (75way article plus comparators)

Research date: 2026-10-28. Every claim has a URL. Vendor self-reports are labelled **[vendor claim]**. Independent or court or regulator sources are labelled **[evidence]**.

## Q1. What does the 75way article recommend, and how good is it?

### Takeaway
The 75way guide is a sound but generic, vendor-written checklist. It describes five components, eight build steps, starting with a single agent, approval gates, permissions at the matter level, audit logs, regression evals and treating prompt injection as a threat. It gives no data, case studies or date, and its cost ranges have no source. Its safeguards are correct but stated only at a high level. Harvey's and Thomson Reuters' posts go into far more detail on the same points.

### Cited Findings
- **Authorship and intent:** written by Salony Gupta, 75way's Chief Marketing Officer. It carries no publication date (it is titled "Guide 2027", with a 2015–2026 copyright footer), and it repeatedly sells 75way's agent development services and its "75AI Agent Store" — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Five-part architecture:** an LLM for interpreting requests and producing structured output; retrieval from authorized sources; workflow orchestration that sequences tasks and handles exceptions; integrations with DMS, practice management, email and CRM; and security and oversight (permissions, approvals, monitoring, audit logs) — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Start simple:** begin with one agent. Use several agents (separate extraction, comparison and validation) only when permissions or specialised processing require it. In its words: "Start with the simplest architecture that meets the requirements" — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Eight steps:**
  1. Pick a workflow, with a measured baseline.
  2. Prepare the data, with versioning, metadata and permissions.
  3. Size the architecture to the risk.
  4. Choose the stack.
  5. Integrate through supported APIs.
  6. Build approval gates and failure handling.
  7. Test on realistic documents, edge cases and permission-bypass attempts.
  8. Run a gradual pilot, then monitor.
  — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Use cases:** intake and lead qualification; contract review against an approved playbook; research memos with citations checked by lawyers; matter administration and document classification, with deadlines verified separately; due diligence and compliance — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Human oversight:** lawyers approve consequential actions and stay accountable. The agent "should not independently approve contractual changes" or make final calls on legal risk — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Confidentiality:** review the vendor's data-retention and training terms; encrypt; use role-based and matter-level access; minimise the data sent to external services — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **RAG:** the article says retrieval from approved sources improves access but does not guarantee accuracy. That caveat is honest — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Audit:** log document IDs, tool calls, decisions and approvals, and keep unnecessary confidential content out of the logs — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Evals:** test extraction accuracy, source support, messy documents, ambiguous instructions, duplicate actions and escalation behaviour, and re-run the tests after changes — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Prompt injection:** treat document and email content as untrusted, allowlist tools, and require approval for consequential actions — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Tool list:** a long list of tools rather than reasoned choices. Models: GPT, Claude, Gemini. Frameworks: LangChain/LangGraph, CrewAI, Semantic Kernel, LlamaIndex, Haystack. Vector DBs: Pinecone, Weaviate, Qdrant, Milvus. OCR: Azure Document Intelligence, Textract, Google Document AI. Legal integrations: Westlaw, Lexis, CourtListener, Clio. Identity and secrets: Auth0, Entra, KMS, Vault. Observability and evals: Langfuse, LangSmith, OpenTelemetry, DeepEval, Ragas — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Costs [vendor claim, no source]:** $15k–$50k for one focused workflow agent; $50k–$150k+ for a multi-system build. No timelines are given. ROI is defined as labour saved (measured after attorney review) minus AI operating cost. The article declines to promise any percentage — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)
- **Regulatory anchors cited by 75way:** ABA Formal Opinion 512 (29 July 2024) and the NIST AI RMF with its GenAI Profile — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)

### Inferences
- **What holds up:** starting from a baseline, choosing the simplest architecture that works, approval gates, matter-level access, logs without confidential content, injection-aware tool allowlists, and regression evals all match what Harvey and Thomson Reuters publish (see Q2 and Q4).
- **What is generic or marketing:**
  - It has no worked example, no eval metrics and no failure stories.
  - It does not say how to build a golden set, or who writes the answer keys.
  - It does not mention ethical walls by name, and it does not discuss permission inheritance from the DMS or conflicts system.
  - It does not mention hallucination statistics or sanctions case law.
  - Its cost figures cannot be checked.
  - The tool list is SEO-style breadth rather than guidance.
- **Its most useful single point:** measure "time saved after attorney review", not raw generation speed. That is the honest ROI measure.

### Gaps
- No publication date, so I could not establish how current the article is.
- No evidence was found behind the cost ranges.

## Q2. How do credible vendors and firms recommend building legal agents (HITL, permissions, privilege, citations, logging, evals)?

### Takeaway
The detailed guidance converges on five practices:
1. Make the matter the hard security boundary, and inherit walls from the DMS and conflicts systems, failing closed (Harvey).
2. Show a plan and get approval before the agent acts, and log every step (Harvey).
3. Ground outputs in gold-standard precedents, with redlines delivered as tracked changes (A&O Shearman ContractMatrix).
4. Grade every claim against its cited authority (Thomson Reuters).
5. Get informed client consent, not boilerplate, before confidential data goes into self-learning tools (ABA 512).

### Cited Findings
- **Harvey, ethical walls (12 Mar 2026, Gabe Pereyra) [vendor guidance]:**
  - Fail closed: if the agent cannot confirm a document is inside the matter boundary, it skips the document and flags it.
  - The matter is the atomic security unit, not a metadata tag.
  - Walls are enforced at the retrieval, context and output layers.
  - Wall configuration is inherited from Intapp (conflicts) and iManage/NetDocuments (DMS) rather than kept as a parallel copy.
  - Persistent agent memory is a cross-matter leakage risk.
  - Every retrieval, context window and session is logged with its matter and wall configuration, so the firm can prove a screen held if a disqualification motion is filed.
  — [Harvey](https://www.harvey.ai/blog/agents-and-ethical-walls)
- **Intapp, silent enforcement:** an AI tool that silently respects a screen leaves no log of the blocked access, so a silent failure leaves no audit trail. Blocked access should therefore be logged too — [Intapp via search summary](https://www.intapp.com/?p=32876) (I saw only the search snippet, not the full page.)
- **Harvey Agent Builder (Mar 2026) [vendor claim]:**
  - Users preview the plan, adjust its scope and approve it before the agent starts.
  - "Every step is logged."
  - Shared agents have Run, View, Edit and Full access levels, and are unavailable to recipients until approved and published by an admin.
  - Harvey says customers have built more than 25,000 custom workflows.
  — [Harvey Agent Builder](https://www.harvey.ai/blog/introducing-agent-builder); [Harvey release note](https://eu.help.harvey.ai/release-notes/simplified-sharing-in-agent-builder); [Harvey agents page](https://www.harvey.ai/en-US/agents)
- **A&O Shearman ContractMatrix [firm/vendor claim]:**
  - Outputs are grounded in a bank of gold-standard precedents and policies "to reduce hallucination".
  - Proposed amendments come back as tracked changes, so a lawyer is always in the loop.
  - It was built as a Word add-in so lawyers work where they already are.
  - Reported users: about 1,000 (2024), 1,900 or about 2,000 (later sources).
  - The firm estimates about 7 hours, roughly 30%, saved per contract review.
  — [Harvey customer page](https://www.harvey.ai/en-US/customers/a-and-o-shearman); [Microsoft 2024](https://microsoft.com/en-gb/industry/blog/cross-industry/2024/08/27/setting-a-precedent-how-ao-shearman-is-embracing-ai-to-transform-the-legal-sector); [A&O Shearman agentic announcement](https://www.aoshearman.com/news/ao-shearman-and-harvey-to-roll-out-agentic-ai-agents-targeting-complex-legal-workflows)
- **Thomson Reuters CoCounsel, citation grading (13 Aug 2026):** each claim is classified as properly supported, uncited, flawed reasoning, fabricated authority, or misattributed. Misattribution is graded by how far the source actually supports the claim — [Thomson Reuters](https://www.thomsonreuters.com/en-us/posts/innovation/when-legal-ai-is-evaluated-like-legal-work-the-results-change/)
- **ITECS practitioner production gate [consultancy opinion]:**
  - Start with one bounded "read-and-propose" workflow.
  - Keep data in a matter-isolated environment.
  - Prove that walls survive every connector, index, cache, memory and log.
  - Ban bulk exports.
  - Put an attorney between the agent and every filing, advice, negotiation or disclosure.
  - If any of this cannot be shown with evidence, the workflow stays a sandbox.
  — [ITECS](https://itecs.ai/insights/legal-ai-agents-protect-privilege-ethical-walls)
- **Prompt injection through retrieved content:** injected instructions can compound across hand-offs in a chain of agents — [law.co](https://law.co/blog/guarding-against-prompt-injection-in-legal-agent-chains) (vendor blog)
- **ABA Formal Op. 512 (29 Jul 2024) [regulator guidance, advisory]:**
  - Existing rules on competence, confidentiality, communication, candour, supervision and fees apply to generative AI.
  - Self-learning tools need informed client consent before confidential data goes in, and boilerplate in an engagement letter is not enough.
  - The depth of verification depends on the tool and on the lawyer's experience with it.
  - Lawyers may not bill for more time than was actually spent.
  — [FKKS summary](https://technologylaw.fkks.com/post/102jwnl/aba-issues-comprehensive-formal-ethics-opinion-on-lawyers-use-of-generative-ai); [ABA Litigation newsletter](https://www.americanbar.org/groups/litigation/resources/newsletters/ethics-professionalism/generative-ai-lawyers-part-2-maintaining-confidentiality/?mode=list); [SimpleLaw](https://simplelaw.com/blog/overview-of-aba-formal-opinion-on-generative-artificial-intelligence) (secondary summaries; I did not read the opinion PDF itself)

### Inferences
- The concrete design points beyond 75way are:
  - Permission inheritance from the DMS and conflicts system, failing closed.
  - Logging blocked access, not only granted access.
  - Isolating cross-matter memory.
  - Tracked changes as the human-review user interface.
  - Grading citations claim by claim.
- **Relevance to a CLM (single-tenant, contract-scoped walls):** scope retrieval and agent context per contract or grant; log every retrieval; deliver AI edits as tracked suggestions tied to an issue. These map directly onto the Harvey and A&O patterns.

### Gaps
- I did not find primary engineering posts from Clifford Chance, LexisNexis (Protégé) or the large consultancies within this tool budget.
- I found no technical detail on Harvey's data retention or training terms.
- The Intapp page was seen only as a snippet.

## Q3. Published failures and lessons, and the design changes they prompted

### Takeaway
Fabricated or garbled citations are the dominant documented failure. Since 2023 the consequences have moved from small fines (Mata, $5k) to disqualification and bar referral (Johnson v Dunn, 2025) and regulator referral in England (Ayinde, 2025). Even purpose-built RAG research tools hallucinated in roughly 1 in 6 or more queries in Stanford's 2024 test. Adoption is largely stuck at the pilot stage. The design lesson is consistent: verify citations automatically and require human sign-off before anything leaves the firm.

### Cited Findings
- **Mata v Avianca (S.D.N.Y., 22 Jun 2023) [evidence]:**
  - $5,000 sanction for six fictitious ChatGPT-generated opinions, with a finding that the lawyers consciously avoided the warning signs.
  - The lawyers had to send the opinion to each judge falsely named as an author.
  — [Bloomberg Law](https://news.bloomberglaw.com/antitrust/chatgpt-phony-legal-filing-case-gets-lawyers-a-5-000-fine); [opinion PDF](https://www.courts.ri.gov:443/PDF/Mata%20v.%20Avianca%20Inc.pdf)
- **Johnson v Dunn (N.D. Ala., order 23 Jul 2025) [evidence]:**
  - Three Butler Snow lawyers were disqualified from the case and referred to the Alabama State Bar.
  - The court said fake authority demands more than "reprimands and modest fines".
  - EDRM frames the case as one where the firm had an AI policy and the policy failed.
  — [EDRM](https://edrm.net/2025/07/when-ai-policies-fail-the-ai-sanctions-in-johnson-v-dunn-and-what-they-mean-for-the-profession/); [Volokh](https://reason.com/volokh/2025/05/21/ai-hallucinated-citation-case-involving-prominent-alabama-firm-with-over-350-lawyers-nationwide)
- **Ayinde v Haringey [2025] EWHC 1040 (Admin) [evidence]:**
  - Five non-existent authorities were cited.
  - The court ordered wasted costs and sent the judgment to the BSB and SRA.
  - The judge did not find that AI was the cause.
  — [Legal Futures](https://www.legalfutures.co.uk/latest-news/judge-condemns-lawyers-who-produced-fake-citations-to-court); [HSF Kramer](https://www.hsfkramer.com/notes/litigation/2025-05/court-lambasts-citation-of-fake-authorities-in-proceedings-and-orders-wasted-costs)
- **Concord v Anthropic (May 2025) [evidence]:**
  - A Latham lawyer used Claude to format a citation. The link was correct, but the title and authors were wrong, and proofreading missed it.
  - The court struck part of the expert declaration.
  - **Lesson:** even "formatting" tasks need verification against the source.
  — [The Register](https://www.theregister.com/2025/05/15/anthopics_law_firm_blames_claude_hallucinations/); [Volokh](https://reason.com/volokh/2025/05/26/judge-strikes-part-of-anthropic-claude-ai-experts-declaration-because-of-uncaught-ai-hallucination-in-part-of-citation/)
- **Scale [evidence, counts vary by source and date]:**
  - Charlotin's database held about 712 decisions by December 2025 (about 90% from 2025), with new ones arriving at 5–6 per day — [Bloomberg Government](https://news.bgov.com/legal-ops-and-tech/ai-faked-cases-become-core-issue-irritating-overworked-judges)
  - An MSBA article (Jul 2026) reports 524 instances in 2025 (97 monetary sanctions, 59 disciplinary referrals) and 668 in 2026 so far — [MSBA](https://www.msba.org/site/content/News-and-Publications/News/General-News/Confronting_the_Hard%20Numbers_Behind_Lawyers_AI_and_Phantom_Precedents.aspx)
  - These two sources' totals are not directly reconcilable.
- **Stanford RegLab/HAI (2024) [evidence]:**
  - Lexis+ AI, Westlaw AI-Assisted Research and Ask Practical Law AI hallucinated in 1 in 6 or more benchmark queries. They did better than GPT-4 but were not "hallucination-free".
  - Per-tool rates of about 17% (Lexis), 33% (Westlaw) and 43% (GPT-4) come from a secondary source that I did not verify against the paper.
  - The vendors dispute the paper's broad definition of hallucination.
  — [Stanford HAI](https://hai.stanford.edu/news/ai-trial-legal-models-hallucinate-1-out-6-or-more-benchmarking-queries); [arXiv 2405.20362](https://arxiv.org/pdf/2405.20362)
- **Adoption [evidence, surveys]:**
  - ILTA 2025 (about 600 firms): 80% use or explore GenAI, but mostly at the pilot stage — [Law360](https://www.law360.com/pulse/articles/2387443/law-firms-embrace-ai-but-full-deployment-remains-rare)
  - ABA 2025: 31% of respondents use GenAI personally at work; firm-level adoption lags, and restrictive firm policies are cited as a cause — [ABA Legal Industry Report 2025](https://americanbar.org/groups/law_practice/resources/law-technology-today/2025/the-legal-industry-report-2025)

### Inferences
- **Johnson v Dunn shows that a written AI policy is not a control.** The control has to be in the workflow: automated checks that each citation exists and supports its claim, plus a mandatory human sign-off step before filing or sending. That is the argument for building verification into the agent rather than relying on training.
- **Concord shows the failure is not limited to research.** Any field the model writes (titles, parties, dates, clause references) needs a check against the source.

### Gaps
- I found no published post-mortem of a failed enterprise legal-agent deployment, as opposed to individual misuse cases.
- I found no data on seat-licence utilisation.

## Q4. Practical LLMOps for legal: golden sets, regression, drift, cost

### Takeaway
The best-documented practice is Thomson Reuters'. Attorney subject-matter experts write test cases and gold answers, which are peer-reviewed. Automated scoring runs at scale, attorneys review outputs, and the full test set is re-run in staging on every model migration. Scorecard tests run several times a day after release. Harvey's BigLaw Bench uses lawyer-written rubrics that penalise hallucinations and score source reliability separately. Independent benchmarks (Vals) add a lawyer baseline. I found little public material on cost control or drift.

### Cited Findings
- **Thomson Reuters Scorecard (26 Sep 2025) [vendor claim]:**
  - Attorney subject-matter experts build test sets of several hundred or more cases, each with a gold-standard answer.
  - Responses are scored on recall, precision and accuracy (1–5 scales), and attorneys review them manually as well.
  - On a model migration, the whole test set is compared old model versus new in staging before deployment.
  - After each prompt change, part of the test set is re-run until performance recovers. One test case went from 1/5 to 4/5 and then passed 99–100% of runs.
  - After release, tests run several times a day.
  - The post does not say whether the automated scorer uses an LLM as judge, and it gives no numeric pass threshold.
  — [Thomson Reuters](https://www.thomsonreuters.com/en-us/posts/innovation/from-testcase-to-trust-benchmarking-cocounsel-with-scorecard/)
- **Thomson Reuters Trust Team [vendor claim]:** attorneys complete tasks by hand to create answer keys, which are peer-reviewed. Thomson Reuters reports more than 1,000,000 tests in total and more than 1,500 tests a night — [Thomson Reuters 2024](https://www.thomsonreuters.com/en-us/posts/innovation/legal-ai-benchmarking-cocounsel/); [Thomson Reuters 2025](https://www.thomsonreuters.com/en-us/posts/innovation/the-brains-behind-the-bot-how-our-lawyers-shape-cocounsel/)
- **Thomson Reuters CoCoBench (13 Aug 2026) [vendor claim]:**
  - "Presence-based" automated scoring rewards mentioning a legal standard without applying it, and passes a real case cited for a proposition it does not support. Attorney review catches both.
  - Gold references should be written by experts, not generated by a model.
  - Accuracy should be tested across long multi-step workflows, because errors compound.
  - Reported results: 50 tasks (about 6 hours of lawyer time each) completed in under 8 minutes; attorneys preferred the AI output over the expert reference on about 40% of tasks.
  — [Thomson Reuters](https://www.thomsonreuters.com/en-us/posts/innovation/when-legal-ai-is-evaluated-like-legal-work-the-results-change/)
- **Harvey BigLaw Bench (Aug 2024 onward) [vendor claim]:**
  - Tasks are derived from lawyers' time entries.
  - Custom rubrics score answer quality and source reliability, with penalties for hallucination.
  - Launch figures: Harvey 74% vs GPT-4o 61% on answer quality; 68% vs 24% on source reliability.
  - An Arena (lawyer preference) version was added in November 2025.
  — [Harvey](https://www.harvey.ai/en-US/blog/introducing-biglaw-bench); [Artificial Lawyer](https://www.artificiallawyer.com/2024/09/03/harvey-launches-legal-genai-evaluation-system-biglaw-bench/); [Harvey Arena](https://www.harvey.ai/blog/introducing-biglaw-bench-arena)
- **Vals VLAIR (Feb 2025) [independent, but vendors opted in]:**
  - Lawyers working without AI beat every tool on redlining and EDGAR research.
  - The best tool beat the lawyers on Document Q&A (94.8% vs 70.1%), summarisation (77.2% vs 50.3%), transcript analysis (77.8% vs 53.7%) and extraction (75.1% vs 71.1%).
  — [Vals](https://www.vals.ai/industry-reports/vlair-2-27-25); [LawNext](https://www.lawnext.com/2025/02/legal-ai-tools-show-promise-in-first-of-its-kind-benchmark-study-with-harvey-and-cocounsel-leading-the-pack.html)
- **Vals VLAIR Legal Research (Oct 2025):** all four tools scored above the lawyer baseline. Thomson Reuters and LexisNexis did not take part — [LawNext](https://www.lawnext.com/2025/10/vals-ais-latest-benchmark-finds-legal-and-general-ai-now-outperform-lawyers-in-legal-research-accuracy.html)
- **Scoring vendor tools:** 75way names DeepEval, Ragas, Langfuse and LangSmith as tools, but describes no eval process — [75way](https://75way.com/blog/build-ai-agent-for-law-firms)

### Inferences
- **A minimal credible LLMOps loop for a legal agent:**
  1. Lawyer-written cases with peer-reviewed gold answers.
  2. Claim-level citation and source checks.
  3. An automated scorer as the regression gate on every prompt or model change.
  4. Periodic lawyer review of a sample, to catch "plausible but unusable" output.
  5. Repeated post-release runs to detect drift or non-determinism.
- **Redlining is the weakest task in Vals.** Any CLM redlining agent therefore needs the strongest human gate.

### Gaps
- I found no credible public source on cost controls specific to legal agents (token budgets, caching, per-matter cost caps).
- I found no public source on drift-monitoring metrics beyond Thomson Reuters' repeated daily runs.
- Several benchmark numbers for later models (Gemini 2.5, GPT-5, BigLaw Bench Global) come only from secondary coverage — [Artificial Lawyer](https://www.artificiallawyer.com/2025/08/08/gpt-5-tops-harveys-biglaw-bench-eval/)
