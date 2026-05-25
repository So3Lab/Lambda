"""Tests for branch-aware session tree rendering."""

from lambda_coding_agent.tui.screens.session_tree import build_session_tree_rows


def _node(node_id: str, parent_id: str | None, role: str, content: str | None, created_at: str):
    return {
        "id": node_id,
        "parent_id": parent_id,
        "created_at": created_at,
        "message": {"role": role, "content": content},
    }


def test_linear_history_does_not_indent_each_message():
    nodes = {
        "msg_000001": _node("msg_000001", None, "user", "first", "001"),
        "msg_000002": _node("msg_000002", "msg_000001", "assistant", "one", "002"),
        "msg_000003": _node("msg_000003", "msg_000002", "user", "second", "003"),
        "msg_000004": _node("msg_000004", "msg_000003", "assistant", "two", "004"),
    }

    rows = build_session_tree_rows(nodes, "msg_000004")

    assert [row.node_id for row in rows] == [
        "msg_000001",
        "msg_000002",
        "msg_000003",
        "msg_000004",
    ]
    assert [row.indent for row in rows] == [0, 0, 0, 0]


def test_session_tree_rows_style_roles_with_distinct_colors():
    nodes = {
        "msg_000001": _node("msg_000001", None, "user", "first", "001"),
        "msg_000002": _node("msg_000002", "msg_000001", "assistant", "one", "002"),
        "msg_000003": _node("msg_000003", "msg_000002", "tool", "tool result", "003"),
    }

    rows = build_session_tree_rows(nodes, "msg_000003")

    assert "blue" in rows[0].label
    assert "green" in rows[1].label
    assert "yellow" in rows[2].label


def test_indent_increases_only_at_branch_points():
    nodes = {
        "msg_000001": _node("msg_000001", None, "user", "first", "001"),
        "msg_000002": _node("msg_000002", "msg_000001", "assistant", "one", "002"),
        "msg_000003": _node("msg_000003", "msg_000002", "user", "second", "003"),
        "msg_000004": _node("msg_000004", "msg_000003", "assistant", "two", "004"),
        "msg_000005": _node("msg_000005", "msg_000002", "user", "second revised", "005"),
        "msg_000006": _node("msg_000006", "msg_000005", "assistant", "alternate", "006"),
        "msg_000007": _node("msg_000007", "msg_000005", "assistant", "alternate b", "007"),
        "msg_000008": _node("msg_000008", "msg_000007", "user", "follow up", "008"),
    }

    rows = build_session_tree_rows(nodes, "msg_000008")

    assert [(row.node_id, row.indent) for row in rows] == [
        ("msg_000001", 0),
        ("msg_000002", 0),
        ("msg_000003", 1),
        ("msg_000004", 1),
        ("msg_000005", 1),
        ("msg_000006", 2),
        ("msg_000007", 2),
        ("msg_000008", 2),
    ]


def test_empty_tool_call_only_assistant_nodes_are_hidden():
    nodes = {
        "msg_000001": _node("msg_000001", None, "user", "run command", "001"),
        "msg_000002": {
            "id": "msg_000002",
            "parent_id": "msg_000001",
            "created_at": "002",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "execute_code", "arguments": "{}"},
                    }
                ],
            },
        },
        "msg_000003": _node("msg_000003", "msg_000002", "tool", "tool result", "003"),
        "msg_000004": _node("msg_000004", "msg_000003", "user", "next", "004"),
    }

    rows = build_session_tree_rows(nodes, "msg_000004")

    assert [row.node_id for row in rows] == [
        "msg_000001",
        "msg_000003",
        "msg_000004",
    ]
    assert "assistant:" not in " ".join(row.label for row in rows)
    assert "[tool calls]" not in " ".join(row.label for row in rows)


def test_none_content_assistant_nodes_are_hidden():
    nodes = {
        "msg_000001": _node("msg_000001", None, "user", "run command", "001"),
        "msg_000002": _node("msg_000002", "msg_000001", "assistant", None, "002"),
    }

    rows = build_session_tree_rows(nodes, "msg_000002")

    assert [row.node_id for row in rows] == ["msg_000001"]
