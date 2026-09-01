# Security policy

## What this software does

`ios-agent` drives a real iPhone or an iOS Simulator on your behalf, and when
it is pointed at a physical device it acts on that device's real accounts. A
tap that dismisses a dialog in a test suite can send a message, make a payment,
or delete a photo library here. Treat it accordingly.

The controls that exist, and their limits, are documented in
[SAFETY.md](SAFETY.md). The short version:

- Destructive actions are classified **before** they run and require approval.
- Secrets are passed by reference and read from the host keychain, never
  through a prompt, a tool result, or the audit trail.
- Card-like numbers and email addresses are stripped from digests, text reads
  and traces before they leave the server.
- The approval gate is a heuristic over labels and roles. It cannot recognise a
  destructive action whose control is unlabelled or misleadingly named. An app
  allowlist and approval mode are the real controls.

## Supported versions

This is a portfolio and developer tool rather than a deployed service. Fixes
land on `main`; there are no maintained release branches.

## Reporting a vulnerability

Please report privately rather than in a public issue, using GitHub's
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository: **Security > Report a vulnerability**.

Include what you were driving (simulator or device), the tool or layer
involved, and a trace from `ios_export_trace` if you have one, with anything
sensitive removed.

Expect an acknowledgement within a week. Since this is a single-maintainer
project, please allow reasonable time for a fix before disclosing publicly.

## Out of scope

- Anything requiring an attacker to already control the machine running the
  server, or the phone it is paired with.
- The WebDriverAgent dependency itself. Report those upstream to
  [appium/WebDriverAgent](https://github.com/appium/WebDriverAgent).
- Turning the policy gate off. `IOS_MCP_POLICY__ENABLED=false` is a documented
  switch, and disabling a safeguard is not a vulnerability in it.
