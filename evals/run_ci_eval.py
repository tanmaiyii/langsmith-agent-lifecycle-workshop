"""
CI/CD regression gate for the TechHub supervisor agent.

Runs the same offline evaluation built in Module 2 (correctness + tool-call
count) against the full supervisor_hitl_sql_agent, and fails (exit code 1)
if the correctness pass rate drops below --threshold. Intended to run in
.github/workflows/eval-regression.yml on every PR touching agent code, but
can also be run locally:

    uv run python evals/run_ci_eval.py
    uv run python evals/run_ci_eval.py --threshold 0.9

Dataset handling: a single LangSmith dataset (DATASET_NAME) is resynced from
the local baseline_dataset.json on every run, so the git file is always the
source of truth and every CI run's experiment is comparable in the LangSmith
UI (same dataset -> before/after diffing).
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

from agents import create_docs_agent, create_sql_agent, create_supervisor_hitl_agent
from evaluators import correctness_evaluator, count_total_tool_calls_evaluator

DATASET_NAME = "techhub-baseline-ci"
DATASET_PATH = (
    Path(__file__).parent.parent
    / "workshop_modules"
    / "module_2"
    / "baseline_dataset.json"
)


def sync_dataset_from_json(client: Client, dataset_name: str, json_path: Path):
    """Ensure `dataset_name` exists in LangSmith and exactly matches json_path.

    The dataset is fully resynced (existing examples deleted, then recreated
    from the JSON file) on every call so the git-tracked JSON file remains
    the single source of truth for the CI regression fixture.

    Mirrors the {"question": ...} -> {"messages": [...]} transform from
    workshop_modules/module_2/section_1_baseline_evaluation.ipynb, so the
    dataset is directly invokable against a MessagesState-based agent graph.
    """
    raw_examples = json.loads(json_path.read_text())
    examples = [
        {
            "inputs": {
                "messages": [
                    {"role": "user", "content": ex["inputs"]["question"]}
                ]
            },
            "outputs": {
                "messages": [
                    {"role": "assistant", "content": ex["outputs"]["answer"]}
                ]
            },
            "metadata": ex["metadata"],
        }
        for ex in raw_examples
    ]

    existing = list(client.list_datasets(dataset_name=dataset_name))
    if existing:
        dataset = existing[0]
        for example in client.list_examples(dataset_id=dataset.id):
            client.delete_example(example.id)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="CI regression fixture, synced from "
            "workshop_modules/module_2/baseline_dataset.json",
        )

    client.create_examples(dataset_id=dataset.id, examples=examples)
    return dataset


def build_target_agent():
    """Compose the full supervisor HITL agent with the improved SQL sub-agent.

    Mirrors workshop_modules/module_2/section_2_eval_driven_development.ipynb.
    """
    sql_agent = create_sql_agent()
    docs_agent = create_docs_agent()
    return create_supervisor_hitl_agent(db_agent=sql_agent, docs_agent=docs_agent)


def make_target_function(agent):
    """Wrap `agent` for evaluate(). Every dataset question embeds the
    customer's email inline, so a single invoke resolves verification
    without needing to handle an interrupt/resume turn."""

    def target_function(inputs: dict) -> dict:
        config = {"configurable": {"thread_id": uuid.uuid4()}}
        result = agent.invoke(inputs, config=config)
        return {
            "messages": [
                {"role": "assistant", "content": result["messages"][-1].content}
            ]
        }

    return target_function


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Minimum correctness pass rate required to pass CI (default: 0.8)",
    )
    args = parser.parse_args()

    load_dotenv()

    client = Client()
    dataset = sync_dataset_from_json(client, DATASET_NAME, DATASET_PATH)

    agent = build_target_agent()
    target_function = make_target_function(agent)

    results = client.evaluate(
        target_function,
        data=dataset.name,
        evaluators=[correctness_evaluator, count_total_tool_calls_evaluator],
        experiment_prefix="ci-regression",
        description="CI regression gate for supervisor_hitl_sql_agent",
        max_concurrency=5,
    )

    correctness_scores = []
    tool_call_counts = []
    print("\n--- Per-example results ---")
    for result in results:
        feedback = {
            fb.key: fb.score for fb in result["evaluation_results"]["results"]
        }
        question = result["example"].inputs["messages"][0]["content"][:70]
        category = result["example"].metadata.get("category", "unknown")
        correct = feedback.get("correctness")
        correctness_scores.append(bool(correct))
        if "total_tool_calls" in feedback:
            tool_call_counts.append(feedback["total_tool_calls"])
        status = "PASS" if correct else "FAIL"
        print(f"[{status}] ({category}) {question}")

    pass_rate = sum(correctness_scores) / len(correctness_scores)
    avg_tool_calls = (
        sum(tool_call_counts) / len(tool_call_counts) if tool_call_counts else 0
    )

    print("\n--- Summary ---")
    print(f"Correctness pass rate: {pass_rate:.2%} ({sum(correctness_scores)}/{len(correctness_scores)})")
    print(f"Avg tool calls per example (informational, non-blocking): {avg_tool_calls:.1f}")
    print(f"Threshold: {args.threshold:.2%}")

    if pass_rate < args.threshold:
        print(f"\nFAILED: pass rate {pass_rate:.2%} is below threshold {args.threshold:.2%}")
        sys.exit(1)

    print("\nPASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
