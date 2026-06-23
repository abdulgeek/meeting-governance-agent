"""Ephemerality regression guard: dropped/declined content must not reach any sink,
and a redacted value must be verified gone before write. Offline (fake model).
"""

from governance.actions import mask_verified
from governance.agent import GovernanceAgent
from governance.audit import Audit
from governance.consent import ConsentRegistry
from governance.policy_check import PolicyChecker
from governance.sink import Sink

CANARY_DROP = "CANARY-DROP-7f3a9e21"
CANARY_DECLINE = "CANARY-DECLINE-b48c0d55"


class FakeLLM:
    def complete(self, system, user):
        p1 = "true" if CANARY_DROP in user else "false"
        return (
            '{"verdicts":['
            f'{{"policy_id":"P1","fired":{p1},"confidence":0.99,"abstain":false,"rationale":"comp"}},'
            '{"policy_id":"P2","fired":false,"confidence":0.99,"abstain":false,"rationale":"n"},'
            '{"policy_id":"P3","fired":false,"confidence":0.99,"abstain":false,"rationale":"n"},'
            '{"policy_id":"P4","fired":false,"confidence":0.99,"abstain":false,"rationale":"n"}'
            ']}'
        )


def test_dropped_and_declined_leave_no_copy(tmp_path):
    sink = Sink(tmp_path / "t.jsonl")
    audit = Audit(tmp_path / "a.jsonl")
    agent = GovernanceAgent(ConsentRegistry({"a": True, "b": False}),
                            PolicyChecker(FakeLLM(), "x"), sink, audit, window_size=4)
    list(agent.run([
        (1, "a", "agenda for today"),                       # COMMIT
        (2, "a", f"salary is {CANARY_DROP}"),               # DROP
        (3, "b", f"privileged note {CANARY_DECLINE}"),      # DECLINE
    ]))
    blob = (tmp_path / "t.jsonl").read_text() + (tmp_path / "a.jsonl").read_text()
    assert CANARY_DROP not in blob
    assert CANARY_DECLINE not in blob
    assert "agenda for today" in (tmp_path / "t.jsonl").read_text()  # committed line survives


def test_mask_removes_the_value_before_write():
    ok, masked = mask_verified("the number is 1234", ["1234"])
    assert ok is True
    assert "1234" not in masked


def test_mask_verified_ok_when_target_absent():
    # nothing to mask (and the value isn't there anyway) -> ok, write proceeds
    ok, masked = mask_verified("hello world", ["1234"])
    assert ok is True and masked == "hello world"
