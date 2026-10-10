# DeepScout Offline Golden Evaluation (v1)

*Generated 2026-10-09T19:00:31Z — deterministic, network-free.*

Golden set: `bench/golden/queries_v1.json`  
Thresholds: `bench/golden/thresholds_v1.json`  
Queries: 26 (scored 26, failed 0)

**Result: PASS** (0 threshold violation(s))

## Aggregate metrics

| Metric | Actual | Floor | Status |
|---|---|---|---|
| query_type_accuracy | 1.0000 | 1.0000 | ok |
| dimension_hit_rate | 0.9487 | 0.8000 | ok |
| forbidden_clean_rate | 1.0000 | 1.0000 | ok |
| section_presence_rate | 0.4000 |  | ok |
| citation_resolution_rate | 1.0000 | 1.0000 | ok |
| verified_claims_mean | 3.6923 | 1.8000 | ok |
| corroborated_claims_mean | 2.2692 | 1.2000 | ok |
| distinct_domains_mean | 2.7692 | 1.4000 | ok |
| primary_share_mean | 0.7679 | 0.6500 | ok |
| grade_ab_share | 1.0000 | 0.9500 | ok |
| answer_quality_mean | 68.6154 | 57.0000 | ok |
| answer_relevance_mean | 0.6088 | 0.3000 | ok |
| report_quality_mean | 9.1146 | 8.6000 | ok |
| report_quality_pass_rate | 0.6538 |  | ok |
| support_rate_mean | 0.5295 | 0.4000 | ok |
| minimums_pass_rate | 1.0000 | 0.9000 | ok |

## Per category

| Category | Queries | Quality | Support | Citation res. | Min-pass |
|---|---|---|---|---|---|
| ambiguous_term | 3 | 64.000 | 0.389 | 1.000 | 1.000 |
| causal | 4 | 70.250 | 0.575 | 1.000 | 1.000 |
| comparison | 4 | 70.500 | 0.617 | 1.000 | 1.000 |
| current_trend | 4 | 63.250 | 0.375 | 1.000 | 1.000 |
| decision_policy | 3 | 69.333 | 0.556 | 1.000 | 1.000 |
| factual_explanation | 5 | 77.600 | 0.680 | 1.000 | 1.000 |
| quantitative | 3 | 60.000 | 0.422 | 1.000 | 1.000 |

## Per query

| id | cat | type ok | dims | sections | cit. res | verified | corrob | domains | quality | min ok |
|---|---|---|---|---|---|---|---|---|---|---|
| factual-rag | factual_explanation | Y | 3/3 | 2/5 | 1.00 | 5 | 2 | 4 | 72 | Y |
| factual-mrna | factual_explanation | Y | 3/3 | 2/5 | 1.00 | 3 | 1 | 2 | 84 | Y |
| factual-transformer-ml | factual_explanation | Y | 3/3 | 2/5 | 1.00 | 3 | 1 | 3 | 69 | Y |
| factual-solid-state | factual_explanation | Y | 3/3 | 2/5 | 1.00 | 2 | 1 | 2 | 88 | Y |
| trend-renewables | current_trend | Y | 3/3 | 2/5 | 1.00 | 8 | 4 | 5 | 57 | Y |
| trend-ai-adoption | current_trend | Y | 3/3 | 2/5 | 1.00 | 1 | 1 | 1 | 65 | Y |
| trend-storage | current_trend | Y | 3/3 | 2/5 | 1.00 | 6 | 4 | 5 | 71 | Y |
| comparison-nuclear-solar | comparison | Y | 3/3 | 2/5 | 1.00 | 7 | 6 | 5 | 67 | Y |
| comparison-lithium-solid | comparison | Y | 3/3 | 2/5 | 1.00 | 3 | 3 | 2 | 72 | Y |
| comparison-rag-finetune | comparison | Y | 3/3 | 2/5 | 1.00 | 4 | 3 | 4 | 76 | Y |
| decision-nuclear-investment | decision_policy | Y | 4/4 | 2/5 | 1.00 | 1 | 1 | 1 | 68 | Y |
| decision-carbon-policy | decision_policy | Y | 4/4 | 2/5 | 1.00 | 3 | 3 | 2 | 71 | Y |
| ambiguous-transformer | ambiguous_term | Y | 3/3 | 2/5 | 1.00 | 3 | 1 | 2 | 64 | Y |
| ambiguous-apple | ambiguous_term | Y | 2/2 | 2/5 | 1.00 | 1 | 1 | 1 | 65 | Y |
| ambiguous-bank | ambiguous_term | Y | 2/2 | 2/5 | 1.00 | 2 | 1 | 2 | 63 | Y |
| causal-rag-hallucination | causal | Y | 2/3 | 2/5 | 1.00 | 5 | 3 | 4 | 76 | Y |
| causal-battery-costs | causal | Y | 2/3 | 2/5 | 1.00 | 3 | 3 | 2 | 69 | Y |
| causal-solar-growth | causal | Y | 2/3 | 2/5 | 1.00 | 8 | 5 | 4 | 68 | Y |
| quant-renewable | quantitative | Y | 2/2 | 2/5 | 1.00 | 4 | 3 | 4 | 64 | Y |
| quant-battery-price | quantitative | Y | 2/2 | 2/5 | 1.00 | 4 | 1 | 3 | 60 | Y |
| quant-ai-investment | quantitative | Y | 2/2 | 2/5 | 1.00 | 2 | 1 | 2 | 56 | Y |
| factual-heat-pumps | factual_explanation | Y | 3/3 | 2/5 | 1.00 | 5 | 2 | 3 | 75 | Y |
| trend-ev-adoption | current_trend | Y | 3/3 | 2/5 | 1.00 | 3 | 1 | 1 | 60 | Y |
| comparison-wind-solar | comparison | Y | 3/3 | 2/5 | 1.00 | 3 | 2 | 2 | 67 | Y |
| causal-nuclear-cost | causal | Y | 2/3 | 2/5 | 1.00 | 6 | 4 | 5 | 68 | Y |
| decision-ai-regulation | decision_policy | Y | 3/3 | 2/5 | 1.00 | 1 | 1 | 1 | 69 | Y |

## Determinism / separation from live judging

- Every number here comes from deterministic, LLM-free scorers and a
  scripted offline pipeline. No model is called and the network is not
  touched. Live/model-judged quality stays in `bench/run_live.py`.
- Citation and numeric metrics are computed over the writer body only:
  `sources.strip_machine_sections` removes the machine-appended sections
  (Evidence integrity, Source ledger, Sources, Limitations, ...) so the
  accounting is never scored as if it were unsupported prose.
