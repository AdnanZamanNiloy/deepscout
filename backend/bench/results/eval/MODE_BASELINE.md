# Mode baseline (Step 0)

Generated: 2026-10-15T19:22:01+00:00

Measured with `bench/run_mode_baseline.py` against live providers and live retrieval. These are observations, not targets.


## Headline

| mode | ok/total | wall median | wall p90 | wall max | LLM calls | search | sections | body secs | domains | words | overlap | conf | degraded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| quick | 10/10 | 115.935s | 136.2s | 143.2s | 16 | 17 | 3 | 2.4 | 5.4 | 6230.5 | 0.015 | 0.531 | no |
| standard | 10/10 | 158.495s | 191.3s | 198.4s | 22.7 | 25 | 5 | 3.7 | 5 | 8880.4 | 0.013 | 0.549 | no |
| deep | 10/10 | 186.295s | 235.3s | 243.2s | 30.3 | 37 | 8 | 6.6 | 7.7 | 13711.7 | 0.041 | 0.55 | no |

## Same query across the three modes (the comparison the protocol asks for)

| query | mode | wall | sections | domains | words | overlap | contradictions | limitations | degraded |
|---|---|---|---|---|---|---|---|---|---|
| How does quantum computing work, and wha… | quick | 143.25s | 3 | 26 | 6324 | 0.0168 | 0 | no | no |
| How does quantum computing work, and wha… | quick | 92.06s | 3 | 5 | 7210 | 0.025 | 0 | no | no |
| How does quantum computing work, and wha… | standard | 120.76s | 3 | 3 | 7170 | 0.0054 | 0 | no | no |
| How does quantum computing work, and wha… | standard | 180.39s | 5 | 5 | 11690 | 0.0129 | 0 | no | no |
| How does quantum computing work, and wha… | deep | 168.29s | 8 | 5 | 16794 | 0.0527 | 0 | no | no |
| How does quantum computing work, and wha… | deep | 188.91s | 8 | 7 | 16248 | 0.0399 | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | quick | 136.16s | 3 | 2 | 8408 | 0.0071 | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | quick | 47.17s | 0 | 1 | 758 | None | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | standard | 61.02s | 0 | 0 | 1756 | None | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | standard | 121.03s | 3 | 11 | 6624 | 0.0381 | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | deep | 85.45s | 1 | 6 | 3914 | None | 0 | no | no |
| Compare PostgreSQL, MySQL, and MongoDB f… | deep | 79.2s | 1 | 12 | 2808 | None | 0 | no | no |
| Is intermittent fasting better than regu… | quick | 102.89s | 3 | 5 | 7563 | 0.2173 | 0 | yes | no |
| Is intermittent fasting better than regu… | quick | 18.69s | 0 | 4 | 980 | None | 1 | no | no |
| Is intermittent fasting better than regu… | standard | 198.42s | 5 | 4 | 13867 | 0.0147 | 0 | no | no |
| Is intermittent fasting better than regu… | standard | 170.41s | 5 | 3 | 10785 | 0.0363 | 0 | yes | no |
| Is intermittent fasting better than regu… | deep | 183.68s | 8 | 6 | 16244 | 0.0499 | 0 | no | no |
| Is intermittent fasting better than regu… | deep | 243.2s | 8 | 7 | 16072 | 0.0477 | 0 | no | no |
| What is the current state of the EU AI A… | quick | 128.98s | 3 | 4 | 8225 | 0.0106 | 0 | no | no |
| What is the current state of the EU AI A… | quick | 135.06s | 3 | 3 | 8466 | 0.0237 | 0 | no | no |
| What is the current state of the EU AI A… | standard | 146.58s | 3 | 10 | 7411 | 0.0092 | 0 | no | no |
| What is the current state of the EU AI A… | standard | 125.18s | 3 | 7 | 6341 | 0.0106 | 0 | no | no |
| What is the current state of the EU AI A… | deep | 222.81s | 8 | 17 | 15105 | 0.0298 | 0 | no | no |
| What is the current state of the EU AI A… | deep | 175.25s | 8 | 7 | 16851 | 0.0426 | 0 | no | no |
| How is generative AI changing software e… | quick | 91.27s | 3 | 2 | 7181 | 0.0133 | 0 | no | no |
| How is generative AI changing software e… | quick | 135.82s | 3 | 2 | 7190 | 0.009 | 0 | no | no |
| How is generative AI changing software e… | standard | 191.12s | 5 | 3 | 11941 | 0.0122 | 0 | no | no |
| How is generative AI changing software e… | standard | 191.33s | 5 | 4 | 11219 | 0.0259 | 0 | no | no |
| How is generative AI changing software e… | deep | 235.32s | 8 | 8 | 16524 | 0.0119 | 0 | no | no |
| How is generative AI changing software e… | deep | 204.72s | 8 | 2 | 16557 | 0.0252 | 0 | no | no |

