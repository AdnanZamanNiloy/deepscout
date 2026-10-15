# Mode baseline (Step 0)

Generated: 2026-10-15T16:46:07+00:00

Measured with `bench/run_mode_baseline.py` against live providers and live retrieval. These are observations, not targets.


## Headline

| mode | ok/total | wall p50 | wall p90 | wall max | LLM calls | search | sections | auditor passes | dominant stage |
|---|---|---|---|---|---|---|---|---|---|
| quick | 5/5 | 23.6s | 98.5s | 131.5s | 14.6 | 17 | 3 | 1.4 | pathfinder |
| standard | 5/5 | 23.9s | 77.4s | 90.0s | 18 | 25 | 5 | 1.2 | scout_swarm |
| deep | 5/5 | 34.4s | 40.8s | 51.1s | 23.4 | 37 | 8 | 1.4 | scout_swarm |

## Per-stage mean (seconds)

| stage | quick | standard | deep |
|---|---|---|---|
| architect | 1.8 | 1.4 | 2.4 |
| diagram_designer | 2.2 | 2.4 | 2.2 |
| evidence_auditor | 2.6 | 3.7 | 1.5 |
| narrative_writer | 13.7 | 10.0 | 10.9 |
| pathfinder | 16.7 | 9.0 | 2.2 |
| report_assembler | 0.0 | 0.0 | 0.0 |
| review_gate | 0.0 | 0.0 | 0.0 |
| scout_swarm | 16.4 | 20.0 | 12.5 |

## Per-run detail

| mode | query | ok | wall | LLM | search | sections | auditor | conf | error |
|---|---|---|---|---|---|---|---|---|---|
| quick | What is retrieval augmented generation… | yes | 23.58s | 12 | 17 | 3 | 1 | 0.417 |  |
| quick | Compare the economics of solar versus … | yes | 20.76s | 12 | 17 | 3 | 1 | 0.06 |  |
| quick | What can be the most demanding jobs in… | yes | 131.51s | 18 | 17 | 3 | 2 | 0.633 |  |
| quick | What are the latest advances in AI age… | yes | 98.53s | 18 | 17 | 3 | 2 | 0.477 |  |
| quick | Should cities adopt congestion pricing… | yes | 42.26s | 13 | 17 | 3 | 1 | 0.06 |  |
| standard | What is retrieval augmented generation… | yes | 1.81s | 16 | 25 | 5 | 1 | 0.417 |  |
| standard | Compare the economics of solar versus … | yes | 23.87s | 16 | 25 | 5 | 1 | 0.06 |  |
| standard | What can be the most demanding jobs in… | yes | 77.36s | 20 | 25 | 5 | 1 | 0.476 |  |
| standard | What are the latest advances in AI age… | yes | 63.14s | 18 | 25 | 5 | 1 | 0.57 |  |
| standard | Should cities adopt congestion pricing… | yes | 90.05s | 20 | 25 | 5 | 2 | 0.476 |  |
| deep | What is retrieval augmented generation… | yes | 40.83s | 23 | 37 | 8 | 1 | 0.06 |  |
| deep | Compare the economics of solar versus … | yes | 40.59s | 24 | 37 | 8 | 2 | 0.417 |  |
| deep | What can be the most demanding jobs in… | yes | 34.39s | 23 | 37 | 8 | 1 | 0.06 |  |
| deep | What are the latest advances in AI age… | yes | 22.95s | 22 | 37 | 8 | 1 | 0.06 |  |
| deep | Should cities adopt congestion pricing… | yes | 51.06s | 25 | 37 | 8 | 2 | 0.06 |  |
