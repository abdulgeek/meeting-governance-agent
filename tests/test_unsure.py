"""The unsure path - uncertainty must cost a recording, never a leak."""

from governance.precedence import resolve
from governance.schemas import Action, PolicyCheckResult, PolicyVerdict


def res(*v):
    return PolicyCheckResult(verdicts=list(v), valid=True)


def pv(pid, **kw):
    return PolicyVerdict(policy_id=pid, **kw)


def test_unsure_compensation_drops():
    # abstain on a suppression rule => drop
    action, pid, _, _, note = resolve(res(pv("P1", fired=False, abstain=True)))
    assert action == Action.DROP
    assert "unsure" in note


def test_unsure_number_redacts_when_maskable():
    action, *_ = resolve(res(pv("P4", abstain=True, redaction_targets=["123-456"])))
    assert action == Action.REDACT


def test_unsure_number_without_target_escalates_to_flag():
    # suspected financial number but nothing concrete to mask => flag for a human
    action, pid, *_ = resolve(res(pv("P4", abstain=True, redaction_targets=[])))
    assert action == Action.FLAG
    assert pid == "P4_NO_TARGET"


def test_unsure_legal_flags():
    action, *_ = resolve(res(pv("P3", abstain=True)))
    assert action == Action.FLAG


def test_low_confidence_suppression_still_drops():
    action, _, _, conf, note = resolve(res(pv("P1", fired=True, confidence=0.30)))
    assert action == Action.DROP
    assert "unsure" in note
