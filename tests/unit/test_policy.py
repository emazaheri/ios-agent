"""The safety gate. Its failure modes are the ones that damage real devices."""

from __future__ import annotations

import pytest

from ios_mcp.config import PolicySettings
from ios_mcp.errors import AppNotAllowed, SecretNotFound, SessionHalted
from ios_mcp.perception.refs import Target
from ios_mcp.policy.audit import AuditTrail
from ios_mcp.policy.gate import PolicyGate, Risk
from ios_mcp.policy.redact import Redactor
from ios_mcp.policy.secrets import resolve_secret
from ios_mcp.wda.models import Rect


def target(label: str, role: str = "button", identifier: str | None = None) -> Target:
    return Target(
        ref="e1",
        role=role,
        label=label,
        identifier=identifier,
        rect=Rect(0, 0, 50, 30),
        enabled=True,
        resolved_via="exact",
    )


def gate(**overrides) -> PolicyGate:
    return PolicyGate(PolicySettings(**overrides))


# -- destructive classification ---------------------------------------------


@pytest.mark.parametrize(
    "label",
    ["Send", "Pay", "Delete", "Confirm", "Buy Now", "Sign Out", "Transfer", "Place Order"],
)
def test_destructive_labels_require_approval(label: str) -> None:
    verdict = gate().classify("tap", target(label))
    assert verdict.risk is Risk.DESTRUCTIVE
    assert verdict.needs_approval


@pytest.mark.parametrize("label", ["Cancel", "Back", "Settings", "Wi-Fi", "Search", "Done"])
def test_ordinary_labels_pass_freely(label: str) -> None:
    assert gate().classify("tap", target(label)).risk is Risk.SAFE


@pytest.mark.parametrize("label", ["Sender", "Undelete", "Resend Later", "Buyer", "Deleted Items"])
def test_substring_collisions_do_not_trip_the_gate(label: str) -> None:
    """Prompting on 'Sender' trains an operator to approve everything reflexively."""
    assert gate().classify("tap", target(label)).risk is Risk.SAFE


def test_matching_is_case_insensitive() -> None:
    assert gate().classify("tap", target("SEND")).risk is Risk.DESTRUCTIVE
    assert gate().classify("tap", target("delete")).risk is Risk.DESTRUCTIVE


def test_the_accessibility_identifier_is_checked_too() -> None:
    """Icon-only buttons carry their meaning in the identifier, not a label."""
    verdict = gate().classify("tap", target("", identifier="delete_button"))
    assert verdict.risk is Risk.DESTRUCTIVE


def test_typed_text_is_checked() -> None:
    assert gate().classify("type", None, text="please delete everything").risk is Risk.DESTRUCTIVE


def test_read_only_actions_are_never_destructive() -> None:
    for action in ("observe", "screenshot", "read_text", "wait_for", "list_apps"):
        assert gate().classify(action, target("Delete")).risk is Risk.SAFE


def test_the_gate_can_be_turned_off_entirely() -> None:
    assert gate(enabled=False).classify("tap", target("Delete")).risk is Risk.SAFE
    assert gate(enabled=False).classify("tap", target("Follow")).risk is Risk.SAFE
    assert gate(confirm_destructive=False).classify("tap", target("Delete")).risk is Risk.SAFE


def test_the_verdict_explains_itself() -> None:
    verdict = gate().classify("tap", target("Send"))
    assert "Send" in (verdict.reason or "")
    assert verdict.matched == "send"


# -- reaching another person -------------------------------------------------


def test_a_real_failure_is_now_asked_about() -> None:
    """The run that motivated this: four likes on a stranger's profile.

    The goal was to read the profile's prompts. Every tap reached a stranger in
    the owner's name, and the gate let all four through, because "like" answers
    neither of the questions it used to ask: it destroys nothing and costs
    nothing.
    """
    verdict = gate().classify("tap", target("Like Radha's answer"))

    assert verdict.risk is Risk.REACHES_A_PERSON
    assert verdict.needs_approval


@pytest.mark.parametrize(
    "label", ["Like", "Follow", "Comment", "Reply", "Share", "Invite", "Message", "Post", "RSVP"]
)
def test_actions_that_reach_a_person_are_asked_about(label: str) -> None:
    assert gate().classify("tap", target(label)).risk is Risk.REACHES_A_PERSON


@pytest.mark.parametrize("label", ["Likes", "Following", "Followers", "Comments", "Messages"])
def test_the_screens_named_after_them_are_not(label: str) -> None:
    """A tab called Likes is where the likes are, not a like.

    Whole words, as for the destructive rule, so that navigating to the list of
    something is not mistaken for doing it.
    """
    assert gate().classify("tap", target(label)).risk is Risk.SAFE


