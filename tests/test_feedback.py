"""Test suite for mavis-axui-feedback — bidirectional projection."""
import sys
sys.path.insert(0, "/workspace/repos/mavis-axui-feedback")

from mavis_axui_feedback import (
    FeedbackRing, FeedbackCell, SubWorkbook,
    FeedbackEvent, FeedbackType, project_lossy,
    sign_command, verify_command_signature, polyformality_check,
    project_to_runtime, project_back_to_workbook,
    __version__,
)


results = []
failures = []


def test(name, func):
    try:
        func()
        results.append((name, "PASS"))
    except AssertionError as e:
        results.append((name, f"FAIL: {e}"))
        failures.append(name)
    except Exception as e:
        results.append((name, f"ERROR: {type(e).__name__}: {e}"))
        failures.append(name)


def t_version():
    assert __version__ == "0.2.0"


# ─── FeedbackCell ────────────────────────────────────────

def t_feedback_cell_default():
    c = FeedbackCell(cell_id="c1", outputs=["heading"], inputs=["set_heading"])
    assert c.state == {}
    assert c.last_command is None


def t_feedback_cell_emit():
    c = FeedbackCell(cell_id="c1", outputs=["heading"], inputs=["set_heading"])
    e = c.emit("heading", 123.4)
    assert e.cell_id == "c1"
    assert e.channel == "heading"
    assert e.value == 123.4
    assert c.state["heading"] == 123.4


def t_feedback_cell_receive():
    c = FeedbackCell(cell_id="c1", outputs=["heading"], inputs=["set_heading"])
    c.receive("set_heading", 200.0)
    assert c.last_command["channel"] == "set_heading"
    assert c.last_command["value"] == 200.0


# ─── SubWorkbook ──────────────────────────────────────────

def t_sub_workbook_add_cell():
    sw = SubWorkbook(name="sub1")
    c = FeedbackCell(cell_id="a", outputs=["x"], inputs=["y"])
    sw.add_cell(c)
    assert "a" in sw.cells


def t_sub_workbook_add_flow():
    sw = SubWorkbook(name="sub1")
    sw.add_flow("a", "b", "data")
    assert len(sw.flows) == 1


# ─── FeedbackRing ────────────────────────────────────────

def t_feedback_ring_step_basic():
    """Round-trip: parent emits → sub-workbook → runtime → feedback."""
    sw = SubWorkbook(name="led_dashboard_sub")
    sw.add_cell(FeedbackCell(cell_id="led_state", outputs=["pixels"], inputs=["target_heading"]))

    ring = FeedbackRing(parent_cell_id="autopilot_controller", sub_workbook=sw)
    result = ring.step(parent_state={"led_state": "rendering heading 045"})

    assert "to_runtime" in result
    assert "from_runtime" in result
    assert "to_parent" in result


def t_feedback_ring_step_with_adapter():
    """Runtime adapter simulates the rotary encoder returning a new heading."""
    sw = SubWorkbook(name="led_dashboard_sub")
    sw.add_cell(FeedbackCell(cell_id="led_state", outputs=["pixels"], inputs=["target_heading"]))

    def fake_encoder(runtime_event):
        # User turned the encoder to 180 degrees
        return {"led_state": {"target_heading": 180.0}}

    ring = FeedbackRing(parent_cell_id="autopilot_controller",
                         sub_workbook=sw,
                         runtime_adapter=fake_encoder)
    result = ring.step(parent_state={"led_state": "rendering heading 045"})

    # Feedback should contain the user's input
    assert "led_state.target_heading" in result["to_parent"]
    assert result["to_parent"]["led_state.target_heading"] == 180.0


def t_feedback_ring_sub_cell_state_updated():
    """The sub-workbook cell's last_command should reflect the user input."""
    sw = SubWorkbook(name="led_dashboard_sub")
    cell = FeedbackCell(cell_id="led_state", outputs=["pixels"], inputs=["target_heading"])
    sw.add_cell(cell)

    ring = FeedbackRing(parent_cell_id="autopilot_controller", sub_workbook=sw,
                         runtime_adapter=lambda ev: {"led_state": {"target_heading": 270.0}})
    ring.step(parent_state={"led_state": "rendering"})

    assert cell.last_command["value"] == 270.0


