import json

from app.dataset import Expectations, load_dataset
from app.fake import FIXTURES
from app.grading import grade
from app.prompts import estimate, stable_prefix, support_prompt


def test_all_requests_grade_a_good_and_a_bad_response():
    data = load_dataset()
    fixtures = json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))
    for requests in data.requests.values():
        for request in requests:
            good = fixtures[request.id]
            assert grade(json.dumps(good), request.expect) == [], request.id
            assert grade(json.dumps({**good, "policy_ids": [], "answer": ""}), request.expect), request.id


def test_grader_enforces_every_rule_and_explains_misses():
    spec = Expectations(policies=["BILL-1"], must=[r"\b2\b"], must_not=["immediately"], decision=["approve"])
    assert grade("not json", spec) == ["unparseable"]
    answer = {"answer": "Immediately", "policy_ids": ["BILL-2"], "decision": "refuse"}
    assert grade(json.dumps(answer), spec) == [
        "missing policy: BILL-1", r"required: \b2\b", "forbidden: immediately", "unexpected decision: refuse",
    ]


def test_prefix_is_stable_and_big_enough():
    policies = load_dataset().policies
    assert estimate(stable_prefix(policies)) >= 1024
    first, extras = support_prompt(policies, "one", "first", "pc-01", policies_first=True,
                                   explicit=True, key="v")
    next_, _ = support_prompt(policies, "two", "second", "pc-02", policies_first=True,
                              explicit=True, key="v")
    assert first[0] == next_[0]
    assert first[1] != next_[1]
    assert extras == {"prompt_cache_key": "v", "prompt_cache_options": {"mode": "explicit", "ttl": "30m"}}