def test_an_id_that_names_the_action_is_caught() -> None:
    """A drawn heart arrives with no label, only an id like `like_prompt_card_2`."""
    verdict = gate().classify("tap", target("", identifier="like_prompt_card_2"))

    assert verdict.risk is Risk.REACHES_A_PERSON


def test_the_prompt_says_what_the_action_would_do() -> None:
    """The person approving is told the consequence, not that a rule matched."""
    reason = gate().classify("tap", target("Follow")).reason or ""

    assert "reach another person" in reason
    assert "Follow" in reason


def test_destroying_or_paying_outranks_reaching_someone() -> None:
    """An action that does both is asked about as the costlier of the two."""
    verdict = gate().classify("tap", target("Send Payment"))

    assert verdict.risk is Risk.DESTRUCTIVE


def test_the_two_questions_are_switched_separately() -> None:
    """A simulator test suite may drop one without dropping the other."""
    only_destructive = gate(confirm_reaching_a_person=False)
    only_people = gate(confirm_destructive=False)

    assert only_destructive.classify("tap", target("Follow")).risk is Risk.SAFE
    assert only_destructive.classify("tap", target("Delete")).risk is Risk.DESTRUCTIVE
    assert only_people.classify("tap", target("Delete")).risk is Risk.SAFE
    assert only_people.classify("tap", target("Follow")).risk is Risk.REACHES_A_PERSON


def test_typed_prose_is_not_a_person_reached() -> None:
    """Typing reaches nobody until something is sent, and send is destructive.

    Neither the typed text nor the field's own label is judged: "like" is also
    a preposition, and a field called Comment is where a comment is drafted,
    not posted. The button that finally sends it is what gets asked about.
    """
    field = target("Comment", role="textfield")

    assert gate().classify("type", field, text="I'd like to come").risk is Risk.SAFE


def test_a_paragraph_is_nobodys_button() -> None:
    """The one false positive measured on a real Settings app, ten panes deep."""
    prose = target(
        "StandBy will turn on when iPhone is placed on its side while charging "
        "to show information like widgets, photo frames, or clocks.",
        role="text",
    )

    assert gate().classify("tap", prose).risk is Risk.SAFE


def test_typed_text_still_answers_the_destructive_question() -> None:
    """Narrowing the person rule leaves the older one exactly as it was."""
    assert (
        gate().classify("type", target("Note"), text="delete everything").risk is Risk.DESTRUCTIVE
    )


def test_reading_a_profile_is_still_free() -> None:
    for action in ("observe", "screenshot", "read_text", "wait_for", "list_apps"):
        assert gate().classify(action, target("Like")).risk is Risk.SAFE


# -- app scope --------------------------------------------------------------


def test_blocked_apps_are_refused() -> None:
    with pytest.raises(AppNotAllowed) as exc_info:
        gate().check_app("com.apple.Passbook")
    assert "payment" in (exc_info.value.hint or "")


def test_an_allowlist_excludes_everything_else() -> None:
    g = gate(app_allowlist=("com.apple.Preferences",))
    g.check_app("com.apple.Preferences")
    with pytest.raises(AppNotAllowed):
        g.check_app("com.apple.mobilesafari")


def test_no_allowlist_means_anything_not_blocked_is_allowed() -> None:
    gate().check_app("com.example.whatever")


# -- approval ---------------------------------------------------------------


def test_approval_is_scoped_to_one_specific_action() -> None:
    """Approving Send must not silently approve Delete."""
    g = gate()
    send = g.signature("tap", target("Send", identifier="send_btn"))
    delete = g.signature("tap", target("Delete", identifier="del_btn"))

    g.approve(send)

    assert g.is_approved(send)
    assert not g.is_approved(delete)


def test_revoking_clears_every_approval() -> None:
    g = gate()
    sig = g.signature("tap", target("Send"))
    g.approve(sig)
    g.revoke_all()
    assert not g.is_approved(sig)


# -- kill switch ------------------------------------------------------------


def test_repeated_failures_halt_the_session() -> None:
    g = gate(max_consecutive_failures=3)
    for _ in range(3):
        g.record_failure()
    with pytest.raises(SessionHalted) as exc_info:
        g.check_running()
    assert "stuck" in exc_info.value.message


def test_a_success_resets_the_failure_count() -> None:
    g = gate(max_consecutive_failures=3)
    g.record_failure()
    g.record_failure()
    g.record_success()
    g.record_failure()
    g.check_running()  # must not raise


def test_a_detected_loop_halts_the_session() -> None:
    g = gate()
    g.record_loop()
    with pytest.raises(SessionHalted) as exc_info:
        g.check_running()
    assert "looping" in exc_info.value.message


def test_resume_clears_the_halt() -> None:
    g = gate()
    g.halt("because")
    g.resume()
    g.check_running()


