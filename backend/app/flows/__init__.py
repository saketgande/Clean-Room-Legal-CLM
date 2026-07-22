"""Legal workflow engine — the single brain that drives a ticket through an
ordered, configurable set of steps (AI / human / draft / approval / signature),
replacing the three ad-hoc flows (intake spine, routing rules, contract enum).

Named ``flows`` (not ``workflows``) because ``app.workflows`` is already the
Prompt Library. UI surface is still called "Workflows".
"""
