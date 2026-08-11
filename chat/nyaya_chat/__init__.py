"""nyaya-chat: a LangGraph + FastAPI chat backend for the nyaya legal corpus.

The agent uses a supervisor-synthesis architecture over the nyaya MCP tools,
powered by NVIDIA Nemotron models, streamed to clients over Server-Sent Events.
Conversation state is per-request only (no persistence).
"""

__version__ = "0.1.0"
