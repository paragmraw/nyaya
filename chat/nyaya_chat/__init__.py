"""nyaya-chat: a LangGraph + FastAPI chat backend for the nyaya legal corpus.

The agent is a ReAct loop over the nyaya MCP tools, powered by an NVIDIA
Nemotron chat model, streamed to clients over Server-Sent Events. Conversation
state is per-request only (no persistence).
"""

__version__ = "0.1.0"
