# LLM output consistency: causes of non-determinism and measured mitigations (lens: legal extraction and judgment)

## 1. Why outputs vary run to run, even at temperature 0, on hosted APIs

### Takeaway
At temperature 0 a single forward pass is deterministic. Hosted outputs still vary because the server batches your request with others, and the kernels give slightly different numbers at different batch sizes. Those tiny differences flip a near-tie token, and the rest of the text diverges from that point. Providers say plainly that temperature 0 or a seed gives only "best effort" determinism. Batch-invariant kernels can make inference fully deterministic, but they are slower, and the hosted APIs we use do not offer them.

### Cited Findings
- Thinking Machines Lab (He et al., Sept 2025) argues the common "concurrency + floating point" explanation is incomplete. Typical LLM forward passes use no atomic adds, so they are run-to-run deterministic. The real cause is a lack of **batch invariance**: a request's numerics depend on batch size, and batch size depends on server load. — [Thinking Machines, "Defeating Nondeterminism in LLM Inference"](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
- Their experiment: Qwen3-235B-A22B-Instruct-2507, temperature 0, the same prompt ("Tell me about Richard Feynman"), 1,000 completions of 1,000 tokens. They got **80 unique completions**, and the most common appeared 78 times. All 1,000 matched for the first 102 tokens and diverged at token 103 (992 "Queens, New York" vs 8 "New York City"). With batch-invariant kernels, all 1,000 were identical. — [Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
- Cost of determinism: Qwen-3-8B, 1,000 sequences. Default vLLM took 26 s, unoptimized deterministic vLLM 55 s (~2.1x), and deterministic with an improved attention kernel 42 s (~1.6x). The batch-invariant matmul alone was ~20% slower than cuBLAS. — [Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
- Atil et al. ("Non-Determinism of 'Deterministic' LLM Settings", arXiv 2408.04667, v5 Apr 2025) tested GPT-3.5 Turbo, GPT-4o, Llama-3-70B/8B and Mixtral-8x7B at temperature 0, top-p 1, fixed seed, 10 runs each, on 8 BBH/MMLU tasks. Accuracy varied by up to 15% across runs, and the gap between best and worst possible accuracy reached 70%. No model was consistently repeatable on every task. Insiders attributed this to batching for efficiency, and the authors expect it to persist. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- Extremes from the same paper: Mixtral-8x7B on college math ranged 75% to 3% (a 72-point gap), and GPT-4o few-shot on college math ranged 88.0 to 44.0. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- A follow-up version focused on hosted providers appeared at the 5th Workshop on Evaluation and Comparison of NLP Systems (2025). — [search summary pointing to ACL Anthology / arXiv 2408.04667](https://arxiv.org/abs/2408.04667)
- **OpenAI:** `seed` is "best effort", not a guarantee. `system_fingerprint` identifies the weights, infrastructure and configuration behind a completion, and changes when OpenAI updates its serving stack, "a few times a year". Outputs are "mostly identical" only when seed, parameters and fingerprint all match, and some divergence remains possible even then. — [OpenAI Cookbook: reproducible outputs with seed](https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter)
- **Azure OpenAI:** larger `max_tokens` values generally give less deterministic responses, even with a fixed seed. — [Microsoft Learn: reproducible output](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/reproducible-output)
- Developers report that outputs with matching fingerprints still diverge after the first few words, which makes `seed` useless for unit tests. — [OpenAI community thread](https://community.openai.com/t/seed-param-and-reproducible-output-do-not-work/487245)
- **Anthropic:** several third-party sources quote Anthropic's API reference: "even with temperature of 0.0, the results will not be fully deterministic." I could not find this wording on the current Messages API page, which no longer lists `temperature` in the part I could read. Treat this as reported, not verified first-hand. — [CAMEL-AI Anthropic config docs](https://docs.camel-ai.org/reference/camel.configs.anthropic_config); [aiwiki temperature entry](https://www.aiwiki.ai/wiki/temperature/raw); [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)
- Whether Anthropic exposes a `seed` parameter: sources conflict. One blog implies it does, and a May 2026 post says it does not. I found no seed in Anthropic's docs. — [agno.com article](https://www.agno.com/articles/honor-temperature-0-on-claude-for-deterministic-output); [resumelens blog](https://www.resumelens.org/blog/ai/temperature-and-determinism)
- Anthropic's own "Increase output consistency" guide does not mention temperature or seeds at all. It recommends structured outputs, exact formats, examples, retrieval grounding and prompt chaining. It also notes that prefill is **not supported on Claude 4.6 and later models**. — [Anthropic: Increase output consistency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/increase-consistency)

### Inferences
- For a product on a hosted Claude API, bit-for-bit reproducibility cannot be bought. Design for a *distribution* of outputs. Only self-hosted inference with batch-invariant kernels gives true determinism.
- Divergence is caused by a near-tie at one token and then compounds. Questions where the model is genuinely torn are therefore the ones that flip. Run-to-run disagreement is a signal about the input, not just noise.
- Silent backend changes (OpenAI's `system_fingerprint`; Anthropic has no visible equivalent) mean a regression baseline can drift with no code change on our side. Pin the model version string and re-baseline on model changes.

### Gaps
- I found no first-party, currently published Anthropic statement on temperature-0 determinism, and no measurement of Claude's run-to-run token-level divergence comparable to the Thinking Machines experiment.
- I found no public data on how often hosted providers change serving kernels.

## 2. How variance grows with output length, open-endedness and input length (incl. extraction)

### Takeaway
Longer outputs are reliably less stable. Open-ended, contested judgments (such as "who should win") flip far more than closed tasks. Input length has at most a weak measured effect. Parsing outputs down to an answer recovers much of the agreement lost at the raw-string level. On legal tasks specifically, measured instability is large: 11% to 50% of hard legal questions flipped across 20 temperature-0 runs, and an issue-extraction pipeline on court judgments segmented only 60% of documents identically across two runs.

### Cited Findings
- **Output length (Atil et al.):** output length had a strong or moderate negative correlation with both raw-string agreement (TARr@10) and parsed-answer agreement (TARa@10), and a positive correlation with the max-min accuracy spread. "Every model was more stable when generating shorter responses." The authors suggest capping max generation tokens. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- **Raw vs parsed agreement:** raw-string agreement was low (GPT-3.5 ruin-names TARr 5.6, GPT-4o college math TARr 0.0) while parsed-answer agreement was much higher (GPT-4o college math TARa 50.0; GPT-3.5 navigation TARa 96.4 vs TARr 46.0; Llama-70B navigation 98.4 vs 64.0). — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- **Task type:** the navigation task was the most stable. College math and professional accounting were the most scattered. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- **Compounding:** four chained classifiers, each 95% stable, give about 81% end-to-end stability (0.95^4). — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- **Legal judgment (Blair-Stanek & Van Durme, 2025):** 500 hard legal questions distilled from split-panel US appellate decisions, each asked 20 times at temperature 0. A question was "unstable" if some runs picked party 1 and others party 2. Results: **Claude-3.5 10.6% ±2.7 (53/500), GPT-4o 43.0% ±4.3, Gemini-1.5 50.4% ±4.4**. o1 was unstable on 14 of 50 (28% ±12; a non-random subset). — [arXiv 2502.05196](https://arxiv.org/html/2502.05196v1)
- Same paper: only Gemini-1.5 showed a significant question-length effect (r = -0.121, p = 0.007). Instability correlated only weakly across models (0.123 to 0.181), so it is mostly model-specific. A legal expert found the reasoning in both sides of unstable pairs sound. The models weighed ambiguous standards differently from run to run. Accuracy against actual outcomes was near chance (GPT-4o 53.9%, Claude-3.5 52.9%, Gemini-1.5 46.4%). — [arXiv 2502.05196](https://arxiv.org/html/2502.05196v1)
- **Legal extraction:** "From Judgments to Issues" (arXiv 2607.03325) re-ran an extraction pipeline on 50 court judgments with identical settings, including seed and temperature. Only 30 of 50 (60%) were segmented identically. Issue-level agreement between the two runs was F1 78.3. Citation-level agreement within matched issues was F1 73.2. 7 of 63 aligned issues (11%) shared no reference at all. — [arXiv 2607.03325](https://arxiv.org/pdf/2607.03325) (figures from search-result excerpt; the PDF body could not be parsed to confirm)
- **Prompt-format sensitivity** is a separate source of instability (it varies across prompts, not across runs). Sclar et al. (ICLR 2024) found accuracy gaps of up to 76 points between semantically equivalent prompt formats (LLaMA-2-13B, few-shot). The gaps persisted with larger models, more few-shot examples and instruction tuning, and format rankings transferred poorly between models. — [arXiv 2310.11324](https://arxiv.org/html/2310.11324v2)

### Inferences
- For contract work, keep each model output short and closed: one label, one span ID, one yes/no per call. Avoid long free-form lists. That is where both the length effect and the parsed-vs-raw gap work in our favour.
- Judgment calls on genuinely ambiguous standards (e.g. "is this indemnity market standard?") will flip even on the most stable model. Run-to-run disagreement on those should be surfaced to a person, not hidden.
- Set-valued extraction (lists of issues, citations or obligations) is unstable at the *membership* level (issue F1 ~78 between identical runs). Scoring item by item against a reference set matters more than scoring whole outputs.

### Gaps
- I found no study that measures run-to-run variance specifically on contract clause extraction (CUAD-style). Existing CUAD studies report single-run accuracy only. — [arXiv 2605.05532](https://arxiv.org/pdf/2605.05532)
- I found no clean controlled measurement of input (document) length vs run-to-run variance on extraction.

## 3. Mitigations and their evidence

### Takeaway
The best-supported levers are: (a) make the output closed and short (fixed label sets, structured outputs, one decision per call); (b) sample several times and vote, or keep only items that agree, using disagreement as an abstention / human-review signal; (c) ground answers in verbatim quotes, and drop claims that cannot be backed by a quote; (d) verify with *independent* questions (CoVe), not by asking the model to re-read its own draft. Caching identical inputs removes variance on repeats but does not make the answer right. Few-shot examples help stability on some tasks, but formatting choices can swing results more than the examples do.

### Cited Findings
**Self-consistency / voting**
- Self-consistency (Wang et al., 2022; ICLR 2023): sample diverse reasoning paths and take the majority answer. The abstract reports gains over greedy chain-of-thought of GSM8K +17.9%, SVAMP +11.0%, AQuA +12.2%, StrategyQA +6.4% and ARC-challenge +3.9%. — [arXiv 2203.11171](https://arxiv.org/abs/2203.11171)
- Universal Self-Consistency (Chen et al., 2023; ICML 2024 workshop): concatenate the samples and ask the model to pick the most consistent one. No answer-extraction step is needed, so it works for free-form outputs. It matches standard self-consistency on GSM8K/MATH and matches execution-based voting on code, and it improves open-ended QA and summarization. — [arXiv 2311.17311](https://arxiv.org/abs/2311.17311v1)
- Extraction with voting: a 2026 clinical SDOH event-extraction study ran the model 3 times per note, kept the events that agreed most across runs, and reported improved F1. — [arXiv 2604.13502](https://arxiv.org/pdf/2604.13502)
- For free-text answers, exact-match voting fails. Alternatives are semantic clustering of answers (EvidenceRL) and latent / embedding-based consistency selection. — [arXiv 2603.19532](https://arxiv.org/pdf/2603.19532); [arXiv 2508.18395](https://arxiv.org/pdf/2508.18395v3)
- Ranked-choice voting (Borda, instant-runoff) over ranked candidates outperforms plain majority in some settings. — [arXiv 2505.10772](https://arxiv.org/pdf/2505.10772)
- Certified Self-Consistency (2025): majority voting gives a statistical guarantee that the answer matches the model's modal answer, and it gives a sequential stopping rule for how many samples to draw. Stop early when agreement is already clear. — [arXiv 2510.17472](https://arxiv.org/html/2510.17472v2)
- Anthropic recommends "Best-of-N verification": run the same prompt several times and compare; "inconsistencies across outputs could indicate hallucinations." — [Anthropic: Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)

**Grounding, abstention, verification**
- Anthropic: explicitly allow "I don't know" ("can drastically reduce false information"). For documents over 20k tokens, extract word-for-word quotes first, then do the task using only those quotes. After drafting, find a supporting quote for each claim and retract any claim without one. — [Anthropic: Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- Anthropic also notes that these techniques "don't eliminate [hallucinations] entirely" and advises validating critical information for high-stakes decisions. — [Anthropic: Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- Chain-of-Verification (Dhuliawala et al., Findings of ACL 2024): draft an answer, plan verification questions, answer them **independently of the draft**, then revise. On Wikidata list questions it more than doubled precision, mainly by removing hallucinated entities. The independence step matters: answering with the draft in context lets the model repeat its error. — [ACL Anthology 2024.findings-acl.212](https://preview.aclanthology.org/add_acl24_videos/2024.findings-acl.212); [arXiv 2309.11495 summary](https://www.deepai.org/publication/chain-of-verification-reduces-hallucination-in-large-language-models)

**Format and label constraints**
- Anthropic: for guaranteed schema conformance use Structured Outputs. Otherwise specify exact formats, give examples ("more effective than abstract instructions"), ground answers in a fixed retrieved set, and chain prompts so each covers one subtask, "reducing inconsistency errors across scaled workflows". — [Anthropic: Increase output consistency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/increase-consistency)
- Few-shot vs zero-shot (Atil et al.): few-shot often had higher median accuracy and parsed agreement (e.g. GPT-4o navigation TARa 99.6 few-shot vs 91.6 zero-shot), but zero-shot showed a similar *degree* of non-determinism overall. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- Contested: Sclar et al. found that adding few-shot examples did **not** remove sensitivity to prompt formatting. Examples are not a stability guarantee, and the format itself should be fixed and tested. — [arXiv 2310.11324](https://arxiv.org/html/2310.11324v2)

### Inferences
- For clause typing and other closed-label extraction: use a closed label set, structured output, and 3 samples with a majority vote. Unanimous answers are accepted. Split votes go to a person, or are marked as low confidence. The cost is ~3x tokens on that step only. Certified-SC-style early stopping (stop at 2/2 agreement) cuts most of that.
- For set extraction (obligations, issues, citations): run N times and keep items that appear in at least k runs (the SDOH approach). Every kept item must carry a verbatim quote that is string-matched against the source text, so code (not the model) checks the quote exists.
- Result caching keyed on (model version, prompt version, exact input hash) makes repeat views of the same document return the same answer. That is a consistency guarantee, not a correctness one. Provider *prompt* caching only reuses prefix computation, so it does not by itself fix the output. (This is an inference from how prompt caching is documented; I did not find a source that measures its effect on output variance.)
- Use CoVe-style verification only with independent sub-questions. A "critic reads the draft and fixes it" pass lacks the independence that CoVe credits for its gains.

### Gaps
- I found no published measurement of agreement-based abstention (e.g. "accept only unanimous 3-of-3") on contract extraction, giving coverage vs accuracy curves.
- I found no study that isolates the effect of the "quotes first" instruction on run-to-run stability (as opposed to hallucination rate).
- I found no source quantifying when verification/critic passes *hurt* (e.g. a critic deleting correct items). This is commonly asserted, but I found no number.

## 4. Measuring consistency: metrics, regression sets, LLM-as-judge reliability

### Takeaway
Measure both "does it ever get it right" and "does it always get it right". pass^k (all k trials succeed) from tau-bench is the reliability metric, and it decays sharply. Report agreement on parsed answers, not raw strings, and report the spread across runs, not one number. LLM judges must themselves be validated against a single domain expert's binary labels, using true-positive / true-negative rates rather than raw agreement. Criteria drift, so expect to re-grade when outputs change.

### Cited Findings
- **pass^k (tau-bench, Yao et al., 2024):** the probability that *all* k independent trials of a task succeed, averaged over tasks. The unbiased estimator is E[C(c,k)/C(n,k)] for n trials with c successes. It is the reliability counterpart of pass@k. GPT-4o fell to ~25% pass^8 on tau-retail, a ~60% drop from its pass^1. — [arXiv 2406.12045](https://arxiv.org/pdf/2406.12045); [Sierra blog](https://sierra.ai/uk/blog/benchmarking-ai-agents)
- ReliabilityBench: if runs were independent, pass^k = (pass^1)^k, but correlated runs make the real values deviate from that. — [arXiv 2601.06112](https://arxiv.org/pdf/2601.06112)
- **TARr@N / TARa@N** (Atil et al.): total agreement rate across N runs on raw strings vs on parsed answers. The authors recommend reporting the max-min accuracy across runs, building regression tests that tolerate variability, and writing parsers that tolerate format variation. — [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)
- **LLM-as-judge (Hamel Husain):** use binary pass/fail, not 1-5 scales. Use one principal domain expert, not a developer or a proxy. Every label gets a written critique detailed enough to reuse as a few-shot example. — [Hamel Husain, "Creating a LLM-as-a-Judge That Drives Business Results"](https://hamel.dev/blog/posts/llm-judge/)
- Raw agreement misleads when failures are rare: a judge that always says Pass scores 95% when 5% of cases fail. Report TPR and TNR separately against the expert's labels. Split the data into train/dev/test, freeze the prompt before the test set, and aim for 30-50 examples per label in dev and in test. Below ~60 examples, confidence intervals are too wide. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- The Honeycomb case reached >90% judge-expert agreement in three iterations on a roughly balanced (~50% fail) dataset. That figure is specific to its dataset, not a general benchmark. Re-review after material changes such as a model update. — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)
- A practitioner (cited by a secondary source) treats 100% judge-human agreement as a likely defect (sycophancy), not success. — [Simon Willison / secondary summary](https://simonwillison.net/2024/Oct/30/llm-as-a-judge)
- **"Who Validates the Validators?" (Shankar et al., UIST 2024):** LLM-generated evaluators "inherit all the problems of the LLMs they evaluate" and need human validation. EvalGen proposes criteria and assertions, has a person grade a subset, and keeps the implementations that best match the grades. **Criteria drift:** people define their criteria *by* grading outputs, some criteria depend on the specific outputs seen, and so a pipeline change may require re-grading. — [arXiv 2404.12272](https://arxiv.org/pdf/2404.12272)
- In the legal setting, models agreed with *each other* (GPT-4o/Claude-3.5 79%) far more than with courts (~50%). Cross-model agreement is therefore not evidence of correctness, which is relevant to using one LLM to judge another. — [arXiv 2502.05196](https://arxiv.org/html/2502.05196v1)

### Inferences
- A legal-extraction regression set needs: a frozen set of real contracts with expert-labelled spans and labels; each case run k times (e.g. k=3-5); and reports of per-field accuracy, pass^k, and set agreement (Jaccard or item-level F1) between runs and against gold. Re-baseline whenever the model version or prompt version changes.
- For judgment tasks (deviation severity, "is this acceptable?"), agreement with one designated lawyer's binary labels, reported as TPR/TNR, is the target. Cohen's kappa against that lawyer is the natural chance-corrected version.

### Gaps
- I found no source giving recommended kappa thresholds, or reported Cohen's/Fleiss' kappa between LLMs and lawyers, on contract review tasks.
- I found no published use of Jaccard specifically as a run-to-run consistency metric for extracted sets (the legal judgments paper used F1 between runs instead).

## 5. Output truncation / max_tokens failures in long extraction

### Takeaway
Truncation is a silent failure unless the code checks `stop_reason`. Anthropic says to treat `max_tokens` and `model_context_window_exceeded` as incomplete output: raise the limit or continue the response, and never parse a cut-off tool call. Structurally, the fix is to stop asking for whole-document outputs. Extract per section or per segment, keep each response short, and have the model return IDs/offsets instead of re-copying text. Shorter outputs are also more stable (section 2).

### Cited Findings
- Anthropic's `max_tokens` is an absolute ceiling, and models may stop before it. — [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)
- `stop_reason: "max_tokens"` means "The response reached your `max_tokens` limit." The recommended action is "Raise `max_tokens` or continue the response." A truncated response with an incomplete tool-use block must be retried with a higher `max_tokens`. When output is truncated, "append a notice so the reader knows the output is incomplete." — [Anthropic: Handling stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)
- `model_context_window_exceeded` means the response filled the context window, and it should be treated as truncated. `refusal` arrives as an HTTP 200 with `stop_details`, and the recommended action is to retry on a fallback model. — [Anthropic: Handling stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)
- Anthropic recommends chaining prompts so each covers a single subtask, and for long documents (>20k tokens) extracting quotes first. — [Anthropic: Increase output consistency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/increase-consistency); [Anthropic: Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- Larger `max_tokens` / longer outputs are less deterministic. — [Microsoft Learn](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/reproducible-output); [arXiv 2408.04667](https://arxiv.org/html/2408.04667v5)

### Inferences
- Pattern for long contracts: split deterministically (by the parser's sections/clauses), send the model segment IDs plus short excerpts, have it answer ID → label, and let code attach the exact text/offsets. This keeps output size roughly constant regardless of document length. Fail the job on any `max_tokens` / `model_context_window_exceeded` stop. Never partially store a truncated list as if it were complete.
- Paginate when a single section is still too large (ask for items N..M, or "continue from item X"), and verify completeness in code: every segment received exactly one answer.

### Gaps
- I found no published benchmark comparing chunked/per-section extraction against whole-document extraction for recall and stability on contracts specifically.
- I did not read Anthropic's "Ensuring complete responses" continuation section in full (only the summary pointer).
