"""Integration test for TUI mount fix."""

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, OptionList

from textual import events

from lambda_coding_agent.tui.app import LambdaCodingTUIApp
from lambda_coding_agent.tui.chat_input import ChatInput
from lambda_coding_agent.tui.screens.session_tree import SessionTreeScreen


async def stub_agent(message, history=None, **kwargs):
    """Stub agent that yields nothing."""
    return
    yield  # make it a generator


@pytest.mark.asyncio
async def test_user_message_mounts():
    """Verify that typing a message and submitting doesn't cause MountError."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        # Type a message and press enter
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        # If we get here without MountError, the fix works
        await pilot.pause()


@pytest.mark.asyncio
async def test_lambda_logo_shows_until_first_message():
    """A fresh empty TUI should show the Lambda splash until the user starts chatting."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        logos = list(app.query("#lambda-logo"))
        assert len(logos) == 1
        assert app._logo_visible is True

        await pilot.press("h", "i", "enter")
        await pilot.pause()

        assert list(app.query("#lambda-logo")) == []
        assert app._logo_visible is False


@pytest.mark.asyncio
async def test_lambda_logo_hides_after_loading_session(tmp_path):
    """Selecting an existing saved session should hide the initial Lambda splash."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )
    session_id = app.session_manager.start_new_session()
    app.session_manager.save_session(
        session_id,
        history=[{"role": "user", "content": "saved"}],
        model_name="test",
        name="saved session",
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        assert list(app.query("#lambda-logo"))

        await app._load_session(session_id)
        await pilot.pause()

        assert list(app.query("#lambda-logo")) == []
        assert app.history == [{"role": "user", "content": "saved"}]


@pytest.mark.asyncio
async def test_slash_exit_command():
    """Verify Exit via command palette works."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("e", "x", "i", "t")
        await pilot.press("enter")
        # App should exit


@pytest.mark.asyncio
async def test_slash_clear_command():
    """Verify Clear Chat via command palette works."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        # First add a message
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        # Open command palette and run Clear Chat
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("c", "l", "e", "a", "r")
        await pilot.press("enter")
        await pilot.pause()
        # Chat log should be empty now
        chat_log = app.query_one("#main-chat-log", VerticalScroll)
        assert len(chat_log.children) == 0


@pytest.mark.asyncio
async def test_ctrl_c_clears_input_without_exiting():
    """Verify Ctrl+C clears the input and keeps the TUI running."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        input_widget.select_all()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert input_widget.value == "hello"
        assert app.clipboard == "hello"
        assert app.is_running

        input_widget.selection = input_widget.selection.cursor(input_widget.cursor_location)
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert input_widget.value == ""
        assert app.is_running


@pytest.mark.asyncio
async def test_shift_enter_adds_newline_and_enter_submits():
    """Shift+Enter should create a newline; Enter should submit the full text."""
    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("shift+enter")
        await pilot.press("t", "h", "e", "r", "e")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == "hi\nthere"
        assert input_widget.content_size.height >= 2
        assert "there" in input_widget.render_line(1).text
        assert calls == []

        await pilot.press("enter")
        await pilot.pause()

        assert calls == ["hi\nthere"]
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_load_session_scrolls_chat_log_to_bottom(tmp_path):
    """Loading a saved session should show the latest rendered messages."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )
    session_id = app.session_manager.start_new_session()
    history = [
        {"role": "user", "content": f"message {i} " + ("x " * 80)}
        for i in range(50)
    ]
    app.session_manager.save_session(
        session_id,
        history=history,
        model_name="test",
        name="long session",
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await app._load_session(session_id)
        chat_log = app.query_one("#main-chat-log", VerticalScroll)

        await pilot.pause()

        assert chat_log.scroll_y == chat_log.max_scroll_y


@pytest.mark.asyncio
async def test_rebuild_chat_initially_renders_only_recent_messages(tmp_path):
    """Large histories should initially render only the recent 8 messages."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": f"message {i}"}
            for i in range(30)
        ]
        await app._rebuild_chat_from_history()
        await pilot.pause()

        chat_log = app.query_one("#main-chat-log", VerticalScroll)
        assert len(chat_log.children) == 8
        assert app._render_window_start == 22
        assert app._render_window_end == 30
        assert "message 22" in str(chat_log.children[0].children[1].content)


