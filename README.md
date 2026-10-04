# Constraint-Aware Shopping Agent

A constraint-aware LLM shopping agent built on top of the [WebShop](https://github.com/princeton-nlp/WebShop) environment.

This project studies whether explicit constraint checking can improve the reliability of LLM-based shopping agents.

We introduce a shared **ConstraintManager** and two optional reasoning modules:

- **Query Gate** — checks whether generated search queries preserve important user requirements.
- **Product Gate** — checks whether a selected product satisfies the requested constraints using visible product evidence.

The gates do not replace the original LLM policy. Instead, they provide structured constraint-aware feedback at different stages of the shopping process.

---

## Architecture

The main idea is to separate **general action generation** from **explicit constraint checking**.

A normal LLM shopping agent has to remember all user requirements while simultaneously searching, navigating product pages, inspecting attributes, and deciding when to buy. During a long trajectory, some requirements may be forgotten or ignored.

Our architecture therefore extracts the user's requirements once and maintains them as a shared structured representation.

The system contains four main components:

- **Base Agent** — generates WebShop actions such as search, click, inspect, go back, and buy.
- **ConstraintManager** — extracts, validates, normalizes, and matches user constraints.
- **Query Gate** — checks constraints during the retrieval stage.
- **Product Gate** — checks constraints during the candidate-verification stage.

Both gates share the same `ConstraintManager`, ensuring that search-time and product-time reasoning use the same interpretation of the user's request.

```text
                         User Instruction
                                |
                                v
                    +-----------------------+
                    |   ConstraintManager   |
                    |-----------------------|
                    | extract constraints   |
                    | validate extraction   |
                    | normalize / aliases   |
                    | semantic fallback     |
                    | cache shared schema   |
                    +-----------+-----------+
                                |
                                v
                    Shared Constraint Schema
                                |
              +-----------------+-----------------+
              |                                   |
              v                                   v
      +------------------+                +------------------+
      |    Query Gate    |                |   Product Gate   |
      | Retrieval Stage  |                | Candidate Stage  |
      +------------------+                +------------------+
              ^                                   ^
              |                                   |
       proposed query                     accumulated evidence
              |                                   |
              +---------------+   +---------------+
                              |   |
                              v   v
                       +----------------+
                       |   Base Agent   |
                       |----------------|
                       | observation    |
                       | action history |
                       | gate feedback  |
                       +--------+-------+
                                |
                                v
                         Proposed Action
                                |
               +----------------+----------------+
               |                                 |
               v                                 v
         search[query]                    click / inspect
               |                                 |
               v                                 v
          Query Gate                           WebShop
               |                                 |
        +------+-------+                         v
        |              |                Visible Product Evidence
        v              v                         |
      PASS           REVISE                      v
        |              |               Candidate Evidence Memory
        |              |                         |
        |       constraint feedback              v
        |              |                  Product Gate
        |              |                         |
        |              |          +--------------+--------------+
        |              |          |              |              |
        |              |          v              v              v
        |              |        READY          INSPECT        REJECT
        |              |          |              |              |
        +--------------+----------+------+-------+--------------+
                                          |
                                          v
                                 Structured Feedback
                                          |
                                          v
                                     Base Agent
                                          |
                                          v
                                 Next WebShop Action
```

The gates act as **reasoning checkpoints**, rather than independent agents.

Conceptually:

```text
Base Agent:
"What action should I take next?"

Query Gate:
"Does this search query still represent the user's requirements?"

Product Gate:
"Does the visible evidence support this product as a valid candidate?"
```

The gates operate only on information available to the agent during normal interaction:

- user instruction
- visible WebShop observations
- available actions
- action history
- visible product evidence

They do not use evaluation-only information such as the target ASIN, hidden task attributes, or reward.

---

## ConstraintManager

Main implementation:

```text
gates/constraint_manager.py
```

The `ConstraintManager` converts a natural-language shopping instruction into a structured constraint schema shared by both gates.

A simplified example:

```json
{
  "product_type": {
    "source_text": "tongue cleaners",
    "canonical": "tongue cleaner",
    "aliases": ["tongue scraper"]
  },
  "required_constraints": [
    {
      "source_text": "bpa free",
      "canonical": "bpa free",
      "aliases": ["BPA-free"]
    }
  ],
  "post_selection_constraints": [],
  "price_constraint": null
}
```

The manager performs:

- constraint extraction
- extraction validation
- canonicalization
- alias / synonym construction
- deterministic matching
- semantic fallback for unresolved cases

Constraint matching follows a progressively more flexible strategy:

```text
Exact Match
     |
     v
Alias / Synonym Match
     |
     v
Token Coverage
     |
     v
Semantic Fallback
```

The schema is extracted once per task and cached. Query Gate and Product Gate therefore reuse the same validated interpretation instead of independently extracting constraints.

---

## Query Gate

Main implementation:

```text
gates/query_gate.py
```

The Query Gate operates during the **retrieval stage**.

Its purpose is to prevent important requirements from disappearing when the Base Agent converts a detailed user instruction into a shorter search query.

For example:

```text
User Instruction:
Find a BPA-free tongue cleaner that is easy to clean

Generated Query:
tongue cleaner
```

The product type is preserved, but some useful constraints have been lost.

The Query Gate compares the proposed query against the shared constraint schema.

```text
                    Base Agent
                        |
                        v
                proposed search query
                        |
                        v
                 +--------------+
                 |  Query Gate  |
                 +--------------+
                        |
             +----------+----------+
             |                     |
             v                     v
      Check Product Type    Check Constraints
             |                     |
             +----------+----------+
                        |
                        v
             Are important requirements
               sufficiently preserved?
                        |
                  +-----+-----+
                  |           |
                 YES          NO
                  |           |
                  v           v
                PASS        REVISE
                  |           |
                  |           v
                  |    identify missing or
                  |    inconsistent constraints
                  |           |
                  |           v
                  |    structured feedback
                  |           |
                  |           v
                  |       Base Agent
                  |           |
                  |           v
                  |      revised query
                  |           |
                  +-----+-----+
                        |
                        v
                  Execute Search
```

The main decisions are:

### PASS

The proposed query sufficiently represents the important retrieval constraints.

Example:

```text
Instruction:
BPA-free tongue cleaner

Query:
BPA free tongue cleaner

Decision:
PASS
```

### REVISE

Important constraints are missing or inconsistent.

Example:

```text
Instruction:
BPA-free tongue cleaner

Query:
tongue cleaner

Decision:
REVISE

Missing:
BPA free
```

The Query Gate does not directly replace the search action.

Instead, it provides structured feedback to the Base Agent, which can generate a revised query:

```text
search[BPA free tongue cleaner]
```

The revised query can then be validated again before execution.

The Query Gate therefore focuses specifically on:

```text
detecting information loss
        |
        v
providing constraint feedback
        |
        v
improving query formulation
```

It does not determine whether a retrieved product is actually correct. That responsibility belongs to the Product Gate.

---

## Product Gate

Main implementation:

```text
gates/product_gate.py
```

The Product Gate operates during the **candidate-verification stage**, after the agent has opened a product.

Its purpose is to determine whether the current candidate has enough visible evidence to satisfy the user's requirements.

Product information in WebShop may be distributed across several pages:

```text
Product Page
     |
     +----> Features
     |
     +----> Description
     |
     +----> Reviews
     |
     +----> Options
```

For this reason, the agent maintains an evidence memory for the currently active candidate.

Evidence from different visible pages is accumulated instead of evaluating every page independently.

```text
                         Click Product
                              |
                              v
                    Candidate Evidence Memory
                              |
                +-------------+-------------+
                |             |             |
                v             v             v
            Features      Description     Reviews
                |             |             |
                v             v             v
           add evidence   add evidence   add evidence
                |             |             |
                +-------------+-------------+
                              |
                              v
                     Accumulated Evidence
                              |
                              v
                       +--------------+
                       | Product Gate |
                       +--------------+
                              |
                 +------------+------------+
                 |            |            |
                 v            v            v
               READY        INSPECT      REJECT
                 |            |            |
                 |            |            |
                 v            v            v
             supported     need more    conflicting
             candidate     evidence      candidate
                 |            |            |
                 +------------+------------+
                              |
                              v
                     Structured Feedback
                              |
                              v
                          Base Agent
                              |
               +--------------+--------------+
               |              |              |
               v              v              v
              Buy        Inspect More    Leave Candidate
```

The Product Gate first evaluates whether the candidate matches the requested product type.

It then evaluates the required constraints.

A simplified decision process is:

```text
Check Product Type
        |
        +---- contradicted --------> REJECT
        |
        +---- uncertain -----------> INSPECT
        |
        +---- supported
                 |
                 v
       Check Required Constraints
                 |
       +---------+---------+
       |                   |
       v                   v
 contradiction        missing evidence
       |                   |
       v                   v
     REJECT              INSPECT

       all supported
            |
            v
          READY
```

The three main decisions are:

### READY

The visible evidence supports the requested product type and required constraints.

Example:

```text
Instruction:
BPA-free tongue cleaner

Visible Evidence:
tongue scraper
BPA-free material

Decision:
READY
```

### INSPECT

The candidate may be valid, but important evidence is still missing.

Example:

```text
Instruction:
BPA-free tongue cleaner

Visible Evidence:
tongue scraper

BPA information:
not visible

Decision:
INSPECT
```

This distinction is important:

```text
missing evidence != wrong product
```

A potentially valid product should not necessarily be abandoned simply because one requirement is not visible on the first page.

The agent can instead inspect additional evidence such as features or descriptions.

### REJECT

The visible evidence conflicts with the requested product type or an important constraint.

For example:

```text
Requested:
tongue cleaner

Candidate:
lipstick

Decision:
REJECT
```

The Product Gate is advisory rather than directly controlling.

It does not automatically click `Buy Now`, inspect a particular page, or leave the candidate.

Instead, it provides structured feedback such as:

```text
Decision:
INSPECT

Matched:
tongue cleaner

Missing:
BPA free

Recommended:
inspect features or description
```

The Base Agent remains responsible for selecting the actual WebShop action.

The two gates therefore address different stages of the shopping process:

```text
Query Gate
    |
    +---- retrieval-stage checking
    |
    +---- "Am I searching with the right constraints?"


Product Gate
    |
    +---- candidate-stage checking
    |
    +---- "Does this product actually satisfy the constraints?"
```

Together, the overall reasoning process becomes:

```text
User Request
     |
     v
Extract and Validate Constraints
     |
     v
Generate Search Query
     |
     v
Query Gate
     |
     v
Retrieve Candidate
     |
     v
Collect Product Evidence
     |
     v
Product Gate
     |
     v
Base Agent Chooses Next Action
```

---

## Agent

Main implementation:

```text
agent/gated_agent.py
```

The agent supports four experimental configurations:

| Configuration | Query Gate | Product Gate |
|---|---|---|
| No Gate | OFF | OFF |
| Query Gate Only | ON | OFF |
| Product Gate Only | OFF | ON |
| Query + Product Gate | ON | ON |

When both gates are enabled, they share a single `ConstraintManager`.

This provides a simple 2 × 2 ablation design for measuring the contribution of each gate.

---

## Evaluation

Main evaluation entry:

```text
evaluation/run_gates_31.py
```

The current development evaluation uses:

- 31 instruction-bearing shopping tasks
- a WebShop search environment containing 1000 indexed products
- a main interaction budget of 20 steps

Example:

```bash
python -u evaluation/run_gates_31.py \
    --query-gate on \
    --product-gate on \
    --num-products 1000 \
    --max-steps 20 \
    --dataset evaluation/webshop_test_100.json \
    --output-dir results/both_gates
```

Product Gate only:

```bash
python -u evaluation/run_gates_31.py \
    --query-gate off \
    --product-gate on \
    --num-products 1000 \
    --max-steps 20 \
    --dataset evaluation/webshop_test_100.json \
    --output-dir results/product_gate
```

---

## Preliminary Results

Current 20-step development results:

| Configuration | Full Success | Partial | Zero | Average Reward |
|---|---:|---:|---:|---:|
| No Gate | 8 / 31 | 3 | 20 | 0.304 |
| Query Gate Only | 6 / 31 | 2 | 23 | 0.205 |
| Product Gate Only | 17 / 31 | 2 | 12 | 0.575 |
| Query + Product Gate | 18 / 31 | 2 | 11 | 0.608 |

These results are preliminary and should not be interpreted as final benchmark estimates.

The Product Gate currently shows the clearest observed improvement. Query Gate results are more variable, so repeated runs and larger-scale evaluation are planned before drawing conclusions about its standalone or interaction effect.

---

## Project Structure

```text
Constraint-Aware-Shopping-Agent/
│
├── agent/
│   ├── base_agent.py
│   ├── baseline_agent.py
│   └── gated_agent.py
│
├── gates/
│   ├── __init__.py
│   ├── constraint_manager.py
│   ├── query_gate.py
│   └── product_gate.py
│
├── evaluation/
│   ├── run_gates_31.py
│   └── summarize_product_gate_31.py
│
├── webshop_wrapper/
├── web_agent_site/
├── search_engine/
│
├── requirements.txt
├── setup.sh
├── THIRD_PARTY_LICENSE_WebShop.md
└── README.md
```

---

## Setup

Install the project dependencies:

```bash
pip install -r requirements.txt
```

The current implementation uses DeepSeek as the action-generating language model.

Set the API key through an environment variable:

```bash
export DEEPSEEK_API_KEY="YOUR_KEY"
```

Do not commit API keys to the repository.

---

## Upstream Project

This project is built on top of the WebShop environment developed by Princeton NLP.

Original repository:

https://github.com/princeton-nlp/WebShop

The original WebShop license is preserved in:

```text
THIRD_PARTY_LICENSE_WebShop.md
```

---

## Status

Current work focuses on:

- repeated ablation runs
- evaluating variance across LLM runs
- longer interaction-budget sensitivity experiments
- larger evaluation sets

The current 31-task results are treated as development and diagnostic experiments rather than a final large-scale benchmark.
