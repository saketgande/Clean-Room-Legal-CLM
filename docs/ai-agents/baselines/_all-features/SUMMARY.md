# One call per feature (2026-11-10)

Inputs are tiny made-up samples. Each file: app function result, ledger row, model_output.

| Feature | Status | In tok | Out tok | ms | stop |
|---|---|---|---|---|---|
| clause_extraction | succeeded | 1985 | 1513 | 20309.11473499873 | tool_use |
| clause_hierarchy | succeeded | 1067 | 58 | 1312.266537999676 | tool_use |
| contract_brain_answer | succeeded | 2253 | 242 | 4050.1375749954605 | tool_use |
| contract_docx_generation | succeeded | 1592 | 1962 | 33775.09708300204 | tool_use |
| contract_edit_suggestions | succeeded | 2311 | 654 | 12553.594614000758 | tool_use |
| email_triage_agent | succeeded | 1266 | 75 | 2125.279631000012 | tool_use |
| flow_router | succeeded | 5044 | 245 | 5215.700761000335 | tool_use |
| intake_triage | succeeded | 4575 | 554 | 11453.401785001915 | tool_use |
| litigation_intake_agent | succeeded | 4743 | 635 | 13441.553204000229 | tool_use |
| notice_extraction_agent | succeeded | 648 | 338 | 4641.088468000817 | tool_use |
| notice_response_agent | succeeded | 509 | 1052 | 24977.727737998066 | end_turn |
| obligation_extraction | succeeded | 1413 | 2108 | 22199.55390100222 | tool_use |
| plain_language_summary | succeeded | 288 | 230 | 7197.993801004486 | end_turn |
| playbook_chat_build | succeeded | 1763 | 353 | 5992.0197109968285 | tool_use |
| playbook_expand | succeeded | 923 | 528 | 12884.133175997704 | tool_use |
| playbook_generation | succeeded | 2363 | 2329 | 45888.264995999634 | tool_use |
| playbook_recommendations | succeeded | 1746 | 462 | 9881.777773000067 | tool_use |
| playbook_review | succeeded | 1854 | 1053 | 16107.468881004024 | tool_use |
| portfolio_filter | succeeded | 1651 | 81 | 1736.3407940065372 | tool_use |
| privacy_incident_assessment | succeeded | 1890 | 429 | 9884.140848000243 | tool_use |
| renewal_extraction | succeeded | 1319 | 688 | 7614.047349001339 | tool_use |
| renewal_recommendation | succeeded | 921 | 133 | 3438.2900779964984 | tool_use |
| tabular_cell_extraction | succeeded | 1779 | 180 | 2733.689245003916 | tool_use |
| tabular_review_chat | succeeded | 1281 | 129 | 2229.0428830019664 | tool_use |
| tabular_row_extraction | succeeded | 1974 | 381 | 4870.444546999352 | tool_use |
| trademark_class_suggest | succeeded | 1272 | 86 | 2932.434012000158 | tool_use |
| trademark_conflict_explain | succeeded | 178 | 102 | 4256.370388997311 | end_turn |
| trademark_portfolio_digest | succeeded | 197 | 119 | 3788.338307997037 | end_turn |

Not run: trademark_journal_vision (needs an image), assistant_streaming (chat loop). Captured earlier: intake_form_read, clause_labeling, contract_metadata_extraction.