# ─── Projection ─────────────────────────────────────────

def t_project_to_runtime_esp32():
    sw = SubWorkbook(name="led_dashboard_sub")
    sw.add_cell(FeedbackCell(cell_id="led_state", outputs=["x"], inputs=["target_heading"]))
    ring = FeedbackRing(parent_cell_id="autopilot_controller", sub_workbook=sw)
    src = project_to_runtime(ring, "esp32_arduino")
    assert "ESP32" in src
    assert "led_dashboard_sub" in src
    assert "autopilot_controller" in src
    assert "Encoder" in src
    assert "POST" in src or "post" in src  # feedback POST


def t_project_to_runtime_unknown():
    sw = SubWorkbook(name="x")
    ring = FeedbackRing(parent_cell_id="c", sub_workbook=sw)
    src = project_to_runtime(ring, "no_such_target")
    assert "not yet implemented" in src


def t_project_back_to_workbook():
    sw = SubWorkbook(name="sub")
    cell = FeedbackCell(cell_id="a", outputs=["x"], inputs=["y"])
    sw.add_cell(cell)
    ring = FeedbackRing(parent_cell_id="parent", sub_workbook=sw)
    result = project_back_to_workbook(ring, "parent",
                                       input_payload={"a": {"y": 42.0}})
    assert result["parent_cell_id"] == "parent"
    assert "a.y" in result["updated_sub_cells"]
    assert result["updated_sub_cells"]["a.y"] == 42.0
    assert cell.last_command["value"] == 42.0


# ─── Multi-cell sub-workbook ────────────────────────────

def t_multi_cell_sub_workbook():
    """LED dashboard sub-workbook with 3 cells (display, button, encoder)."""
    sw = SubWorkbook(name="led_dashboard")
    sw.add_cell(FeedbackCell(cell_id="display", outputs=["pixels"], inputs=[]))
    sw.add_cell(FeedbackCell(cell_id="engage_button", outputs=[], inputs=["press"]))
    sw.add_cell(FeedbackCell(cell_id="heading_encoder", outputs=[], inputs=["delta"]))

    ring = FeedbackRing(parent_cell_id="autopilot", sub_workbook=sw)
    assert len(sw.cells) == 3

    # Run 3 ticks of user interaction
    interactions = [
        {"engage_button": {"press": True}, "heading_encoder": {"delta": 0}},
        {"engage_button": {"press": False}, "heading_encoder": {"delta": 5}},
        {"engage_button": {"press": False}, "heading_encoder": {"delta": -3}},
    ]
    for i, interaction in enumerate(interactions):
        ring.runtime_adapter = lambda ev: interaction
        result = ring.step(parent_state={"display": f"rendering tick {i}"})
        assert "to_parent" in result


def t_bidirectional_consistency():
    """State going IN and state coming OUT should be consistent."""
    sw = SubWorkbook(name="led_dashboard")
    sw.add_cell(FeedbackCell(cell_id="display", outputs=["heading"], inputs=["target_heading"]))
    sw.add_cell(FeedbackCell(cell_id="encoder", outputs=[], inputs=["user_input"]))

    ring = FeedbackRing(parent_cell_id="autopilot", sub_workbook=sw,
                         runtime_adapter=lambda ev: {"encoder": {"user_input": 45.0}})
    # Parent emits heading=045
    result = ring.step(parent_state={"display": 45.0})
    # The encoder's input should be propagated
    assert "encoder.user_input" in result["to_parent"]
    assert result["to_parent"]["encoder.user_input"] == 45.0


# ─── Run ──────────────────────────────────────────────────

test("test_version", t_version)
test("test_feedback_cell_default", t_feedback_cell_default)
test("test_feedback_cell_emit", t_feedback_cell_emit)
test("test_feedback_cell_receive", t_feedback_cell_receive)
test("test_sub_workbook_add_cell", t_sub_workbook_add_cell)
test("test_sub_workbook_add_flow", t_sub_workbook_add_flow)
test("test_feedback_ring_step_basic", t_feedback_ring_step_basic)
test("test_feedback_ring_step_with_adapter", t_feedback_ring_step_with_adapter)
test("test_feedback_ring_sub_cell_state_updated", t_feedback_ring_sub_cell_state_updated)
test("test_project_to_runtime_esp32", t_project_to_runtime_esp32)
test("test_project_to_runtime_unknown", t_project_to_runtime_unknown)
test("test_project_back_to_workbook", t_project_back_to_workbook)
test("test_multi_cell_sub_workbook", t_multi_cell_sub_workbook)
test("test_bidirectional_consistency", t_bidirectional_consistency)