@pytest.mark.asyncio
async def test_scroll_up_expands_but_caps_render_window(tmp_path):
    """Scrolling up should render older context, but never more than 20 messages."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": f"message {i}"}
            for i in range(50)
        ]
        await app._rebuild_chat_from_history()
        chat_log = app.query_one("#main-chat-log", VerticalScroll)
        chat_log.scroll_up()
        await pilot.pause()

        assert len(chat_log.children) <= 20
        assert app._render_window_end == 50
        assert app._render_window_start == 30
        assert "message 30" in str(chat_log.children[0].children[1].content)


@pytest.mark.asyncio
async def test_scroll_up_during_stream_keeps_inflight_response(tmp_path):
    """Lazy history re-rendering must not remove the currently streaming turn."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": f"message {i}"}
            for i in range(30)
        ]
        await app._rebuild_chat_from_history()
        await app._append_user_message("current question")
        await app.start_model_response("streaming-call")
        await app.append_model_content("streaming-call", "partial answer")
        app._flush_model("streaming-call")

        chat_log = app.query_one("#main-chat-log", VerticalScroll)
        chat_log.scroll_up()
        await pilot.pause()

        assert len(chat_log.children) <= 20
        assert app._render_window_end == len(app.history) == 32
        assert app.history[-2:] == [
            {"role": "user", "content": "current question"},
            {"role": "assistant", "content": "partial answer"},
        ]


@pytest.mark.asyncio
async def test_at_path_autocomplete_inserts_selected_path(tmp_path):
    """Typing @ should show matching workspace files and Enter should insert one."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")

    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("@", "s")
        await pilot.pause()

        from textual.widgets import OptionList

        popup = app.query_one("#path-autocomplete", OptionList)
        assert popup.display is True
        assert [option.prompt for option in popup.options] == ["src/main.py"]

        await pilot.press("enter")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == "@src/main.py"
        assert popup.display is False
        assert calls == []


def test_at_path_autocomplete_matches_nested_and_partial_paths(tmp_path, monkeypatch):
    """Workspace path matching should find nested paths from the configured workspace."""
    workspace = tmp_path / "workspace"
    launch_dir = tmp_path / "launch-dir"
    (workspace / "src" / "ui" / "components").mkdir(parents=True)
    launch_dir.mkdir()
    (workspace / "src" / "ui" / "components" / "Button.py").write_text("")
    (launch_dir / "Button.py").write_text("")
    monkeypatch.chdir(launch_dir)

    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(workspace),
        model_name="test",
    )

    expected = ["src/ui/components/Button.py"]
    assert app._matching_workspace_paths("Button") == expected
    assert app._matching_workspace_paths("utt") == expected
    assert app._matching_workspace_paths("ui/utt") == expected


def test_at_path_autocomplete_resolves_relative_workspace_from_launch_dir(tmp_path, monkeypatch):
    """Relative workspace paths should resolve once, then search that workspace."""
    launch_dir = tmp_path / "launch-dir"
    workspace = launch_dir / "repo"
    workspace.mkdir(parents=True)
    (workspace / "target.py").write_text("")
    (tmp_path / "target.py").write_text("")
    monkeypatch.chdir(launch_dir)

    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="repo",
        model_name="test",
    )

    assert app.workspace == str(workspace)
    assert app._matching_workspace_paths("target") == ["target.py"]


@pytest.mark.asyncio
async def test_click_focuses_input_unless_modal_is_active():
    """Background clicks focus the chat input, but not while a modal screen is active."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        app.screen.set_focus(None)
        await pilot.pause()
        assert not input_widget.has_focus

        await pilot.click("#main-chat-log")
        await pilot.pause()
        assert input_widget.has_focus

        await pilot.press("ctrl+p")
        await pilot.pause()
        active_screen = app.screen
        assert app._modal_screen_active()
        input_widget.blur()
        await pilot.pause()
        app.on_click(None)
        await pilot.pause()

        assert app.screen is active_screen
        assert not input_widget.has_focus



@pytest.mark.asyncio
async def test_empty_initial_session_not_saved_on_exit(tmp_path):
    """Opening and closing the TUI without sending a message should not persist a session."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.pause()

    assert app.session_manager.list_sessions() == []


@pytest.mark.asyncio
async def test_empty_placeholder_session_not_saved_when_starting_new_session(tmp_path):
    """Creating another new session should not save the untouched placeholder."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        initial_session_id = app._current_session_id
        await app._start_new_session()
        await pilot.pause()

    assert initial_session_id is not None
    assert app.session_manager.list_sessions() == []


@pytest.mark.asyncio
async def test_do_auto_save_after_rewind_updates_existing_session_in_place(tmp_path):
    """Saving immediately after rewind should not rewrite the branch as a new trunk."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        session_id = app._current_session_id
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()
        original_node_count = len(app._message_nodes)

        await app._rewind_and_fork(2, "second revised")
        await app._do_auto_save()
        await pilot.pause()

    sessions = app.session_manager.list_sessions()
    assert [session["id"] for session in sessions] == [session_id]
    data = app.session_manager.load_session(session_id)
    assert len(data["message_nodes"]) == original_node_count
    assert data["history"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]


@pytest.mark.asyncio
async def test_rewind_creates_branch_inside_current_session(tmp_path):
    """Rewind should keep one session file and branch within its message tree."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        session_id = app._current_session_id
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await app._rewind_and_fork(2, "second revised")
        assert app._current_session_id == session_id
        assert app.history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
        ]

        app.history = app.history + [
            {"role": "user", "content": "second revised"},
            {"role": "assistant", "content": "alternate"},
        ]
        await app._do_auto_save()
        await pilot.pause()

    sessions = app.session_manager.list_sessions()
    assert [session["id"] for session in sessions] == [session_id]

    data = app.session_manager.load_session(session_id)
    assert data["history"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second revised"},
        {"role": "assistant", "content": "alternate"},
    ]
    assert len(data["message_nodes"]) == 6


