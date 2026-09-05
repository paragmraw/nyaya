# Trusted Sources — Nyaya Corpus Provenance

This document describes the trust model for the legal data ingested by the Nyaya
MCP server. The corpus is **public-domain legal text** (Indian government edicts,
court judgments) sourced from a combination of official and third-party
aggregators.

## Trust Model

**The corpus is trusted.** The operator is responsible for verifying the integrity
of each upstream source before ingestion. The ingest pipeline applies
[control-character sanitization](mcp/nyaya/sanitize.py) and length caps as
defense-in-depth, but does **not** redact or filter natural-language content.

If you add a user-submission or crowd-sourced upload path in the future, you
MUST implement additional prompt-injection defenses (e.g. output delimiters,
snippet post-processing). See the security review for details.

## Sources

| Source | URL | Data | License | Integrity Check |
|--------|-----|------|---------|-----------------|
| indiankanoon.org | https://indiankanoon.org | Landmark Supreme Court judgments | Public domain (government edicts) | Manual review; no automated checksum |
| PRS Legislative Research | https://prsindia.org | BNS/BNSS/BSA 2023 PDFs | CC BY 4.0 | Manual review; no automated checksum |
| constitutionofindia.net | https://constitutionofindia.net | Constitution articles | Public domain | Manual review; no automated checksum |
| civictech-India (GitHub) | https://github.com/civictech-India/Indian-Law-Penal-Code-Json | IPC, IEA (Evidence Act), CPC JSON | Public domain | Manual review; no automated checksum |
| mratanusarkar/Indian-Laws (HuggingFace) | https://huggingface.co/datasets/mratanusarkar/Indian-Laws | CrPC + commercial statutes (Companies, GST, IT Act, Arbitration, Consumer Protection) bare acts JSON | Public domain | Manual review; no automated checksum |
| Vikhram-S/IndianConstitution (PyPI) | https://pypi.org/project/indianconstitution | Constitution articles | Apache-2.0 | Manual review; no automated checksum |
| NVIDIA API | https://build.nvidia.com | nemotron-3-embed-1b (embeddings), llama-nemotron-rerank-vl-1b-v2 (reranking) | NVIDIA API Terms | API key secured; responses trusted |

## Recommended Hardening (Future Work)

1. **Pin SHA256 hashes** for each upstream artifact in the hydration notebook.
2. **Disable cross-domain redirects** in fetchers, or allow-list redirect targets.
3. **Verify content hashes** before inserting into the database.
4. **Prefer official sources** (egazette.nic.in) for the most sensitive acts (BNS/BNSS/BSA).
5. **Secure NVIDIA API keys** — store in secret manager; rotate periodically; never commit to source control.

## Prompt-Injection Defense (OWASP LLM01)

The corpus text is returned verbatim to LLMs via MCP tools. A compromised upstream
could inject text containing LLM instructions (e.g. "ignore previous instructions").
Current defenses:

- ✅ Control characters stripped at ingest time
- ✅ Row length capped at 200 KB
- ✅ Database CHECK constraint at 1 MB
- ✅ NVIDIA API responses (embeddings, reranking) are from a trusted provider and not user-controllable

Future defenses (not yet implemented):

- ⬜ Wrap tool responses in `<corpus_text>...</corpus_text>` delimiters
- ⬜ Update tool descriptions to instruct the LLM that corpus text is data, not instructions
- ⬜ Post-process retrieved snippets to strip obvious injection patterns