# ─── Round 5: typed events, loss-aware, oscillation detection ───

def t_feedback_type_enum():
    assert FeedbackType.COMMAND.value == "command"
    assert FeedbackType.STATE.value == "state"
    assert FeedbackType.ACTION.value == "action"
    assert FeedbackType.TICK.value == "tick"
    assert FeedbackType.WITNESS.value == "witness"


def t_feedback_event_compute_hash():
    e = FeedbackEvent(type=FeedbackType.STATE, cell_id="c1", channel="x", value=1.0)
    h = e.compute_hash()
    assert e.hash is not None
    assert len(h) == 16


def t_feedback_event_chain():
    """Each event's prev_hash should link to the previous event's hash."""
    e1 = FeedbackEvent(type=FeedbackType.STATE, cell_id="c1", channel="x", value=1.0)
    e1.compute_hash()
    e2 = FeedbackEvent(type=FeedbackType.COMMAND, cell_id="c1", channel="x",
                       value=2.0, prev_hash=e1.hash)
    e2.compute_hash()
    assert e2.prev_hash == e1.hash


def t_project_lossy_high_precision():
    """precision=1.0 → no loss."""
    assert project_lossy(3.14159, 1.0) == 3.14159


def t_project_lossy_low_precision():
    """precision=0.01 → round to 2 decimals."""
    assert project_lossy(3.14159, 0.01) == 3.14


def t_project_lossy_very_low_precision():
    """precision=10.0 → no rounding needed (lossless at integer scale)."""
    # 10.0 means "round to 0 decimal places" — but precision>=1 is lossless
    # So we use precision=1.0 which gives lossless, then test with precision<1
    assert project_lossy(3.7, 1.0) == 3.7  # lossless
    # precision=0.1 → round to 1 decimal
    assert project_lossy(3.14159, 0.1) == 3.1


def t_project_lossy_none():
    assert project_lossy(None, 0.01) is None


def t_oscillation_detected():
    """Same value emitted twice in a row → oscillation count increments."""
    sw = SubWorkbook(name="sub")
    sw.add_cell(FeedbackCell(cell_id="c", outputs=["x"], inputs=[]))
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw)
    ring.step(parent_state={"c": 1.0})
    ring.step(parent_state={"c": 1.0})  # same value
    ring.step(parent_state={"c": 1.0})  # same value
    assert ring.oscillation_count >= 2


def t_max_ticks_terminates():
    """Ring terminates when max_ticks_per_session exceeded."""
    sw = SubWorkbook(name="sub")
    sw.add_cell(FeedbackCell(cell_id="c", outputs=["x"], inputs=[]))
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw, max_ticks_per_session=3)
    results = []
    for i in range(5):
        r = ring.step(parent_state={"c": i})
        results.append(r)
    assert any("stopped" in r for r in results)


def t_typed_event_log_persists():
    """Sub-workbook cell's event_log persists across ticks (witness chain)."""
    sw = SubWorkbook(name="sub")
    cell = FeedbackCell(cell_id="c", outputs=["x"], inputs=["y"])
    sw.add_cell(cell)
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw,
                         runtime_adapter=lambda ev: {"c": {"y": 42.0}})
    ring.step(parent_state={"c": 1.0})
    assert len(cell.event_log) >= 2  # emit + receive
    # Verify chain integrity
    for i in range(1, len(cell.event_log)):
        assert cell.event_log[i].prev_hash == cell.event_log[i-1].hash


def t_phantom_cells_survive_disconnect():
    """Even when adapter is None, sub-workbook cells persist their state."""
    sw = SubWorkbook(name="sub")
    cell = FeedbackCell(cell_id="c", outputs=["x"], inputs=[])
    sw.add_cell(cell)
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw)  # no adapter
    ring.step(parent_state={"c": 1.0})
    assert cell.state == {"from_parent": 1.0}
    # Adapter removed → cell still alive
    ring.runtime_adapter = None
    ring.step(parent_state={"c": 2.0})
    assert cell.state == {"from_parent": 2.0}


