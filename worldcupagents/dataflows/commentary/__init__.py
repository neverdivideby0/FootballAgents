"""Commentary ingestion + chunking (COMMENTARY_PLAN.md, slice A→B→C).

This sub-package turns raw post-game text commentary into the five-phase
``PhaseChunk`` timeline that the tactical analyst consumes. The chunker is pure
(no network, no LLM); ingestion providers live alongside it.
"""
