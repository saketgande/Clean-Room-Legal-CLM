"""What each AI feature must return, as plain Pydantic models.

Pure on purpose: nothing here imports Anthropic, the gateway, the database or a web framework, so the
same models can describe a feature's output under any runner (the Messages API today; LangChain,
LangGraph or the Claude Agent SDK later). How a feature is called (tool name, token budget, model)
stays in ``app/ai/gateway/features.py``; only the shape of its answer lives here.
"""