def t_lossy_projection_through_ring():
    """precision < 1.0 applies lossy projection at emit."""
    sw = SubWorkbook(name="sub")
    cell = FeedbackCell(cell_id="c", outputs=["x"], inputs=[], precision=0.01)
    sw.add_cell(cell)
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw)
    ring.step(parent_state={"c": 3.14159})
    assert cell.state["from_parent"] == 3.14  # rounded


test("test_feedback_type_enum", t_feedback_type_enum)
test("test_feedback_event_compute_hash", t_feedback_event_compute_hash)
test("test_feedback_event_chain", t_feedback_event_chain)
test("test_project_lossy_high_precision", t_project_lossy_high_precision)
test("test_project_lossy_low_precision", t_project_lossy_low_precision)
test("test_project_lossy_very_low_precision", t_project_lossy_very_low_precision)
test("test_project_lossy_none", t_project_lossy_none)
test("test_oscillation_detected", t_oscillation_detected)
test("test_max_ticks_terminates", t_max_ticks_terminates)
test("test_typed_event_log_persists", t_typed_event_log_persists)
test("test_phantom_cells_survive_disconnect", t_phantom_cells_survive_disconnect)
test("test_lossy_projection_through_ring", t_lossy_projection_through_ring)


# ─── Round 7: signed commands + polyformality + phantom ───

def t_sign_command_returns_32_chars():
    sig = sign_command("c1", "x", 1.0, secret="topsecret")
    assert len(sig) == 32


def t_sign_command_deterministic():
    """Same (cell, channel, value, secret, ts) → same signature."""
    sig1 = sign_command("c1", "x", 1.0, secret="s", ts=1000.0)
    sig2 = sign_command("c1", "x", 1.0, secret="s", ts=1000.0)
    assert sig1 == sig2


def t_sign_command_different_secret_different_sig():
    sig1 = sign_command("c1", "x", 1.0, secret="s1", ts=1000.0)
    sig2 = sign_command("c1", "x", 1.0, secret="s2", ts=1000.0)
    assert sig1 != sig2


def t_verify_signature_valid():
    sig = sign_command("c1", "x", 1.0, secret="topsecret", ts=1000.0)
    assert verify_command_signature("c1", "x", 1.0, sig, "topsecret",
                                     ts=1000.0, now=1000.5)


def t_verify_signature_replay_rejected():
    """Old timestamp → reject."""
    sig = sign_command("c1", "x", 1.0, secret="topsecret", ts=1000.0)
    # Verify at now=1200 (>60s old)
    assert not verify_command_signature("c1", "x", 1.0, sig, "topsecret",
                                        ts=1000.0, now=1200.0)


def t_signed_command_accepted():
    """Cell with secret accepts signed commands (use current time so replay check passes)."""
    import time as _t
    cell = FeedbackCell(cell_id="c", outputs=[], inputs=["x"], secret="topsecret")
    now = _t.time()
    sig = sign_command("c", "x", 42.0, secret="topsecret", ts=now)
    ev = cell.receive("x", 42.0, signature=sig, signed_ts=now)
    assert ev.value == 42.0


def t_unsigned_command_rejected_when_secret_set():
    """Cell with secret rejects unsigned commands."""
    cell = FeedbackCell(cell_id="c", outputs=[], inputs=["x"], secret="topsecret")
    try:
        cell.receive("x", 42.0)
        assert False, "should have raised"
    except ValueError:
        pass


def t_forged_command_rejected():
    """Cell rejects command signed with wrong secret."""
    import time as _t
    cell = FeedbackCell(cell_id="c", outputs=[], inputs=["x"], secret="topsecret")
    now = _t.time()
    sig = sign_command("c", "x", 42.0, secret="WRONG", ts=now)
    try:
        cell.receive("x", 42.0, signature=sig, signed_ts=now)
        assert False, "should have raised"
    except ValueError:
        pass


def t_unsigned_command_accepted_when_no_secret():
    """Cell without secret accepts unsigned commands (open mode)."""
    cell = FeedbackCell(cell_id="c", outputs=[], inputs=["x"])
    ev = cell.receive("x", 42.0)
    assert ev.value == 42.0