@pytest.mark.asyncio
async def test_double_escape_opens_session_tree_screen(tmp_path):
    """Double Esc should open the branch-aware session tree instead of interrupting."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await pilot.press("escape")
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, SessionTreeScreen)


@pytest.mark.asyncio
async def test_session_tree_search_highlights_matches_while_typing(tmp_path):
    """Typing in session-tree search should immediately jump to the first match."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await app._open_rewind_selector()
        await pilot.pause()
        option_list = app.screen.query_one("#session-tree-widget", OptionList)
        assert option_list.highlighted == 3

        await pilot.press("/")
        await pilot.press("f", "i", "r", "s", "t")
        await pilot.pause()

        search = app.screen.query_one("#session-tree-search", Input)
        assert search.value == "first"
        assert option_list.highlighted == 0


@pytest.mark.asyncio
async def test_rewind_command_opens_session_tree(tmp_path):
    """The Rewind command should use the same tree UI as double Esc."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await app._open_rewind_selector()
        await pilot.pause()

        assert isinstance(app.screen, SessionTreeScreen)


@pytest.mark.asyncio
async def test_session_tree_switches_to_existing_branch(tmp_path):
    """Selecting a leaf in the tree should project that branch into the TUI."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()
        await app._rewind_and_fork(2, "second revised")
        app.history = app.history + [
            {"role": "user", "content": "second revised"},
            {"role": "assistant", "content": "alternate"},
        ]
        await app._do_auto_save()

        original_leaf = next(
            node_id
            for node_id, node in app._message_nodes.items()
            if node["message"] == {"role": "assistant", "content": "two"}
        )
        await app._switch_branch(original_leaf)
        await pilot.pause()

        assert app._active_leaf_id == original_leaf
        assert app.history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]


@pytest.mark.asyncio
async def test_session_tree_rewind_prefills_selected_user_message(tmp_path):
    """Tree rewind should move active_leaf before the selected user and prefill input."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        user_node_id = next(
            node_id
            for node_id, node in app._message_nodes.items()
            if node["message"] == {"role": "user", "content": "second"}
        )
        await app._rewind_to_node(user_node_id)
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert app.history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
        ]
        assert app._active_leaf_id is not None
        assert app._message_nodes[app._active_leaf_id]["message"] == {
            "role": "assistant",
            "content": "one",
        }
        assert input_widget.value == "second"


@pytest.mark.asyncio
async def test_enter_on_assistant_node_does_not_rewind_or_switch(tmp_path):
    """Rewind tree only allows rewinding between user messages."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        original_history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app.history = list(original_history)
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await app._open_rewind_selector()
        await pilot.pause()
        option_list = app.screen.query_one("#session-tree-widget", OptionList)
        option_list.highlighted = 1
        await pilot.press("enter")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert isinstance(app.screen, SessionTreeScreen)
        assert app.history == original_history
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_enter_on_user_node_rewinds_and_prefills_without_showing_user(tmp_path):
    """In rewind/tree UI, selecting a user node means edit-and-resend that message."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        app.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ]
        app._current_session_has_user_message = True
        await app._do_auto_save()

        await app._open_rewind_selector()
        await pilot.pause()
        option_list = app.screen.query_one("#session-tree-widget", OptionList)
        option_list.highlighted = 2
        await pilot.press("enter")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert app.history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
        ]
        assert input_widget.value == "second"


@pytest.mark.asyncio
async def test_multiline_paste_under_limit_preserves_newlines_on_send():
    """Pasted multiline text under the compact threshold should send unchanged."""
    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        pasted = "one\ntwo\nthree\nfour\nfive"
        await input_widget._on_paste(events.Paste(pasted))
        await pilot.pause()

        assert input_widget.value == pasted

        await pilot.press("enter")
        await pilot.pause()

        assert calls == [pasted]
        assert app.history[-1] == {"role": "user", "content": pasted}


@pytest.mark.asyncio
async def test_large_multiline_paste_displays_placeholder_but_sends_original():
    """Pastes over five lines should render a compact placeholder and submit the full paste."""
    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace="/tmp",
        model_name="test",
    )
    pasted = "alpha12345\nbeta\ngamma\ndelta\nepsilon\nzeta"

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        app.post_message(events.Paste(pasted))
        await pilot.pause()

        assert input_widget.value == "[Pasted 6 lines: alpha12345...]"
        assert input_widget.submit_value == pasted

        await pilot.press("enter")
        await pilot.pause()

        assert calls == [pasted]
        assert app.history[-1] == {"role": "user", "content": pasted}


@pytest.mark.asyncio
async def test_multiple_large_multiline_pastes_are_expanded_independently():
    """Multiple compact pasted blocks should each expand back to their original content."""
    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace="/tmp",
        model_name="test",
    )
    first = "first paste\n2\n3\n4\n5\n6"
    second = "second data\nb\nc\nd\ne\nf"

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(first))
        input_widget.cursor_position = len(input_widget.value)
        input_widget.replace(" between ", input_widget.cursor_position, input_widget.cursor_position)
        await input_widget._on_paste(events.Paste(second))
        await pilot.pause()

        assert input_widget.value == (
            "[Pasted 6 lines: first past...] between "
            "[Pasted 6 lines: second dat...]"
        )

        await pilot.press("enter")
        await pilot.pause()

        assert calls == [f"{first} between {second}"]


@pytest.mark.asyncio
async def test_file_paste_inserts_at_prefixed_absolute_path(tmp_path):
    """Pasting a file path should insert it as an @ absolute path token."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )
    pasted_file = tmp_path / "pasted file.txt"
    pasted_file.write_text("file contents")

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(str(pasted_file)))
        await pilot.pause()

        assert input_widget.value == f"@{pasted_file.resolve()}"
        assert input_widget.submit_value == f"@{pasted_file.resolve()}"


