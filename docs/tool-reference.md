# Tool reference

Annotations tell a client what a tool does before calling it: `read-only` tools
can be run freely, `destructive` ones may do something irreversible and are
additionally guarded by the policy gate.

Every action tool accepts `idem_key`, which makes a repeat return the original
result instead of touching the device again. Use it when retrying something you
are unsure completed.

## Session and device

| Tool | Notes |
|---|---|
| `ios_doctor` | Toolchain, tunnel and signing checks, each with a remedy. Run this first when anything misbehaves. |
| `ios_list_devices` | Simulators and attached iPhones. `ready: false` comes with `blockers`. |
| `ios_open_session` | Boots or verifies the device, starts WebDriverAgent, returns the first screen. Prefers a simulator when no device is named. |
| `ios_session_status` | Device, foreground app, halted state, audit summary. |
| `ios_close_session` | Releases the device and shuts down its runner. |

## Apps

| Tool | Notes |
|---|---|
| `ios_list_apps` | Bundle identifiers, filterable by `user`/`system`/`all`. |
| `ios_launch_app` | `fresh=true` restarts rather than resuming. |
| `ios_terminate_app` | Force-quit. |
| `ios_open_url` | Deep links. Usually the cheapest way to reach a screen. On iOS 26 use `App-prefs:root`, not the retired `prefs:`. |
| `ios_install_app` | From a local `.app` or `.ipa`. |

## Perception

| Tool | Notes |
|---|---|
| `ios_observe` | The digest. `query` and `region` narrow it; prefer those over raising `budget` when truncated. `include_elements` adds structured JSON at roughly double the cost. |
| `ios_screenshot` | `annotate_refs=true` draws numbered boxes, for UI with no accessibility data. |
| `ios_read_text` | Text of the screen or one element. Use this to extract content; `ios_observe` is shaped for deciding what to tap. |
| `ios_wait_for` | Waits for text to appear or disappear. Reports failure as data rather than raising. |
| `ios_get_logs` | Device logs, when the UI does not explain a failure. |
| `ios_export_trace` | Everything this session did. |

## Actions

| Tool | Notes |
|---|---|
| `ios_tap` | `ref` preferred over `target`. Supports `double` and `long_press_s`. |
| `ios_type` | Focuses the field first when given a ref or target. Never put a real credential here. |
| `ios_type_secret` | Takes a keychain reference, not a value. See [SAFETY.md](../SAFETY.md). |
| `ios_set_value` | Switches, sliders, steppers, pickers. Prefer this over tapping a switch: it checks current state, so asking for `on` when already on does nothing rather than turning it off. |
| `ios_scroll` | `until` stops as soon as the text appears, and gives up when the content stops moving. |
| `ios_swipe` | One swipe, for carousels and swipe-to-reveal. |
| `ios_drag` | Between two refs, for reordering. |
| `ios_press_button` | Hardware (`home`, `volumeUp`, `siri`) and keyboard (`enter`, `tab`, `delete`, `dismiss_keyboard`). |
| `ios_handle_alert` | Read the alert text before choosing. |
| `ios_halt` / `ios_resume` | Stop and restart a session deliberately. |

## Environment

| Tool | Notes |
|---|---|
| `ios_set_permission` | Simulator only. |
| `ios_clipboard` | `get` or `set`. Writing beats typing long strings, which autocorrect can mangle. |
| `ios_set_device_state` | Orientation anywhere; appearance, location and status-bar freeze are Simulator only. |

## Resources

`ios://devices`, `ios://session`, `ios://session/screen`,
`ios://session/screenshot`, `ios://capabilities`. Resources are pulled rather
than pushed into context, so `ios://session/screen` includes the structured
element list that `ios_observe` omits.

`ios://capabilities` reports which tools work on the attached device, so an
agent can check rather than discover it by failing.

## Prompt

`ios_operator` teaches the observe/act/verify loop, ref usage, when to fall
back to a screenshot, and the safety rules.

## Errors

Failures arrive as JSON with a machine-readable `code`, a `hint`, and often
`details` naming candidates:

```json
{
  "error": "element_not_found",
  "message": "Nothing on screen matches 'Snd'",
  "hint": "Check the closest candidates below...",
  "details": { "closest": ["e7: button 'Send'", "e2: button 'Save'"] }
}
```

Common codes: `element_not_found`, `element_ambiguous`, `element_stale`,
`element_not_interactable`, `action_requires_approval`, `app_not_allowed`,
`session_halted`, `device_not_ready`, `tunnel_down`, `runner_crashed`.
