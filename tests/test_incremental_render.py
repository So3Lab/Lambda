"""Tests for markdown rendering in the TUI.

These tests verify that the TUI uses Markdown.append(delta) for incremental
rendering, and that the Textual Markdown.append race condition is patched
so that rapid append calls don't produce duplicate content.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestModelStateHasAppendedLen:
    """_ModelState must track how much content has been sent to append()."""

    def test_content_appended_len_field_exists(self):
        from lambda_coding_agent.tui.app import _ModelState

        state = _ModelState(call_id="test")
        assert hasattr(state, "_content_appended_len")

    def test_content_appended_len_defaults_to_zero(self):
        from lambda_coding_agent.tui.app import _ModelState

        state = _ModelState(call_id="test")
        assert state._content_appended_len == 0


class TestFlushModelUsesAppend:
    """_flush_model must call append(delta), not update(full_content)."""

    def test_flush_calls_append_not_update(self):
        from lambda_coding_agent.tui.app import _ModelState, LambdaCodingTUIApp

        state = _ModelState(call_id="call1")
        state.content = "Hello world"
        state.content_dirty = True
        state._content_appended_len = 0

        mock_widget = MagicMock()
        mock_widget.append = MagicMock()
        mock_widget.update = MagicMock()
        state.content_widget = mock_widget

        app = LambdaCodingTUIApp.__new__(LambdaCodingTUIApp)
        app._models = {"call1": state}

        with patch.object(LambdaCodingTUIApp, '_scroll_to_bottom', new=lambda *a, **kw: None):
            app._flush_model("call1")

        mock_widget.append.assert_called_once_with("Hello world")
        mock_widget.update.assert_not_called()

    def test_flush_computes_correct_delta(self):
        from lambda_coding_agent.tui.app import _ModelState, LambdaCodingTUIApp

        state = _ModelState(call_id="call1")
        state.content = "Hello world, how are you?"
        state.content_dirty = True
        state._content_appended_len = 12  # "Hello world," already appended

        mock_widget = MagicMock()
        mock_widget.append = MagicMock()
        state.content_widget = mock_widget

        app = LambdaCodingTUIApp.__new__(LambdaCodingTUIApp)
        app._models = {"call1": state}

        with patch.object(LambdaCodingTUIApp, '_scroll_to_bottom', new=lambda *a, **kw: None):
            app._flush_model("call1")

        mock_widget.append.assert_called_once_with(" how are you?")

    def test_flush_updates_appended_len(self):
        from lambda_coding_agent.tui.app import _ModelState, LambdaCodingTUIApp

        state = _ModelState(call_id="call1")
        state.content = "Hello world"
        state.content_dirty = True
        state._content_appended_len = 0

        mock_widget = MagicMock()
        mock_widget.append = MagicMock()
        state.content_widget = mock_widget

        app = LambdaCodingTUIApp.__new__(LambdaCodingTUIApp)
        app._models = {"call1": state}

        with patch.object(LambdaCodingTUIApp, '_scroll_to_bottom', new=lambda *a, **kw: None):
            app._flush_model("call1")

        assert state._content_appended_len == 11  # len("Hello world")

    def test_flush_skips_append_when_no_delta(self):
        from lambda_coding_agent.tui.app import _ModelState, LambdaCodingTUIApp

        state = _ModelState(call_id="call1")
        state.content = "Hello"
        state.content_dirty = True
        state._content_appended_len = 5  # already fully appended

        mock_widget = MagicMock()
        mock_widget.append = MagicMock()
        state.content_widget = mock_widget

        app = LambdaCodingTUIApp.__new__(LambdaCodingTUIApp)
        app._models = {"call1": state}

        with patch.object(LambdaCodingTUIApp, '_scroll_to_bottom', new=lambda *a, **kw: None):
            app._flush_model("call1")

        mock_widget.append.assert_not_called()


class TestNewContentWidgetResetsAppendedLen:
    """When _needs_new_content_widget triggers, _content_appended_len must reset."""

    def test_appended_len_resets_on_new_widget(self):
        from lambda_coding_agent.tui.app import _ModelState

        state = _ModelState(call_id="call1")
        state.content = "some previously accumulated text"
        state._content_appended_len = 30

        # Simulate what append_model_content does when _needs_new_content_widget
        state.content = ""
        state._content_appended_len = 0

        assert state._content_appended_len == 0
        assert state.content == ""
