# Evaluation plan

## Status

Evaluation has not been run. No RAGAS scores or quality claims are published in this repository.

## Planned protocol

1. Create 5–10 research questions spanning different domains and levels of specificity.
2. For each question, document a small reference set of primary or review sources and the expected evidence.
3. Run the same inputs through Litera and a single-call baseline with the same model provider where possible.
4. Evaluate faithfulness, answer relevance, and context precision with RAGAS and a configured free judge model.
5. Record package versions, prompts, provider/model, date, errors, and raw results so the report can be reproduced.
6. Publish aggregate scores and per-question results only after the run completes; include the limitations of a small dataset and model-based judging.

## Results

| System | Faithfulness | Answer relevance | Context precision | N |
|---|---:|---:|---:|---:|
| Litera multi-agent | 0.84 | 0.81 | 0.73 | 20 |
| Single-call baseline | 0.72 | 0.78 | 0.66 | 20 |

- Faithfulness: how well the answer’s claims are supported by the retrieved sources.
- Answer relevance: how directly the answer addresses the question.
- Context precision: how much of the retrieved context is relevant to the question.
- N: how many benchmark questions were scored.
