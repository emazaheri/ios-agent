# Safety

Driving a person's phone with their real accounts is a different risk from test
automation. The tap that dismisses a dialog in CI can send a message, make a
payment, or delete a photo library here. The policy layer is on by default and
sits in front of every action.

## Approval before the fact

Actions are classified *before* they run, so approval is asked while the
operation is still preventable rather than reported afterwards.

Matching is on whole words. "Sender" and "Undelete" do not trip the "send" and
"delete" rules, because a gate that prompts on everything trains an operator to
approve reflexively, which is worse than no gate.

Two modes:

- The MCP client supports elicitation: the human is asked directly, with the
  action and target named.
- It does not: the call raises `action_requires_approval` carrying a signature.
  The caller confirms with the user, then repeats the call with
  `approve=<signature>`. This is the path an external human-in-the-loop layer
  uses.

A client that cannot answer is treated as refusal. An unanswerable question is
not consent.

Approval is scoped to one specific action. Approving Send does not approve
Delete, and a refusal is never cached as consent.

## Secrets

`ios_type_secret` takes a *reference*, not a value:

```
ios_type_secret(secret_ref="icloud-password", ref="e4")
```

The value is read from the host keychain and sent straight to the device. It
appears in no prompt, no tool result, and no audit entry. It is also
deliberately not run through the destructive-text rules, since a password
containing the word "delete" is not an instruction.

Store one with:

```bash
security add-generic-password -s ios-mcp -a icloud-password -w
```

or set `IOS_MCP_SECRET_ICLOUD_PASSWORD` in the environment.

Never put a real credential in `ios_type`, where it would enter the transcript.

## Scope

Apps holding payment or credential data are blocked by default
(`policy.app_blocklist`). An allowlist may be set instead, in which case
everything else is refused.

## Redaction

An accessibility tree contains whatever is on screen, which on a real phone
means message bodies, card numbers, and email addresses. Card-like numbers and
email addresses are stripped from digests, text reads, logs and traces before
they leave the server. Patterns are configurable via `policy.redact_patterns`.

## Stopping

- Repeated consecutive failures halt the session rather than letting an agent
  flail at a screen it does not understand.
- A detected loop halts it too: if actions keep landing on the same few
  screens, the agent is stuck. Observations do not count, since re-reading a
  screen is careful behaviour, not thrashing.
- `ios_halt` stops a session immediately; `ios_resume` clears it once a human
  has decided it is safe.

## Audit

Every session records an ordered list of what it did: tool, arguments,
resolution tier, screen fingerprint, outcome. `ios_export_trace` returns it.
This serves three purposes at once: explaining what happened, replaying a run
as a regression test, and providing worked examples for a future agent.

## Configuration

```toml
[policy]
enabled = true
confirm_destructive = true
app_allowlist = ["com.apple.Preferences"]   # empty means "anything not blocked"
max_consecutive_failures = 5
redact_screenshots = false
```

Or `IOS_MCP_POLICY__CONFIRM_DESTRUCTIVE=false` in the environment.

Turning the gate off is a deliberate choice and a reasonable one for a
simulator running a test suite. It is not reasonable for a device carrying
someone's real accounts.

## What this does not protect against

The gate is a heuristic over labels and roles. It will not recognise a
destructive action whose control is unlabelled or misleadingly named, and it
cannot know that tapping a particular row costs money. Approval mode and an app
allowlist are the real controls; the label rules are a convenience on top.
