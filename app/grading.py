"""Grade the current request, including when its answer came from a cache hit."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .dataset import POLICY_IDS, Expectations


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str
    policy_ids: list[str]
    decision: Literal["inform", "approve", "partial", "refuse", "escalate"]

    @field_validator("policy_ids")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        if not set(values).issubset(POLICY_IDS):
            raise ValueError("Unknown policy ID")
        return values


def parse_answer(text: str) -> Answer | None:
    try:
        return Answer.model_validate_json(text)
    except ValidationError:
        return None


def grade(text: str, expected: Expectations) -> list[str]:
    result = parse_answer(text)
    if result is None:
        return ["unparseable"]
    reasons = [f"missing policy: {key}" for key in expected.policies if key not in result.policy_ids]
    reasons += [f"required: {rule}" for rule in expected.must if not re.search(rule, result.answer, re.I)]
    reasons += [f"forbidden: {rule}" for rule in expected.must_not if re.search(rule, result.answer, re.I)]
    if expected.decision and result.decision not in expected.decision:
        reasons.append(f"unexpected decision: {result.decision}")
    return reasons
