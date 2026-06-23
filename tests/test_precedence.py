"""The precedence lattice — deterministic, offline (no model, no network)."""

from governance.precedence import resolve
from governance.schemas import Action, PolicyCheckResult, PolicyVerdict


def result(*verdicts: PolicyVerdict, valid: bool = True) -> PolicyCheckResult:
    return PolicyCheckResult(verdicts=list(verdicts), valid=valid)


def v(pid, fired=False, targets=None, conf=0.95, abstain=False):
    return PolicyVerdict(policy_id=pid, fired=fired, redaction_targets=targets or [],
                         confidence=conf, abstain=abstain)


def test_nothing_fires_commits():
    r = result(v("P1"), v("P2"), v("P3"), v("P4"))
    assert resolve(r)[0] == Action.COMMIT


def test_compensation_drops():
    assert resolve(result(v("P1", fired=True)))[0] == Action.DROP


def test_codename_drops():
    assert resolve(result(v("P2", fired=True)))[0] == Action.DROP


def test_financial_redacts_with_targets():
    action, pid, targets, *_ = resolve(result(v("P4", fired=True, targets=["8847-220193-04"])))
    assert action == Action.REDACT
    assert targets == ["8847-220193-04"]


def test_legal_flags():
    assert resolve(result(v("P3", fired=True)))[0] == Action.FLAG


def test_drop_beats_redact_on_conflict():
    # comp figures that also look like a number → DROP must win over REDACT
    r = result(v("P1", fired=True), v("P4", fired=True, targets=["123"]))
    assert resolve(r)[0] == Action.DROP


def test_redact_beats_flag_on_conflict():
    r = result(v("P4", fired=True, targets=["123"]), v("P3", fired=True))
    assert resolve(r)[0] == Action.REDACT


def test_invalid_result_fails_closed_to_drop():
    assert resolve(result(valid=False))[0] == Action.DROP
