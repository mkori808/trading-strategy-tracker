# Scheduled automation for the virtual-fill paper trading loop

Two Windows Task Scheduler tasks, registered under the `micha` user account
(no admin rights required, `RunLevel: Limited`):

| Task name | Trigger (local time) | ~ET equivalent | Runs |
|---|---|---|---|
| `PaperTrading-Friday-Record` | Friday 1:15 PM | ~4:15pm ET | `python run_rebalance.py --record` |
| `PaperTrading-Monday-Fill` | Monday 7:15 AM | ~10:15am ET | `python run_rebalance.py --fill` |

## Proposed, unregistered Track 4 daily schedule

Track 4 is deliberately **not registered**.  The two wrapper scripts exist
only so that, after explicit approval of the dry run and a separate launch
authorization, they can be registered without modifying Tracks 1–3.

| Proposed task name | Trigger (local Pacific time) | ~ET equivalent | Runs |
|---|---|---|---|
| `PaperTrading-Daily-Record` | Monday–Friday 1:15 PM | ~4:15pm ET | `scheduled_daily_record.ps1` / `--record-daily` |
| `PaperTrading-Daily-Fill` | Monday–Friday 7:15 AM | ~10:15am ET | `scheduled_daily_fill.ps1` / `--fill-daily` |

The record step is a virtual close entry; the fill step reads first-minute
IEX opening bars, records close-to-open only, and never submits broker
orders. Both commands fail closed while `daily_overnight.launch_authorized`
is false in `config.json`. Do not register either task until the user has
approved the dry run and enabled that explicit launch gate.

Local time is Pacific (matches this machine's current timezone, UTC-07:00
during DST). **If this machine's timezone or DST convention changes, the
trigger times need updating** to keep the ~15-minute buffer after the 4pm/
10am ET targets.

## What they do

Each task runs a small wrapper script (`scheduled_record.ps1` /
`scheduled_fill.ps1`) that `cd`s into `V2/paper_trading`, invokes the
resolved python interpreter directly (not the `python` PATH alias, which
Task Scheduler's non-interactive context may not resolve the same way an
interactive shell does), and appends timestamped output to
`V2/paper_trading/logs/record.log` / `logs/fill.log`.

Neither task ever calls Alpaca's order-submission endpoints -- `--record`
only computes signals from Sharadar, and `--fill` only reads Alpaca market
data (the Monday-open price) to price a virtual fill in the local
`state/*_state.json` files. See `state_engine.py` for the fill scheme.

## Constraints / what can make a run silently not happen

- **`LogonType: Interactive`** -- the task only fires while `micha` is
  logged into a session on this machine (locked screen is fine; logged
  off, or a different Windows machine, is not). This was a deliberate
  default (avoids needing to store a password for a batch/S4U logon) --
  ask if you'd rather it run even when logged off.
- **`WakeToRun: False`** -- if the machine is asleep at the trigger time,
  it will NOT wake up to run it. `StartWhenAvailable: True` means it will
  run as soon as you next log in/wake the machine after a missed trigger,
  rather than skipping the week entirely -- but that could mean a Friday
  --record running late Saturday if the laptop was closed all weekend.
- Battery power is no longer a blocker (`DisallowStartIfOnBatteries` was
  turned off after the default registration set it `True`).

## Checking on it

```powershell
Get-ScheduledTask -TaskName "PaperTrading-*" | Select TaskName, State
Get-ScheduledTaskInfo -TaskName "PaperTrading-Friday-Record" | Select LastRunTime, LastTaskResult, NextRunTime
Get-ScheduledTaskInfo -TaskName "PaperTrading-Monday-Fill"   | Select LastRunTime, LastTaskResult, NextRunTime
Get-Content V2\paper_trading\logs\record.log -Tail 40
Get-Content V2\paper_trading\logs\fill.log -Tail 40
```

`LastTaskResult` of `0` means success; anything else, check the log file
for the actual Python traceback (the wrapper never lets a Python exception
crash silently without a `--fill FAILED: ...` line in the log).

## Removing or changing the schedule

```powershell
Unregister-ScheduledTask -TaskName "PaperTrading-Friday-Record" -Confirm:$false
Unregister-ScheduledTask -TaskName "PaperTrading-Monday-Fill" -Confirm:$false
```

or re-run the `Register-ScheduledTask` setup with `-Force` and a different
`New-ScheduledTaskTrigger` to change the time.