## Per-stage mean (seconds)

| stage | quick | standard | deep |
|---|---|---|---|
| architect | 1.7 | 2.3 | 2.1 |
| diagram_designer | 1.9 | 3.6 | 4.0 |
| evidence_auditor | 3.6 | 6.8 | 5.1 |
| narrative_writer | 20.1 | 22.8 | 30.9 |
| pathfinder | 30.1 | 36.3 | 34.4 |
| report_assembler | 0.0 | 0.0 | 0.0 |
| review_gate | 0.0 | 0.0 | 0.0 |
| scout_swarm | 30.5 | 51.0 | 90.3 |

## Per-run detail

| mode | query | ok | wall | LLM | search | sections | auditor | conf | error |
|---|---|---|---|---|---|---|---|---|---|
| quick | How does quantum computing work, and w… | yes | 143.25s | 18 | 17 | 3 | 2 | 0.615 |  |
| quick | Compare PostgreSQL, MySQL, and MongoDB… | yes | 136.16s | 17 | 17 | 3 | 2 | 0.511 |  |
| quick | Is intermittent fasting better than re… | yes | 102.89s | 16 | 17 | 3 | 1 | 0.576 |  |
| quick | What is the current state of the EU AI… | yes | 128.98s | 18 | 17 | 3 | 2 | 0.556 |  |
| quick | How is generative AI changing software… | yes | 91.27s | 16 | 17 | 3 | 1 | 0.518 |  |
| quick | How does quantum computing work, and w… | yes | 92.06s | 16 | 17 | 3 | 1 | 0.554 |  |
| quick | Compare PostgreSQL, MySQL, and MongoDB… | yes | 47.17s | 13 | 17 | 3 | 1 | 0.417 |  |
| quick | Is intermittent fasting better than re… | yes | 18.69s | 12 | 17 | 3 | 1 | 0.496 |  |
| quick | What is the current state of the EU AI… | yes | 135.06s | 17 | 17 | 3 | 2 | 0.544 |  |
| quick | How is generative AI changing software… | yes | 135.82s | 17 | 17 | 3 | 2 | 0.511 |  |
| standard | How does quantum computing work, and w… | yes | 120.76s | 22 | 25 | 5 | 2 | 0.542 |  |
| standard | Compare PostgreSQL, MySQL, and MongoDB… | yes | 61.02s | 17 | 25 | 5 | 1 | 0.06 |  |
| standard | Is intermittent fasting better than re… | yes | 198.42s | 24 | 25 | 5 | 2 | 0.516 |  |
| standard | What is the current state of the EU AI… | yes | 146.58s | 22 | 25 | 5 | 2 | 0.61 |  |
| standard | How is generative AI changing software… | yes | 191.12s | 24 | 25 | 5 | 2 | 0.545 |  |
| standard | How does quantum computing work, and w… | yes | 180.39s | 24 | 25 | 5 | 2 | 0.609 |  |
| standard | Compare PostgreSQL, MySQL, and MongoDB… | yes | 121.03s | 23 | 25 | 5 | 2 | 0.601 |  |
| standard | Is intermittent fasting better than re… | yes | 170.41s | 24 | 25 | 5 | 2 | 0.552 |  |
| standard | What is the current state of the EU AI… | yes | 125.18s | 22 | 25 | 5 | 2 | 0.546 |  |
| standard | How is generative AI changing software… | yes | 191.33s | 25 | 25 | 5 | 2 | 0.58 |  |
| deep | How does quantum computing work, and w… | yes | 168.29s | 31 | 37 | 8 | 1 | 0.543 |  |
| deep | Compare PostgreSQL, MySQL, and MongoDB… | yes | 85.45s | 24 | 37 | 8 | 1 | 0.602 |  |
| deep | Is intermittent fasting better than re… | yes | 183.68s | 31 | 37 | 8 | 1 | 0.543 |  |
| deep | What is the current state of the EU AI… | yes | 222.81s | 33 | 37 | 8 | 2 | 0.556 |  |
| deep | How is generative AI changing software… | yes | 235.32s | 33 | 37 | 8 | 2 | 0.519 |  |
| deep | How does quantum computing work, and w… | yes | 188.91s | 31 | 37 | 8 | 1 | 0.533 |  |
| deep | Compare PostgreSQL, MySQL, and MongoDB… | yes | 79.2s | 24 | 37 | 8 | 1 | 0.606 |  |
| deep | Is intermittent fasting better than re… | yes | 243.2s | 33 | 37 | 8 | 2 | 0.606 |  |
| deep | What is the current state of the EU AI… | yes | 175.25s | 31 | 37 | 8 | 1 | 0.61 |  |
| deep | How is generative AI changing software… | yes | 204.72s | 32 | 37 | 8 | 1 | 0.524 |  |
