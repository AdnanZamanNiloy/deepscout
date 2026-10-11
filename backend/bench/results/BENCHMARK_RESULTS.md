# DeepScout-AI Benchmark Results

*Suite: deepscout-offline-benchmarks v2.0 — generated 2026-10-11T07:53:46Z*

Deterministic, network-free measurement of the intelligence and performance
properties of the upgraded pipeline. All metrics run on hand-labeled
fixtures (see `backend/bench/datasets.py`); every component result is
reproducible with `python bench/run_offline.py` from `backend/`.

## Executive summary

| Component | Headline metrics |
|---|---|
| Citation support | sentence accuracy 1.0000, numeric grounding 1.0000 |
| Contradiction detection | P=1.0000 R=1.0000 F1=1.0000 across 3 kinds ['numeric', 'polarity', 'temporal'] |
| Confidence calibration | 100% in expected band, monotonic ordering: True |
| Semantic engine | Spearman 0.7094 vs labels, dedup F1 0.7500 @ 0.86 |
| LLM response cache | 66% hit ratio on repeat-heavy workload, 293.81x repeat-pass speedup |
Raw numbers: `benchmark_results.json` alongside this file.

## Intelligence detail

### Contradiction engine v2

Numeric (unit-aware), polarity and temporal detectors over the shared semantic
engine. Kinds detected this run: {'numeric': 2, 'polarity': 3, 'temporal': 1}. Pairwise scan latency
6.1300 ms for 12 labeled pairs.

### Confidence engine v2

Scores: {'strong': 0.815, 'moderate': 0.708, 'weak': 0.273, 'conflicted': 0.74}. Contradictions apply a pool-size-scaled penalty;
citation support and axis coverage blend in when present.

## Performance detail

| Operation | Latency |
|---|---|
| Similarity matrix, 60 facts | 403.2 ms |
| Similarity matrix, 200 facts | 1,038.8 ms |
| Cross-similarity, 40 sentences x 200 claims | 24.2 ms |
| Contradiction scan, 60 facts | 493.0 ms |
| Dedup pass, 60 facts | 435.4 ms |
| Answer-support check, 40 sentences | 0.5000 ms |
| Pair similarity (single) | 181.2 µs |

Peak-RSS delta across a 300-fact matrix workload: 22100 KiB —
the engine fits comfortably in the 8 GB RAM budget with the whole stack.

## Known limitations (measured, not hidden)

- Embedding-level paraphrases ("doubled over the past decade" vs "nearly
  doubled in the last ten years") score below the dedup threshold: TF-IDF
  cannot map decade↔ten years. Dedup recall on the labeled set is
 0.6000 (precision 1.0000 — it never merges what it should not).
- The semantic engine is deliberately lexical (CPU-light, 8 GB RAM
  constraint): synonym-level paraphrases are partially handled by the
  stemmer + curated synonym map; deeper equivalence needs embeddings.
- Offline benchmarks exercise deterministic components and a scripted
  end-to-end pipeline. Live-model quality (planner/synthesizer wording)
  is measured by `bench/run_live.py` with real provider keys.