# -- redaction --------------------------------------------------------------


def test_card_numbers_and_emails_are_redacted() -> None:
    r = Redactor(PolicySettings())
    assert "4111111111111111" not in (r.text("card 4111111111111111") or "")
    assert "a@b.com" not in (r.text("mail a@b.com now") or "")
    assert r.redactions == 2


def test_ordinary_numbers_survive() -> None:
    r = Redactor(PolicySettings())
    assert r.text("Timer set for 25 minutes") == "Timer set for 25 minutes"


def test_redaction_walks_nested_payloads() -> None:
    r = Redactor(PolicySettings())
    payload = {"elements": [{"label": "write to a@b.com"}], "count": 1}
    out = r.payload(payload)
    assert "a@b.com" not in str(out)
    assert out["count"] == 1, "non-strings must pass through untouched"


def test_redaction_can_be_disabled() -> None:
    r = Redactor(PolicySettings(redact_patterns=()))
    assert r.active is False
    assert r.text("a@b.com") == "a@b.com"


# -- secrets ----------------------------------------------------------------


async def test_a_secret_resolves_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("IOS_MCP_SECRET_ICLOUD_PASSWORD", "hunter2")
    assert await resolve_secret("icloud-password") == "hunter2"


async def test_a_missing_secret_explains_how_to_store_one() -> None:
    with pytest.raises(SecretNotFound) as exc_info:
        await resolve_secret("definitely-not-set-anywhere")
    assert "add-generic-password" in (exc_info.value.hint or "")


async def test_a_malformed_reference_is_rejected() -> None:
    """Reference names reach a shell argument list; keep them boring."""
    with pytest.raises(SecretNotFound):
        await resolve_secret("../../etc/passwd")


# -- audit ------------------------------------------------------------------


def test_the_trail_summarises_resolution_tiers() -> None:
    trail = AuditTrail()
    trail.record("tap", {"ref": "e1"}, ok=True, resolved_via="exact")
    trail.record("tap", {"ref": "e2"}, ok=True, resolved_via="exact")
    trail.record("tap", {"target": "Send"}, ok=False, resolved_via="text-fuzzy", error="boom")

    summary = trail.summary()
    assert summary["steps"] == 3
    assert summary["failures"] == 1
    assert summary["resolution_tiers"] == {"exact": 2, "text-fuzzy": 1}


def test_the_trail_writes_json(tmp_path) -> None:
    trail = AuditTrail()
    trail.record("tap", {"ref": "e1"}, ok=True)
    path = trail.write(tmp_path / "sub" / "trace.json")
    assert path.exists()
    assert '"steps"' in path.read_text()


def test_the_trail_attributes_its_failures() -> None:
    """A count of failures does not say which of three fixes is needed."""
    trail = AuditTrail()
    trail.record("tap", {"ref": "e1"}, ok=True, resolved_via="exact")
    trail.record("tap", {"target": "Send"}, ok=False, code="runner_crashed", error="boom")
    trail.record("tap", {"target": "Ghost"}, ok=False, code="element_not_found", error="nope")
    trail.record("launch_app:x", {}, ok=False, code="app_not_allowed", error="no")

    summary = trail.summary()
    assert summary["faults"] == {"device": 1, "perception": 1, "policy": 1}
    assert sum(summary["faults"].values()) == summary["failures"]


def test_an_absorbed_device_fault_is_reported_beside_the_histogram() -> None:
    """A recovered action succeeded, so it cannot go in a sum over failures.

    It still says the device misbehaved, which is worth knowing.
    """
    trail = AuditTrail()
    trail.record("tap", {"ref": "e1"}, ok=True, recovered=True)
    trail.record("tap", {"ref": "e2"}, ok=True)

    summary = trail.summary()
    assert summary["absorbed_device_faults"] == 1
    assert summary["faults"] == {}


def test_an_entry_carries_the_cause_as_a_field() -> None:
    """Reading the trail used to mean parsing '[code]' back out of a string."""
    trail = AuditTrail()
    entry = trail.record(
        "tap",
        {"target": "Ghost"},
        ok=False,
        error="[element_not_found] Nothing on screen matches 'Ghost'",
        code="element_not_found",
        details={"closest": ["e3: button 'Wi-Fi'"]},
    )
    assert entry.code == "element_not_found"
    assert entry.to_dict()["details"] == {"closest": ["e3: button 'Wi-Fi'"]}


def test_a_plain_entry_gains_no_new_keys() -> None:
    """Every existing consumer must see byte-identical output."""
    trail = AuditTrail()
    entry = trail.record("tap", {"ref": "e1"}, ok=True, resolved_via="exact")
    assert set(entry.to_dict()) == {"seq", "at", "action", "args", "ok", "resolved_via"}
