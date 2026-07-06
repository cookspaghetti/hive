"""End-to-end lifecycle test with fake nodes (fyp.txt S8).

Proves the state machine wires up and runs IDLE -> ... -> SEALED before any
real layer is implemented. This is the backbone contract.
"""

from hive.orchestrator import build_graph, new_session
from hive.state import Phase


def test_graph_runs_to_sealed(stranger_msg):
    app = build_graph(max_turns=1)
    session = new_session(peer_id=42, persona="confused_elderly")
    result = app.invoke({"session": session, "inbound": stranger_msg})
    assert result["session"].phase is Phase.SEALED
    assert result["session"].turn_count >= 1


def test_stop_request_short_circuits(stranger_msg):
    app = build_graph(max_turns=999)
    session = new_session(peer_id=7, persona="naive_young_adult")
    result = app.invoke(
        {"session": session, "inbound": stranger_msg, "stop_requested": True}
    )
    assert result["session"].phase is Phase.SEALED
