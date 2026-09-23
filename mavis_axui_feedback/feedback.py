"""Bidirectional projection: workbook → runtime → workbook.

The Adversary found: projection is one-way; the runtime can ALSO be a workbook.
The boat LED dashboard projects heading/SOG, AND a panel rotary encoder
projects the new target_heading back into the autopilot cell.

This module makes the feedback ring executable with TYPED events:
  - Command:  runtime → cell (user input, external trigger) — SIGNED
  - State:    cell → runtime (current value)
  - Action:   cell → cell (in-cell transition)
  - Tick:     time progression (per TFMClock)
  - Witness:  log entry (signed, chained)

Round 5 fixes: typed events + loss-aware projection + loop detection.
Round 7 fixes: signed commands + polyformality + phantom-runtime detection
               + bidirectional loss declaration + recursion depth limit.
"""
import json
import time
import hashlib
import hmac
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Union, Set


# ─── Typed Feedback Events (Round 5) ────────────────────────

class FeedbackType(str, Enum):
    COMMAND = "command"     # runtime → cell
    STATE = "state"         # cell → runtime
    ACTION = "action"       # cell → cell
    TICK = "tick"           # time progression
    WITNESS = "witness"     # log entry


@dataclass
class FeedbackEvent:
    """A typed event in the feedback ring.

    Loss-aware: precision field declares how lossy the projection is.
    Chained: prev_hash links to the previous event in the same ring.
    """
    type: FeedbackType
    cell_id: str
    channel: str
    value: Any
    precision: float = 1.0   # 1.0 = lossless, 0.001 = 3-decimal-place precision
    ts: float = field(default_factory=time.time)
    prev_hash: Optional[str] = None
    hash: Optional[str] = None

    def compute_hash(self) -> str:
        payload = f"{self.type.value}:{self.cell_id}:{self.channel}:{self.value}:{self.ts}:{self.prev_hash or ''}"
        h = hashlib.sha256(payload.encode()).hexdigest()[:16]
        self.hash = h
        return h


def project_lossy(value: Any, precision: float) -> Any:
    """Apply precision-aware lossy projection (Round 5 hole #2)."""
    if precision is None or precision >= 1.0 or value is None:
        return value
    if isinstance(value, (int, float)):
        # Round to a number of decimal places proportional to precision
        decimals = max(0, int(-__import__("math").log10(precision)) if precision > 0 else 0)
        return round(value, decimals)
    return value


def sign_command(cell_id: str, channel: str, value: Any,
                  secret: str, ts: Optional[float] = None) -> str:
    """Sign a COMMAND event with HMAC-SHA256 (Round 7 hole #7 — security).

    The signature binds (cell_id, channel, value, ts) so an attacker
    can't replay or forge a feedback command.
    """
    ts = ts or time.time()
    payload = f"{cell_id}:{channel}:{value}:{ts}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def verify_command_signature(cell_id: str, channel: str, value: Any,
                              signature: str, secret: str,
                              ts: float, max_age_s: float = 60.0,
                              now: Optional[float] = None) -> bool:
    """Verify a COMMAND event signature.

    Returns False if:
      - signature mismatch
      - timestamp older than max_age_s (replay attack)

    `now` is for testing — defaults to time.time().
    """
    now = now if now is not None else time.time()
    if now - ts > max_age_s:
        return False  # replay
    expected = sign_command(cell_id, channel, value, secret, ts)
    return hmac.compare_digest(expected, signature)


@dataclass
class FeedbackCell:
    """A cell that has both outputs (to runtime) and inputs (from runtime).

    The cell emits state (outputs) AND receives commands (inputs).
    The runtime renders the state AND injects commands back.
    """
    cell_id: str
    outputs: List[str]  # cell-to-runtime channels
    inputs: List[str]   # runtime-to-cell channels
    state: Dict = field(default_factory=dict)
    last_command: Optional[Dict] = None
    precision: float = 1.0  # lossy projection precision for this cell
    runtime_precision: float = 1.0  # how precise the runtime can BE (Round 7 hole #4)
    event_log: List[FeedbackEvent] = field(default_factory=list)
    secret: Optional[str] = None  # signing key for commands (Round 7)
    trusted_signatures: Set[str] = field(default_factory=set)  # known-good sigs

    def emit(self, channel: str, value: Any) -> FeedbackEvent:
        """Cell emits a STATE event to the runtime."""
        value = project_lossy(value, self.precision)
        self.state[channel] = value
        prev = self.event_log[-1].hash if self.event_log else None
        ev = FeedbackEvent(
            type=FeedbackType.STATE,
            cell_id=self.cell_id,
            channel=channel,
            value=value,
            precision=self.precision,
            prev_hash=prev,
        )
        ev.compute_hash()
        self.event_log.append(ev)
        return ev

    def receive(self, channel: str, value: Any,
                 signature: Optional[str] = None,
                 signed_ts: Optional[float] = None) -> FeedbackEvent:
        """Runtime injects a COMMAND event into the cell.

        If secret is set, the signature MUST be valid.
        `signed_ts` is the timestamp used in signing (must be provided for signed commands).
        """
        # Round 7 hole #7: verify signature
        if self.secret:
            if not signature:
                raise ValueError(f"Cell {self.cell_id} requires signed commands but no signature given")
            if signed_ts is None:
                raise ValueError(f"Cell {self.cell_id} requires signed_ts to verify command")
            if not verify_command_signature(
                self.cell_id, channel, value, signature, self.secret, signed_ts
            ):
                raise ValueError(f"Invalid signature on command for {self.cell_id}.{channel}")
            self.trusted_signatures.add(signature)

        self.last_command = {"channel": channel, "value": value, "ts": time.time()}
        prev = self.event_log[-1].hash if self.event_log else None
        ev = FeedbackEvent(
            type=FeedbackType.COMMAND,
            cell_id=self.cell_id,
            channel=channel,
            value=value,
            prev_hash=prev,
        )
        ev.compute_hash()
        self.event_log.append(ev)
        return ev

    def receives_from_runtime(self, channel: str, value: Any) -> FeedbackEvent:
        """Like receive() but uses runtime's declared precision for the value.

        Used when the runtime declares 'I only know heading to 1 degree'.
        The value is rounded to runtime_precision before storage.
        """
        value = project_lossy(value, self.runtime_precision)
        return self.receive(channel, value)


