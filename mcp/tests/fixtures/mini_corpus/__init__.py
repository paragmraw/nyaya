"""Synthetic mini corpus builder for hermetic tests and CI Docker smoke builds."""

from .builder import build_mini_corpus, make_minimal_pdf

__all__ = ["build_mini_corpus", "make_minimal_pdf"]