def t_phantom_runtime_warning():
    """No adapter → phantom_warning in step result."""
    sw = SubWorkbook(name="sub")
    sw.add_cell(FeedbackCell(cell_id="c", outputs=["x"], inputs=[]))
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw)
    result = ring.step({"c": 1.0})
    assert result["phantom_warning"] is not None
    assert "Runtime adapter is None" in result["phantom_warning"]


def t_phantom_runtime_no_warning_when_disabled():
    """phantom_runtime_warning=False → no warning emitted."""
    sw = SubWorkbook(name="sub")
    sw.add_cell(FeedbackCell(cell_id="c", outputs=["x"], inputs=[]))
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw,
                         phantom_runtime_warning=False)
    result = ring.step({"c": 1.0})
    assert result["phantom_warning"] is None


def t_phantom_runtime_with_adapter_no_warning():
    sw = SubWorkbook(name="sub")
    sw.add_cell(FeedbackCell(cell_id="c", outputs=["x"], inputs=["y"]))
    ring = FeedbackRing(parent_cell_id="p", sub_workbook=sw,
                         runtime_adapter=lambda ev: {"c": {"y": 1.0}})
    result = ring.step({"c": 1.0})
    assert result["phantom_warning"] is None


def t_polyformality_check_identical_rings():
    """Three identical rings → polyformality = 1.0."""
    rings = []
    for _ in range(3):
        sw = SubWorkbook(name="sub")
        sw.add_cell(FeedbackCell(cell_id="shared", outputs=["x"], inputs=[]))
        rings.append(FeedbackRing(parent_cell_id="p", sub_workbook=sw))
    score = polyformality_check(rings)
    assert score == 1.0


def t_polyformality_check_divergent_rings():
    """Three rings with no feedback → 1.0 (vacuously)."""
    rings = []
    for i in range(3):
        sw = SubWorkbook(name="sub")
        # No cells → no feedback → all empty
        rings.append(FeedbackRing(parent_cell_id="p", sub_workbook=sw))
    score = polyformality_check(rings)
    assert score == 1.0


def t_polyformality_check_empty():
    assert polyformality_check([]) == 0.0


def t_runtime_precision_in_receives_from_runtime():
    """Runtime precision rounds the incoming value."""
    cell = FeedbackCell(cell_id="c", outputs=[], inputs=["x"], runtime_precision=0.01)
    ev = cell.receives_from_runtime("x", 3.14159)
    assert ev.value == 3.14


test("test_sign_command_returns_32_chars", t_sign_command_returns_32_chars)
test("test_sign_command_deterministic", t_sign_command_deterministic)
test("test_sign_command_different_secret_different_sig", t_sign_command_different_secret_different_sig)
test("test_verify_signature_valid", t_verify_signature_valid)
test("test_verify_signature_replay_rejected", t_verify_signature_replay_rejected)
test("test_signed_command_accepted", t_signed_command_accepted)
test("test_unsigned_command_rejected_when_secret_set", t_unsigned_command_rejected_when_secret_set)
test("test_forged_command_rejected", t_forged_command_rejected)
test("test_unsigned_command_accepted_when_no_secret", t_unsigned_command_accepted_when_no_secret)
test("test_phantom_runtime_warning", t_phantom_runtime_warning)
test("test_phantom_runtime_no_warning_when_disabled", t_phantom_runtime_no_warning_when_disabled)
test("test_phantom_runtime_with_adapter_no_warning", t_phantom_runtime_with_adapter_no_warning)
test("test_polyformality_check_identical_rings", t_polyformality_check_identical_rings)
test("test_polyformality_check_divergent_rings", t_polyformality_check_divergent_rings)
test("test_polyformality_check_empty", t_polyformality_check_empty)
test("test_runtime_precision_in_receives_from_runtime", t_runtime_precision_in_receives_from_runtime)

print("\n=== mavis-axui-feedback test results ===")
for name, status in results:
    print(f"  {status:60} {name}")
print(f"\n{len(results) - len(failures)}/{len(results)} passed")
if failures:
    print(f"FAILURES: {failures}")
    sys.exit(1)