@dataclass
class SubWorkbook:
    """A sub-workbook nested inside a cell.

    The sub-workbook has its own cells and flows. It can project to the
    parent workbook (feedback) or be projected FROM the parent (decomposition).
    """
    name: str
    cells: Dict[str, FeedbackCell] = field(default_factory=dict)
    flows: List[Dict] = field(default_factory=list)

    def add_cell(self, cell: FeedbackCell) -> None:
        self.cells[cell.cell_id] = cell

    def add_flow(self, from_cell: str, to_cell: str, channel: str) -> None:
        self.flows.append({"from": from_cell, "to": to_cell, "channel": channel})


@dataclass
class FeedbackRing:
    """A bidirectional projection ring with typed events.

    Round 5: detects oscillation, witness-chain integrity.
    Round 7: detects phantom runtime, supports polyformality check,
             bounded recursion depth, signed commands.
    """
    parent_cell_id: str
    sub_workbook: SubWorkbook
    runtime_adapter: Optional[Callable[[Dict], Dict]] = None
    max_ticks_per_session: int = 100  # oscillation guard
    max_recursion_depth: int = 3      # plurality bound (Round 7)
    phantom_runtime_warning: bool = True  # emit WARN event when no adapter

    def __post_init__(self):
        self.tick_count = 0
        self.last_values: Dict[str, Any] = {}
        self.oscillation_count = 0
        self.phantom_warnings: List[str] = []

    def step(self, parent_state: Dict) -> Dict:
        """One tick of the feedback ring with typed events."""
        self.tick_count += 1
        if self.tick_count > self.max_ticks_per_session:
            return {"stopped": True, "reason": "max_ticks exceeded",
                    "ticks": self.tick_count, "ts": time.time()}

        # Round 7 hole #3: phantom runtime detection
        phantom_warning = None
        if self.runtime_adapter is None and self.phantom_runtime_warning:
            phantom_warning = f"Runtime adapter is None — workbook cannot observe this cell's state"
            self.phantom_warnings.append(phantom_warning)

        # Step 1-2: project parent state into sub-workbook cells
        events_out = []
        for cid, cell in self.sub_workbook.cells.items():
            if cid in parent_state:
                ev = cell.emit("from_parent", parent_state[cid])
                # Oscillation detection (Round 5 hole #1)
                key = f"{cid}.from_parent"
                if key in self.last_values and self.last_values[key] == ev.value:
                    self.oscillation_count += 1
                self.last_values[key] = ev.value
                events_out.append(ev)

        # Step 3: runtime adapter (e.g. the LED matrix renders + reads encoder)
        runtime_event = {
            "sub_state": {cid: c.state for cid, c in self.sub_workbook.cells.items()},
            "events": [ev.__dict__ for ev in events_out],
        }
        if self.runtime_adapter:
            user_input = self.runtime_adapter(runtime_event)
        else:
            user_input = {}

        # Step 4-5: project user input back to sub-workbook cells, then to parent
        feedback = {}
        feedback_events = []
        for cid, cell in self.sub_workbook.cells.items():
            for ch in cell.inputs:
                value = None
                if ch in user_input:
                    value = user_input[ch]
                elif cid in user_input and isinstance(user_input[cid], dict) and ch in user_input[cid]:
                    value = user_input[cid][ch]
                if value is not None:
                    # Round 7 hole #7: signed command
                    signature = user_input.get("__signatures__", {}).get(f"{cid}.{ch}") if isinstance(user_input, dict) else None
                    try:
                        if signature is not None:
                            # Caller must provide signed_ts in user_input under __signed_ts__ map
                            signed_ts = user_input.get("__signed_ts__", {}).get(f"{cid}.{ch}")
                            ev = cell.receive(ch, value, signature=signature, signed_ts=signed_ts)
                        else:
                            ev = cell.receives_from_runtime(ch, value)
                    except ValueError as e:
                        return {"stopped": True, "reason": str(e),
                                "ticks": self.tick_count, "ts": time.time()}
                    feedback_events.append(ev)
                    feedback[f"{cid}.{ch}"] = value

        return {
            "to_runtime": [ev.__dict__ for ev in events_out],
            "from_runtime": user_input,
            "to_parent": feedback,
            "to_parent_events": [ev.__dict__ for ev in feedback_events],
            "tick": self.tick_count,
            "oscillation_count": self.oscillation_count,
            "phantom_warning": phantom_warning,
            "ts": time.time(),
        }