@pytest.mark.asyncio
async def test_file_uri_paste_inserts_at_prefixed_absolute_path(tmp_path):
    """File URI pastes from OS file managers should resolve to @ absolute paths."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )
    pasted_file = tmp_path / "pasted.txt"
    pasted_file.write_text("file contents")

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(pasted_file.as_uri()))
        await pilot.pause()

        assert input_widget.value == f"@{pasted_file.resolve()}"


@pytest.mark.asyncio
async def test_copy_selected_paste_placeholder_uses_original_content():
    """Copying a selected compact paste should copy the expanded original content."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )
    pasted = "copy block\n2\n3\n4\n5\n6"

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(pasted))
        await pilot.pause()

        input_widget.select_all()
        input_widget.action_copy()

        assert app.clipboard == pasted



@pytest.mark.asyncio
async def test_large_paste_placeholder_backspace_deletes_atomically():
    """Backspace after a compact paste should remove the full placeholder."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )
    pasted = "alpha12345\nbeta\ngamma\ndelta\nepsilon\nzeta"

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(pasted))
        await pilot.pause()

        input_widget.action_delete_left()

        assert input_widget.value == ""
        assert input_widget.submit_value == ""


@pytest.mark.asyncio
async def test_pasted_file_path_backspace_deletes_atomically(tmp_path):
    """Backspace after a pasted file path should remove the whole path token."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )
    pasted_file = tmp_path / "pasted.txt"
    pasted_file.write_text("file contents")

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste(str(pasted_file)))
        await pilot.pause()

        input_widget.action_delete_left()

        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_at_path_completion_backspace_deletes_atomically(tmp_path):
    """Backspace after an @ path completion should remove the @ path token."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("@", "s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == "@src/main.py"

        input_widget.action_delete_left()

        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_decorated_tokens_render_yellow_and_bold(tmp_path):
    """Pasted placeholders, pasted files, and @ path tokens should render yellow bold."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    pasted_file = tmp_path / "pasted.txt"
    pasted_file.write_text("file contents")
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        await input_widget._on_paste(events.Paste("block\n2\n3\n4\n5\n6"))
        line = input_widget.get_line(0)
        assert line.spans[-1].style.bold is True
        assert line.spans[-1].style.color.name == "yellow"
        rendered_style = input_widget.render_line(0)._segments[0].style
        assert rendered_style.bold is True
        assert rendered_style.color.name == "yellow"

        input_widget.value = ""
        await input_widget._on_paste(events.Paste(str(pasted_file)))
        line = input_widget.get_line(0)
        assert line.spans[-1].style.bold is True
        assert line.spans[-1].style.color.name == "yellow"
        rendered_style = input_widget.render_line(0)._segments[0].style
        assert rendered_style.bold is True
        assert rendered_style.color.name == "yellow"

        input_widget.value = ""
        await pilot.press("@", "s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        line = input_widget.get_line(0)
        assert line.spans[-1].style.bold is True
        assert line.spans[-1].style.color.name == "yellow"
        rendered_style = input_widget.render_line(0)._segments[0].style
        assert rendered_style.bold is True
        assert rendered_style.color.name == "yellow"
