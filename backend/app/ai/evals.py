from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldenEvalCase:
    skill_name: str
    fixture_id: str
    input_path: Path
    expected_path: Path


def discover_golden_eval_cases(root: Path) -> list[GoldenEvalCase]:
    cases: list[GoldenEvalCase] = []
    if not root.exists():
        return cases
    for expected_path in root.rglob("expected.json"):
        fixture_dir = expected_path.parent
        input_path = fixture_dir / "input.json"
        if input_path.exists():
            cases.append(
                GoldenEvalCase(
                    skill_name=fixture_dir.parent.name,
                    fixture_id=fixture_dir.name,
                    input_path=input_path,
                    expected_path=expected_path,
                )
            )
    return cases
