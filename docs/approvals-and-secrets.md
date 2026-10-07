# Approvals and secrets

What happens, on each surface, when an action needs a yes, and how to type a
password without it entering a transcript. The rules themselves (what is gated
and why) are in [Safety](../SAFETY.md); this page is how to work with them.

Every example below was captured from a real run on an iOS 27 simulator. To
trigger the gate harmlessly, they type the word "delete" into the Settings
search field: typed text is judged as well as buttons, and a search changes
nothing.

## What is gated

Anything matching send, pay, buy, delete, confirm, sign out and similar, and
anything that reaches another person (like, follow, reply, share, message).
The match is on whole words, against the control's label and id and any text
being typed. Nothing gated reaches the device until someone says yes.

## In an MCP client that can ask

A client that supports MCP elicitation shows the question itself:

```
Allow type?
type on "delete" matches the destructive rule 'delete'
This affects the real device and may not be reversible.
```

Answer yes and the action runs. Decline, and the tool returns
`action_rejected_by_policy` with the hint not to retry; the agent should ask
what you want instead.

## In an MCP client that cannot

A client without elicitation, or one whose question could not be delivered,
gets the decision handed back with a signature, and nothing happens on the
device:

```json
{
  "error": "action_requires_approval",
  "message": "type on \"delete\" matches the destructive rule 'delete'",
  "hint": "Confirm with the user, then repeat the call with approve='type:-:6197595503f0'.",
  "details": {"signature": "type:-:6197595503f0", "verdict": {"risk": "destructive", "reason": "..."}}
}
```

Ask the person, then repeat the same call with `approve` set to the
signature. The repeat runs. This is also the hook for your own
human-in-the-loop layer.

A signature names one action: the verb, the element, and for typing, a hash
of the text. Approving "delete" typed into a field does not approve
"delete everything" typed into the same field.

## In the terminal app

`ios-agent` refuses everything gated unless you pass `--approve`, because a
run nobody is watching cannot be asked. With it, the run stops and asks:

```
  ? allow type_text
    type on "delete" matches the destructive rule 'delete'
  Allow this one action? [y/N]
```

Anything but an explicit yes is a no, and the run carries on without it. The
summary at the end says how many times it stopped to ask.

## From Python

`run_goal` takes an `approve` callback. It receives the action, the reason and
the signature, and returns whether to allow it. Without one, everything gated
is refused. See [Build on the library](library.md) for a full example.

Driving `IosSession` directly, a gated action raises `ActionRequiresApproval`
carrying the signature. Call `session.approve(signature)` and repeat the
action, or pass `on_approval=` when creating the session to be asked inline.

## Typing a password

Never put a real credential in `ios_type` or a goal: it enters the transcript.
Store it in the Mac's keychain and type it by reference:

```bash
security add-generic-password -s ios-mcp -a icloud-password -w
```

```
ios_type_secret(secret_ref="icloud-password", ref="e4")
```

The value goes from the keychain to the device and nowhere else. If the
keychain has nothing under that name, `IOS_MCP_SECRET_ICLOUD_PASSWORD` in the
environment is used instead.

Type secrets into password fields. A password field shows dots, so the screen
never holds the value. Any other field shows what was typed, and the app may
repeat it ("No Results for ..."). From the moment a secret is typed, the
session replaces it with `[secret]` in every screen, read and audit entry it
returns, which was checked on a simulator by typing one into Settings search.
Two things it cannot do: scrub a value shorter than four characters, and edit
a screenshot.

The typed value is still checked: a secret is read back by length, so one that
lost characters fails rather than being reported as typed.
