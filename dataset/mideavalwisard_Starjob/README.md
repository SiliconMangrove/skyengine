---
license: mit
tags:
- JSSP
- combinatorial_optimization
- llms
- supervised
- dataset
- llm_reasoning
---
## Dataset Descriptions
**Starjob** introduces the first large-scale, supervised dataset (130,000 instances) specifically designed for training Large Language Models (LLMs) to solve the Job Shop Scheduling Problem (JSSP). Leveraging natural language representations of scheduling problems and solutions, Starjob enables fine-tuning of LLMs (Llama 8B, 4-bit quantized, trained with RsLoRA) for end-to-end scheduling. Our fine-tuned model not only generates feasible schedules, but also outperforms classic Priority Dispatching Rules (PDRs) and the state-of-the-art L2D neural approach, achieving up to 15% better results on standard benchmarks (Taillard, DMU). This work demonstrates the untapped potential of LLMs in combinatorial optimization and paves the way for interactive, explainable, and efficient scheduling solutions using natural language interfaces.

[Dataset and code repository](https://github.com/starjob42/Starjob)

### Jssp
**Directory:** `jssp`
**File:** `starjob_no_absalute_path.json`
**Attributes:**
- `num_jobs`
- `num_machines`
- `prompt_jobs_first`
- `instruction`
- `prompt_machines_first`
- `input`
- `output`
- `path`
- `matrix`
**Number of Items:** 129720

## Dataset Groupings

The datasets can be grouped based on their problem domains and attributes:

### Other Problems
**Datasets:**
- Jssp
**Common attributes:**
- `matrix`
- `output`
- `prompt_jobs_first`
- `path`
- `instruction`
- `num_jobs`
- `prompt_machines_first`
- `num_machines`
- `input`

## Statistics Summary

| Dataset | File | Number of Items | Attributes |
|---------|------|----------------|------------|
| Jssp | starjob_no_absalute_path.json | 129720 | `num_jobs`, `num_machines`, `prompt_jobs_first`, `instruction`, `prompt_machines_first`, `input`, `output`, `path`, `matrix` |

## Detailed Dataset Statistics

---