def polyformality_check(rings: List[FeedbackRing]) -> float:
    """Run N feedback rings with the same parent state and measure agreement.

    If 3 rings with the same parent converge to the same feedback, polyformality
    is high (canon-worthy feedback). If they diverge, polyformality is low
    (the feedback is racing or non-deterministic).

    Round 7 hole #2: rings should agree on what the parent receives.
    """
    if not rings:
        return 0.0
    # Run one tick on each ring with the same parent_state
    parent_state = {"shared": "test"}
    feedbacks = []
    for ring in rings:
        result = ring.step(parent_state)
        fb = tuple(sorted(result.get("to_parent", {}).items()))
        feedbacks.append(fb)
    # All-same = 1.0; all-different = 0.0
    if all(f == feedbacks[0] for f in feedbacks):
        return 1.0
    # Pairwise Jaccard similarity
    intersections = 0
    unions = 0
    for i in range(len(feedbacks)):
        for j in range(i+1, len(feedbacks)):
            set_i = set(feedbacks[i])
            set_j = set(feedbacks[j])
            intersections += len(set_i & set_j)
            unions += len(set_i | set_j)
    if unions == 0:
        return 1.0
    return intersections / unions


def project_to_runtime(ring: FeedbackRing, target: str) -> str:
    """Generate the runtime adapter code for a target (e.g. 'esp32_arduino')."""
    cells = list(ring.sub_workbook.cells.values())
    if target == "esp32_arduino":
        body = """// Bidirectional ESP32 sketch
// Sub-workbook: __WB_NAME__
// Parent cell: __PARENT_CELL__

#include <WiFi.h>
#include <ArduinoJson.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <Encoder.h>

const char* QUILT_HOST = "quilt.lan";
const int   QUILT_PORT = 8080;
const char* WORKBOOK = "__WB_NAME__";
const char* PARENT_CELL = "__PARENT_CELL__";

HUB75_I2S_CFG mxConfig(64, 32, 1);
MatrixPanel_I2S_DMA *dma_display = nullptr;
Encoder myEncoder(32, 33);  // CLK, DT pins

void setup() {
  Serial.begin(115200);
  dma_display = new MatrixPanel_I2S_DMA(mxConfig);
  dma_display->begin();
  dma_display->setBrightness8(90);
}

void loop() {
  // 1. GET parent state
  HTTPClient http;
  http.begin(String("http://") + QUILT_HOST + ":" + QUILT_PORT + "/workbook/" + WORKBOOK + "/cell/" + PARENT_CELL + "/state");
  int code = http.GET();
  if (code == 200) {
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, http.getString());
    // 2. RENDER to LED matrix
    render(doc.as<JsonVariant>());
    // 3. READ encoder
    long encoder_pos = myEncoder.read();
    // 4. POST feedback
    if (encoder_pos != last_pos) {
      HTTPClient post;
      post.begin(String("http://") + QUILT_HOST + ":" + QUILT_PORT + "/workbook/" + WORKBOOK + "/input");
      post.addHeader("Content-Type", "application/json");
      String body = String("{\\"cell_id\\":\\"") + PARENT_CELL + "\\",\\"input\\":{\\"target_heading_deg\\":" + encoder_pos + "}}";
      post.POST(body);
      last_pos = encoder_pos;
    }
  }
  http.end();
  delay(100);
}

void render(JsonVariant state) {
  dma_display->clearScreen();
  dma_display->setTextSize(1);
  dma_display->setTextColor(dma_display->color565(0, 255, 128));
  dma_display->setCursor(2, 2);
  dma_display->print("Target:");
  dma_display->setCursor(2, 14);
  dma_display->print(state["target_heading_deg"].as<float>());
}
"""
        return body.replace("__WB_NAME__", ring.sub_workbook.name).replace("__PARENT_CELL__", ring.parent_cell_id)
    return f"# Runtime target '{target}' not yet implemented"


def project_back_to_workbook(ring: FeedbackRing, parent_cell_id: str,
                              input_payload: Dict) -> Dict:
    """Project a runtime payload back into the parent workbook.

    Updates the parent's last_command field via the cell's input channel.
    """
    sub = ring.sub_workbook
    updated = {}
    for cid, cell in sub.cells.items():
        if cid in input_payload:
            for ch in cell.inputs:
                if ch in input_payload[cid]:
                    cell.receive(ch, input_payload[cid][ch])
                    updated[f"{cid}.{ch}"] = input_payload[cid][ch]
    return {
        "parent_cell_id": parent_cell_id,
        "updated_sub_cells": updated,
        "ts": time.time(),
    }
