"""Tier 3: a picker wheel on a real iPhone, which is the only place one exists.

A simulator's Settings is a reduced build with no Date & Time pane and no Clock
app, so there is no stock wheel on it to drive. That is not a detail: every
claim this file makes was wrong in the fixtures first and only hardware said
so.

Three bugs came out of one run against an iPhone 17 Pro Max on iOS 26.6.1, and
each one is asserted below:

* Apple wraps a picker in an unlabelled `Cell` as well as a `Picker`, and the
  cell ate the middle wheel of a three-column time picker.
* A wheel's value is a spoken phrase, `5 o'clock` and `36 minutes`, not `5`
  and `36`.
* A coordinate *drag* cannot move a `UIPickerView` by a known amount. It
  decelerates through four rows at 0.2s and three at 1.6s, varying run to run,
  and since the hour wheel wraps, stepping by four from 5 o'clock reaches 1
  and 9 and nothing else, forever. A *tap* on the neighbouring row moves it by
  exactly one.

## What this changes on the phone, and how it is put back

It opens the Clock app's add-alarm sheet, turns a wheel, and cancels in a
`finally`. No alarm is saved and nothing else is touched. Opt-in twice over,
like the rest of tier 3: the `device` marker and `IOS_MCP_ALLOW_DEVICE=1`.
"""

from __future__ import annotations

import pytest
from device_support import requires_device

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.errors import ElementNotFound
from ios_mcp.session import IosSession

pytestmark = [pytest.mark.device, requires_device]

CLOCK = "com.apple.mobiletimer"


def device_settings() -> Settings:
    cfg = Settings()
    # A device snapshot is slower than a simulator's, so the settle ceiling has
    # to exceed `stable_samples` snapshots or every action reports an unsettled
    # screen. Same reason as `tests/evals/agent/test_agent_device.py`.
    cfg.stabilize.max_wait_s = 20.0
    cfg.wda.startup_timeout_s = 300.0
    cfg.policy.confirm_destructive = False
    return cfg


@pytest.fixture(scope="module")
async def phone():
    """Lease the physical device by name, never whatever the pool prefers.

    `acquire()` with no argument ranks simulators above phones on purpose, so
    tier 3 has to ask for the phone explicitly. The `kind` assertion is what
    would catch this quietly leasing a simulator and calling it hardware.
    """
    from ios_mcp.devices.discovery import list_real_devices

    cfg = device_settings()
    phones = [d for d in await list_real_devices(cfg) if d.ready and d.kind == "device"]
    assert phones, "no ready physical device, though the skip gate said otherwise"

    pool = DevicePool(cfg)
    try:
        lease = await pool.acquire(phones[0].udid, bundle_id=CLOCK)
        assert lease.device.kind == "device", (
            f"acquired a {lease.device.kind}, not a phone; tier 3 must not run on a simulator"
        )
        yield IosSession(lease, cfg)
    finally:
        await pool.release_all()


async def _open_alarm_sheet(session: IosSession):
    """Reach the add-alarm sheet from wherever Clock happens to be."""
    await session.terminate_app(CLOCK)
    await session.launch_app(CLOCK, fresh=True)
    digest = await session.observe()
    if not any(n.role == "picker" for n in digest.nodes):
        for step in ("Cancel", "Alarms", "Add"):
            try:
                await session.tap(target=step)
            except ElementNotFound:
                continue
        digest = await session.observe()
    wheels = [n for n in digest.nodes if n.role == "picker"]
    if not wheels:
        pytest.skip(f"no alarm wheel on this Clock build: {digest.render()[:300]}")
    return digest, wheels


async def test_every_column_of_a_real_time_picker_is_visible(phone: IosSession) -> None:
    """Three wheels in, three wheels out.

    Two of three survived before `picker` was added to `ROLE_PRECEDENCE`: the
    minutes column tied with the unlabelled `Cell` wrapping the picker, and the
    cell won on a precedence list the wheel was absent from. The hours and the
    meridiem either side of it were unaffected, which is what made it invisible
    until someone counted.
    """
    _, wheels = await _open_alarm_sheet(phone)
    try:
        assert len(wheels) == 3, f"expected hours, minutes, meridiem: {[w.value for w in wheels]}"
        assert all(w.value for w in wheels), (
            f"a wheel with no value: {[w.render() for w in wheels]}"
        )
        assert all(w.scrollable for w in wheels)
    finally:
        await phone.tap(target="Cancel")


async def test_a_real_wheel_moves_by_exactly_one_row(phone: IosSession) -> None:
    """The whole argument for a tap over a drag, on the control that proved it."""
    _, wheels = await _open_alarm_sheet(phone)
    hours = wheels[0]
    before = hours.value
    try:
        await phone._nudge_wheel(phone.resolve(await phone.snapshot(), ref=hours.ref), 1)
        after = next(n for n in (await phone.snapshot()).nodes if n.role == "picker").value

        assert after != before, "the wheel did not move at all"
        assert _hour(after) is not None and _hour(before) is not None
        assert (_hour(before) - _hour(after)) % 12 == 1, (
            f"one tap moved {before!r} to {after!r}, which is not one row"
        )
    finally:
        await phone.tap(target="Cancel")


async def test_an_option_a_real_wheel_does_not_have_reports_its_spellings(
    phone: IosSession,
) -> None:
    """An hour wheel wraps, so this is also the proof that the loop terminates.

    Waiting for the value to stop changing never returns on a control with no
    end. Stopping on a value already seen walks it once round, which on a
    twelve-hour wheel is twelve steps, and hands back the spellings: the point
    is that an agent asking for `7` can discover it is called `7 o'clock`.
    """
    _, wheels = await _open_alarm_sheet(phone)
    try:
        with pytest.raises(ElementNotFound) as exc_info:
            await phone.set_value("7", ref=wheels[0].ref)

        seen = exc_info.value.details["seen"]
        assert len(seen) == 12, f"an hour wheel has twelve options, saw {seen}"
        assert any("7" in option for option in seen), seen
    finally:
        await phone.tap(target="Cancel")


def _hour(value: str | None) -> int | None:
    if not value:
        return None
    head = value.split()[0]
    return int(head) if head.isdigit() else None
