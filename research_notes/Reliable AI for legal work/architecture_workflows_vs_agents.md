# Architecture patterns for reliable LLM applications: workflows vs agents, decomposition, structured outputs, deterministic control, multi-agent failure

Research date: 2026-10-28. Sources are 2023–2025 unless noted. "Fetched" means the primary page was read in this session; items marked (not re-fetched) are from well-known papers cited from memory of the abstract and should be spot-checked before quoting numbers.

## 1. How vendors define workflows vs agents, and when they recommend each

### Takeaway
Anthropic and OpenAI agree: start with the simplest thing (a single well-prompted call, then fixed workflows in code), and use an autonomous agent only when the number and order of steps truly can't be known in advance. Anthropic's definition is the useful one for engineering: in a workflow, *code* owns the path; in an agent, the *model* owns it. For contract review, most steps (extract, classify, compare against a playbook, draft a fix) have a known shape and fit workflow patterns: chaining, routing, sectioning, voting, evaluator-optimizer.

### Cited Findings
- Anthropic defines workflows as systems where LLMs and tools are orchestrated through "predefined code paths", and agents as systems where LLMs "dynamically direct their own processes and tool usage" — [Anthropic, Building effective agents (Dec 2024)](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic recommends "finding the simplest solution possible, and only increasing complexity when needed"; for many applications a single LLM call optimized with retrieval and in-context examples is enough. Workflows give predictability and consistency for well-defined tasks; agents fit when flexibility and model-driven decisions are needed at scale — [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- Pattern catalogue and "when to use" (Anthropic):
  - **Prompt chaining**: a sequence of calls, each working on the previous output, with optional programmatic checks ("gates") between steps. For tasks that split cleanly into fixed subtasks; trades latency for accuracy by making each call easier.
  - **Routing**: classify the input, then send it to a specialized prompt or model. For distinct categories that can be classified accurately, by an LLM or a traditional classifier.
  - **Parallelization, sectioning**: independent subtasks run at once, e.g. a guardrail call screening input while another call answers, or separate eval calls each scoring one aspect.
  - **Parallelization, voting**: the same task run several times, e.g. several prompts reviewing code for vulnerabilities, or moderation with vote thresholds to balance false positives and false negatives.
  - **Orchestrator-workers**: a central LLM decides the subtasks *at runtime*, delegates them and synthesizes the results. The difference from sectioning is that the subtasks aren't predefined.
  - **Evaluator-optimizer**: one call generates and another critiques in a loop. Fits when evaluation criteria are clear and iteration adds measurable value.
  - **Agents**: tool use in a loop driven by environment feedback, for open-ended problems where the number of steps can't be predicted. Autonomy means "higher costs, and the potential for compounding errors"; Anthropic recommends extensive testing in sandboxed environments with guardrails.
  - Source: [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic's design principles: simplicity; transparency, by showing planning steps; and careful agent-computer-interface (ACI) design through documentation and testing. Frameworks "often create extra layers of abstraction that can obscure the underlying prompts and responses". "Poka-yoke your tools": change arguments so mistakes are harder to make (example: requiring absolute file paths eliminated a class of errors) — [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- OpenAI: "Agents are systems that independently accomplish tasks on your behalf." LLM apps that "don't use them to control workflow execution" (single-turn LLMs, sentiment classifiers) "are not agents" — [OpenAI, A practical guide to building agents (2025)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- OpenAI's criteria for building an agent: (1) complex decision-making, meaning nuanced judgment and exceptions; (2) rules that are hard to maintain, e.g. vendor security reviews; (3) heavy reliance on unstructured data, e.g. "extracting meaning from documents". "Before committing to building an agent, validate that your use case can meet these criteria clearly. Otherwise, a deterministic solution may suffice." — [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- OpenAI: "maximize a single agent's capabilities first … often a single agent with tools is sufficient". Split into multiple agents only when prompts contain many if-then-else branches, or when tools overlap. Some systems handle more than 15 distinct tools well, while others struggle with fewer than 10 overlapping ones — [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- OpenAI's two multi-agent shapes: **manager** ("agents as tools": one agent keeps control and calls specialists) and **decentralized** (agents hand control to one another). Its advice is to start every task on the most capable model to set an eval baseline, then swap in smaller models where they still pass — [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- Anthropic's multi-agent research system is a worked orchestrator-workers example. A lead Opus 4 with Sonnet 4 subagents beat single-agent Opus 4 by 90.2% on an internal research eval. It used about 15× the tokens of a chat (a single agent uses about 4×), and token usage alone explained 80% of performance variance on BrowseComp. Multi-agent is a poor fit for domains "that require all agents to share the same context or involve many dependencies between agents", including most coding — [Anthropic, How we built our multi-agent research system (Jun 2025)](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic on context: "context rot" means recall from context declines as tokens grow, across all models. The guiding principle is "the smallest possible set of high-signal tokens". Sub-agents work in "clean context windows" and return condensed 1,000–2,000-token summaries; just-in-time retrieval keeps identifiers and loads data with tools — [Anthropic, Effective context engineering for AI agents (Sep 2025)](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Inferences
- How the patterns map onto document review (my mapping, not a vendor's):
  - **Routing**: contract type → playbook.
  - **Sectioning**: one call per clause or per playbook rule, run in parallel.
  - **Prompt chaining with code gates**: extract → validate offsets and quotes in code → classify → draft redline.
  - **Voting**: run high-stakes yes/no questions (e.g. "is liability uncapped?") k times and escalate on disagreement.
  - **Evaluator-optimizer**: redline drafting checked against a rule's acceptance criteria.
- Orchestrator-workers and free agents suit open questions where the steps are unknown up front, such as portfolio Q&A or research. They do not suit a fixed checklist review, where the subtasks are known and should be fixed in code for repeatability.
- Anthropic's evidence that multi-agent gains come largely from spending more tokens suggests a fixed fan-out (one narrow call per clause or rule) captures much of the same benefit, without runtime planning variance.

### Gaps
- Google/DeepMind's "Agents" whitepaper (Wiesinger et al., 2024/2025) was not fetched this session, so Google's framing is not covered here.
- Neither vendor publishes measured head-to-head numbers comparing workflow and agent reliability on extraction tasks. Their guidance is qualitative, apart from Anthropic's research-eval figure.

## 2. Does decomposing "find everything wrong" into narrow, closed sub-questions improve consistency and recall?

### Takeaway
Direct evidence is strong on one side: long, undivided contexts degrade recall, especially for information in the middle and for matches that aren't literal. Evidence that decomposition helps comes from reasoning benchmarks (least-to-most) and from contract-extraction studies where narrower tasks and smaller contexts raised recall. No study found here measures "one big review prompt vs per-clause closed questions" on contracts head-to-head. That is an evidence gap, though the direction is consistent.

### Cited Findings
- Long contexts:
  - **"Lost in the Middle"** (Liu et al., TACL 2023): performance is "often highest when relevant information occurs at the beginning or end of the input context" and degrades in the middle, even for explicitly long-context models — [arXiv 2307.03172](https://arxiv.org/abs/2307.03172). Note: 2023 models; newer models flatten the curve, but item 2 below shows the general degradation remains.
  - **NoLiMa** (Modarressi et al., ICML 2025): when question and answer share few literal words, at 32K tokens 11 models fall below 50% of their short-context baseline. GPT-4o, the top model, drops from 99.3% to 69.7%. Reasoning and CoT don't fix it — [ICML 2025 poster](https://icml.cc/virtual/2025/poster/46685); [PMLR](https://proceedings.mlr.press/v267/modarressi25a.html). The arXiv version reports 10 of 12 models; the counts differ between versions.
  - **Chroma "Context Rot"** (Hong, Troynikov, Huber, Jul 2025): 18 models including GPT-4.1, Claude 4, Gemini 2.5 and Qwen3 grow less reliable as input length rises, in non-uniform ways, even on simple tasks — [Chroma research](https://research.trychroma.com/context-rot). Secondary blogs add specific percentages that I could not verify against the report.
- Decomposition:
  - **Least-to-most prompting** (Zhou et al., ICLR 2023): splitting a problem into ordered subproblems reached ≥99% on SCAN's length split with 14 exemplars, versus 16% for chain-of-thought — [arXiv 2205.10625](https://arxiv.org/abs/2205.10625). Older finding (2022 models) on a synthetic task.
  - **Contract extraction** (UFPE study on CUAD, GPT-3.5, Gemini-1.5-Pro, Claude-3.5-Sonnet, Llama-3-70B): narrower extraction tasks and reduced context tended to *raise recall at some cost to precision*. Claude-3.5-Sonnet, zero-shot with an output example and reduced context, reached 0.77 recall and 0.66 precision across all 41 CUAD categories — [UFPE repository](https://repositorio.ufpe.br/handle/123456789/58332?locale=en). This is an undergraduate thesis, not peer reviewed.
- How hard exhaustive clause recall is:
  - CUAD's headline metric is precision at 80% recall, because precision at 90% recall was near zero for nearly all earlier models — [benchmarklist summary of CUAD](https://benchmarklist.com/benchmarks/cuad/). This is an aggregator; the original is Hendrycks et al., NeurIPS 2021, an older finding.
  - **ContractEval** (Liu et al., Aug 2025; 4 proprietary and 15 open models on CUAD clause-level risk identification): GPT-4.1 and GPT-4.1-mini had the top F1 (≈0.64). Open models more often answer "no related clause" when one exists ("laziness"); Qwen3-8B-AWQ in non-thinking mode exceeded a 30% false-"none" rate. "Thinking" mode improved output effectiveness but *reduced correctness*, "likely due to overcomplicating simpler tasks". Quantization costs accuracy — [arXiv 2508.03080](https://arxiv.org/pdf/2508.03080v1)
- Iterative retrieval: a 2026 TU Delft master's thesis found that LLM-directed iterative retrieval beat single-pass retrieve-and-extract on CUAD's 38 categories (a 4B model's exact match rose from 0.687 to 0.744), because single-pass can't follow cross-references or confirm a clause is *absent* — [TU Delft repository](https://repository.tudelft.nl/file/File_e0c6743a-a0db-46b6-8227-557db173ea98?preview=1). Thesis, not peer reviewed.

### Inferences
- A per-clause or per-rule closed question ("Does this clause cap liability? yes / no / not addressed, plus a quote") keeps each call's context short and puts the evidence at the start of the input. That avoids both the lost-in-the-middle and the NoLiMa failure modes, and the output can be checked item by item, so a miss is visible.
- "None found" answers need a separate check. ContractEval shows false negatives ("no related clause") are a distinct and common error, and the Delft thesis shows absence is the hardest thing for single-pass retrieval to confirm. A deterministic coverage layer, where every parsed segment is labeled, guards against silent misses.
- Higher recall from narrower tasks comes with lower precision (UFPE). Pair narrow, recall-oriented calls with a precision step: a code check that quotes exist, or a second verifying call.
- Reasoning or "thinking" mode is not automatically better for simple classification (ContractEval). Measure it per task rather than turning it on everywhere.

### Gaps
- No peer-reviewed head-to-head on contracts of "single holistic review prompt" vs "decomposed per-clause/per-rule closed questions" measuring both recall and run-to-run consistency was found.
- Decomposed Prompting (Khot et al., ICLR 2023) and self-consistency (Wang et al., 2022/ICLR 2023) are relevant but were not fetched this session. Cite them only after checking.

## 3. Structured outputs and constrained decoding: effect on reliability and reasoning; schema design

### Takeaway
Constrained decoding guarantees *form* (parseable output, valid enums, required fields), not *correctness*. The 2024 claim that JSON formats hurt reasoning ("Let Me Speak Freely?") is contested. A rebuttal traced most of the gap to unequal prompts and parsing, and found structured output equal or slightly better once prompts match. Practical schema rules: closed enums, a quote or evidence field, a short rationale field placed *before* the answer field, and code-side validation of everything the schema can't express.

### Cited Findings
- **"Let Me Speak Freely?"** (Tam et al., Aug 2024) reports "a significant decline in LLMs reasoning abilities under format restrictions", with stricter constraints degrading reasoning more — [arXiv 2408.02442](https://arxiv.org/abs/2408.02442)
- **Rebuttal** (dottxt, "Say What You Mean", 2024). The flaws it identified:
  - The paper's own results show structured output did *better* on classification tasks.
  - NL and JSON prompts differed.
  - The JSON prompts never stated a schema.
  - Answers were parsed with a second LLM (claude-3-haiku) that did worse than a simple regex.
  - The paper conflated JSON-mode with true constrained generation.

  With matched prompts, structured vs unstructured scored GSM8K 0.78 vs 0.77, Last Letter 0.77 vs 0.73, Shuffled Objects 0.44 vs 0.41 — [dottxt blog](https://blog.dottxt.ai/say-what-you-mean.html). The rebuttal comes from a vendor of constrained-decoding tools; treat the dispute as unresolved, but note the original's own classification results favor structure.
- **Anthropic structured outputs** (constrained decoding):
  - What it guarantees: "Always valid" JSON, guaranteed field types and required fields. JSON outputs (`output_config.format`) can be combined with strict tool use (`strict: true`).
  - Two exceptions: on a refusal ("the refusal message takes precedence over schema constraints") or `max_tokens`, output may not match the schema.
  - Unsupported schema features return a 400 error: numeric min/max, string length, recursive schemas, complex types in enums.
  - Enum values may differ from the schema in capitalization; compare case-insensitively.
  - Required properties come first in the output, so property order can differ from the schema.
  - Asking for the model's reasoning in a property can trigger a `reasoning_extraction` refusal; ask for "a short explanation" instead.
  - The docs make no claim about content accuracy.
  - Source: [Anthropic docs, Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- Anthropic tool-design advice: give the model enough tokens to "think" before it writes itself into a corner, keep formats close to text that occurs naturally on the internet, and avoid formatting overhead such as exact line counts and string escaping — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

### Inferences
- For contract classification (clause type, rule pass/fail, risk tier), the tasks where structure did *best* even in the critical paper, closed enums plus constrained decoding are the right default.
- Field order matters with constrained decoding because generation is left to right. Put `evidence_quote` and `short_explanation` before `verdict`, and make all of them required, since optional fields get moved after the required ones.
- Validate in code anything the schema can't enforce: numeric ranges, string lengths, that a quoted span actually exists in the snapshot text, that offsets fall within bounds. Treat `stop_reason` of refusal or `max_tokens` as a failure, never as a partial answer.

### Gaps
- No independent (non-vendor) replication settling the format-restriction dispute on current frontier models was found.
- No published measurement isolates the effect of "rationale-before-answer" field order on accuracy under constrained decoding. The recommendation rests on reasoning about how generation works, not on measured data.

## 4. "Code decides, model proposes": deterministic validation, guardrails and rule layers

### Takeaway
Both vendors describe layered guardrails in which deterministic rules sit alongside model judgments. They recommend risk-rated tools that pause or escalate to a human for high-stakes or irreversible actions, and gates in code between chained steps. Practitioners (Hamel Husain) treat cheap deterministic assertions as both tests and runtime retries. For legal work, this means the model proposes a classification or edit with evidence, and code (or a person) decides whether it is accepted.

### Cited Findings
- OpenAI calls guardrails "a layered defense mechanism", where no single one is enough. The layers combine LLM-based guardrails with rules-based ones such as regex and moderation:
  - Relevance and safety classifiers.
  - A PII filter.
  - **Rules-based protections**: "Simple deterministic measures (blocklists, input length limits, regex filters)".
  - **Output validation**.
  - **Tool safeguards**: rate each tool low, medium or high risk by read/write access, reversibility, permissions and financial impact, and use the rating to pause for checks or escalate to a human.

  Source: [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- OpenAI names two triggers for human intervention: exceeding failure thresholds (retry or action limits), and "High-risk actions … sensitive, irreversible, or have high stakes", which should trigger human oversight "until confidence in the agent's reliability grows" — [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- Anthropic's prompt chaining adds programmatic "gate" checks between steps, and its parallelization pattern includes a separate guardrail call screening input — [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- Hamel Husain: "Unit tests for LLMs are assertions", which can also serve data cleaning and automatic retries at inference time. They should "run fast and cheaply", on every code change. Evaluation has three levels: assertions, then human and model eval, then A/B tests. Check an LLM judge's agreement with human labels, using precision and recall rather than raw agreement when classes are imbalanced. "You must remove all friction from the process of looking at data." — [Hamel Husain, Your AI product needs evals (2024)](https://hamel.dev/blog/posts/evals/)
- Anthropic's recommended eval practice for agents: start with about 20 test cases immediately; use one LLM-judge prompt that outputs a 0.0–1.0 score and a pass/fail grade against a rubric (factual accuracy, citation accuracy, completeness); judge the end state rather than the path; keep human testing for edge cases — [Anthropic multi-agent post](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- How "model proposes, code decides" applies to contract review:
  - The model returns a closed label plus an exact quote and offsets.
  - Code checks that the quote exists in the pinned text.
  - Code maps the label against the playbook rule table, a deterministic lookup, to produce pass, fail or escalate.
  - Code applies thresholds (contract value, authority limits) to gate workflow steps.
  - The model never sets its own confidence as the gate.
- Irreversible or external actions (sending to a counterparty, signing, approving) map to OpenAI's "high-risk tool" category and should always require a person.

### Gaps
- No peer-reviewed neuro-symbolic study specific to contract review was found in this session's budget. The evidence here is vendor and practitioner guidance, not controlled measurement.

## 5. Multi-agent failure modes, compounding error, and consistency metrics (pass^k)

### Takeaway
Multi-agent systems fail often, and mostly for design and verification reasons rather than model capability. MAST catalogs 14 failure modes across 1,600+ traces. Agent loops are stateful, so errors compound. Single-run accuracy also overstates reliability: τ-bench's pass^k (success on *all* k tries) drops sharply as k grows. For auditable legal work, measure repeatability explicitly and prefer short, fixed pipelines.

### Cited Findings
- **MAST** ("Why Do Multi-Agent LLM Systems Fail?", Cemri, Pan, Yang, … Zaharia, Gonzalez, Stoica; Mar 2025, rev. Oct 2025):
  - Built from 150 traces annotated by experts (inter-annotator κ = 0.88), then extended into MAST-Data, 1,600+ annotated traces across 7 multi-agent frameworks.
  - 14 failure modes in 3 categories: system design issues, inter-agent misalignment, task verification.
  - The authors find "improvement headrooms from better MAS design" but say the failures "require more sophisticated solutions" than simple fixes.
  - Source: [arXiv 2503.13657](https://arxiv.org/abs/2503.13657). Per-category percentages were not on the abstract page; see the full paper.
- **τ-bench** (Yao, Shinn, Razavi, Narasimhan; Jun 2024): simulated user plus tool-using agent plus domain policy, scored on whether the final database state matches the goal. GPT-4o function-calling agents succeed on under 50% of tasks, and **pass^8 < 25% in retail**. pass^k is the probability that *all* k independent trials succeed, a consistency metric unlike pass@k — [arXiv 2406.12045](https://arxiv.org/abs/2406.12045)
- Anthropic: "Agents are stateful and errors compound", and "minor system failures can be catastrophic for agents". Mitigations are resuming from checkpoints and retry logic. LLM agents are "not yet great at" real-time coordination and delegation — [Anthropic multi-agent post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic lists "the potential for compounding errors" as the main cost of autonomy — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- OpenAI: maximize a single agent before splitting; more agents add "complexity and overhead" — [OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)

### Inferences
- If one step succeeds with probability p and a run chains n dependent steps without verification, end-to-end success is roughly pⁿ when errors are independent. This is a back-of-envelope model, not a cited result. It is why gates in code between short steps, which catch and stop errors, beat long free loops.
- For auditability, report pass^k (same contract, same playbook, k runs, identical verdicts) next to accuracy. A review that flips between runs fails the legal standard even if its average accuracy is good.
- MAST's "task verification" category argues for an explicit verifier step: either a deterministic quote and offset check, or a separate judge with a rubric.

### Gaps
- MAST's per-failure-mode frequencies and τ-bench's per-model pass^k curves for current (2025–2026) models were not extracted this session.
- No published pass^k-style consistency study for legal document review was found.
