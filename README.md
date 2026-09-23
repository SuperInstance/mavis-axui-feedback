# mavis-axui-feedback

> Bidirectional projection — workbook → runtime → workbook, with **typed events, signed commands, polyformality checks, phantom-runtime detection, and bounded recursion**.

**Principle (Casey's GAN loop, Sept 23 round 13)**: "iteratively play-testing and developing in a GAN loop. the key is to really understand what we are trying to do at the highest levels so the GAN's abilities actually emerge and new insightful challenges are appearing at all times."

The Generator proposes. The Adversary finds the holes. The fixes become the next build.

## What's in here

A **feedback ring** that lets a workbook cell project to a runtime AND receive commands back from the runtime. The boat example: the autopilot cell projects its target heading to the LED dashboard's sub-workbook; the panel rotary encoder's rotation projects back into the autopilot cell's `target_heading_deg`.

```python
from mavis_axui_feedback import FeedbackRing, FeedbackCell, SubWorkbook

sw = SubWorkbook(name="led_dashboard_sub")
sw.add_cell(FeedbackCell(cell_id="led_state",
                          outputs=["pixels"],
                          inputs=["target_heading"],
                          secret="topsecret"))

ring = FeedbackRing(parent_cell_id="autopilot_controller",
                     sub_workbook=sw,
                     runtime_adapter=read_encoder)

result = ring.step(parent_state={"led_state": 45.0})
# result["to_parent"]["led_state.target_heading"] = 180.0  # encoder turned
```

## Five typed event kinds

| Type | Direction | Purpose |
|------|-----------|---------|
| `COMMAND` | runtime → cell | User input / external trigger |
| `STATE`   | cell → runtime | Current value |
| `ACTION`  | cell → cell | In-cell transition |
| `TICK`    | time | Time progression |
| `WITNESS` | log | Signed, chained log entry |

Each event has a SHA-256 hash; each event's `prev_hash` links to the previous event in the same cell's log (witness chain).

## Five new holes the Adversary found (Round 7)

1. **Phantom runtime** — when the runtime adapter is None, the cell silently no-ops. But a None runtime means the LED is unplugged — the autopilot should KNOW.
2. **No polyformality** — three feedback rings with the same parent could diverge. Now `polyformality_check()` measures agreement.
3. **Open event types** — adding more `FeedbackType` is a code change. Now event types are extensible.
4. **Lossy projection one-way** — `project_lossy()` rounds the cell's output, but the runtime also has precision. Now `runtime_precision` is honored.
5. **Recursion unbounded** — sub-workbooks can contain sub-workbooks. Now `max_recursion_depth=3` defaults.
6. **Unsigned commands** — an attacker on the network can inject `target_heading=180` to send the boat into a reef. Now HMAC-signed commands are required when `cell.secret` is set.

## API

```python
# Typed events with hash chain
from mavis_axui_feedback import FeedbackEvent, FeedbackType
ev = FeedbackEvent(type=FeedbackType.STATE, cell_id="c", channel="x", value=1.0)
ev.compute_hash()

# Loss-aware projection
from mavis_axui_feedback import project_lossy
project_lossy(3.14159, precision=0.01)  # → 3.14

# Signed commands (Round 7)
from mavis_axui_feedback import sign_command, verify_command_signature
sig = sign_command("c", "x", 42.0, secret="topsecret")
verify_command_signature("c", "x", 42.0, sig, "topsecret", ts=time.time())

# Polyformality check (Round 7)
from mavis_axui_feedback import polyformality_check
score = polyformality_check([ring1, ring2, ring3])
# score=1.0 if all rings agree; lower if they diverge

# Project to ESP32 with bidirectional feedback
from mavis_axui_feedback import project_to_runtime
src = project_to_runtime(ring, "esp32_arduino")
# Generates Arduino sketch with LED rendering + encoder POST
```

## GAN Loop history

- **Round 0**: Generator proposed 6 highest-level doctrine candidates. Adversary found **time**, **cost**, **failure**, **plurality** hidden.
- **Round 1**: Generator proposed 4 next-builds. Adversary found all 4 are extensions; **PLURALITY** is the revolution.
- **Round 2**: Adversary found that the boat LED matrix projects back. **Bidirectional projection** = the build.
- **Round 3**: Built `FeedbackRing`. 14 tests pass.
- **Round 4**: Adversary found 5 more holes: infinite loop, lossy projection, phantom cells, witness chain, type the feedback.
- **Round 5**: Built typed events + witness chains + oscillation detection. 26 tests pass.
- **Round 6**: Adversary found 7 more holes: phantom runtime, no polyformality, no signatures.
- **Round 7**: Built signed commands + polyformality check + phantom detection. **42 tests pass**.

## Source precursors

- `mavis-sfm` — Momentum, alignment, verification (used in `_score_oscillation`)
- `mavis-tfm` — TFMClock (private cell clocks with skew)
- `autoclaw` — verification-as-bedrock; SIGNED commands derive from there
- `the-beyond` — vessel IS experiment; bidirectional = the vessel extending into the runtime
- `essay-127` — "I saw her as the cell sees her." Bidirectional projection is "I see you seeing me."

Co-authored-by: Mavis <Mavis@superinstance.dev>
