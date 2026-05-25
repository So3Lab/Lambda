"""Clipboard-aware chat input widget for the LambdaCodingAgent TUI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.actions import SkipAction
from textual.binding import Binding
from textual.widgets import Input, TextArea


@dataclass
class _DecoratedToken:
    display_text: str
    start: int
    end: int
    content: str | None = None


class ChatInput(TextArea):
    """Multi-line chat input with compact paste placeholders."""

    BINDINGS = [
        *TextArea.BINDINGS,
        Binding("shift+enter", "insert_newline", "Insert newline", show=False),
    ]

    _COMPACT_PASTE_MIN_LINES = 6
    _PASTE_PREVIEW_CHARS = 10
    _TOKEN_STYLE = Style.parse("bold yellow")

    def __init__(self, *args, **kwargs):
        self._decorated_tokens: list[_DecoratedToken] = []
        super().__init__(*args, **kwargs)

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, value: str) -> None:
        self._decorated_tokens.clear()
        self.text = value
        self.cursor_position = len(value)
        self._refresh_decorations()

    @property
    def submit_value(self) -> str:
        return self._expanded_text_between(0, len(self.value))

    @property
    def cursor_position(self) -> int:
        return self._location_to_position(self.cursor_location)

    @cursor_position.setter
    def cursor_position(self, position: int) -> None:
        position = max(0, min(position, len(self.text)))
        self.move_cursor(self._position_to_location(position))

    def replace(
        self,
        insert: str,
        start: int | tuple[int, int],
        end: int | tuple[int, int],
        *,
        maintain_selection_offset: bool = True,
    ):
        if isinstance(start, int) and isinstance(end, int):
            result = super().replace(
                insert,
                self._position_to_location(start),
                self._position_to_location(end),
                maintain_selection_offset=maintain_selection_offset,
            )
            self.move_cursor(result.end_location)
            return result
        return super().replace(
            insert,
            start,
            end,
            maintain_selection_offset=maintain_selection_offset,
        )

    def _position_to_location(self, position: int) -> tuple[int, int]:
        position = max(0, min(position, len(self.text)))
        current = 0
        for row, line in enumerate(self.text.split("\n")):
            line_end = current + len(line)
            if position <= line_end:
                return row, position - current
            current = line_end + 1
        last_row = self.document.line_count - 1
        return last_row, len(self.document[last_row])

    def _location_to_position(self, location: tuple[int, int]) -> int:
        row, column = location
        lines = self.text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        column = max(0, min(column, len(lines[row])))
        return sum(len(line) + 1 for line in lines[:row]) + column

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(Input.Submitted(self, self.submit_value))
            return
        await super()._on_key(event)

    def edit(self, edit):
        start = self._location_to_position(edit.top)
        end = self._location_to_position(edit.bottom)
        result = super().edit(edit)
        self._sync_decorated_tokens_after_edit(start, end, len(edit.text))
        self._prune_decorated_tokens()
        self.post_message(Input.Changed(self, self.value))
        return result

    def load_text(self, text: str) -> None:
        self._decorated_tokens.clear()
        super().load_text(text)
        self.post_message(Input.Changed(self, self.value))
        self._refresh_decorations()

    def get_line(self, line_index: int):
        line = super().get_line(line_index)
        line_start = self._location_to_position((line_index, 0))
        line_end = line_start + len(self.document.get_line(line_index))
        for token in self._decorated_tokens:
            start = max(token.start, line_start)
            end = min(token.end, line_end)
            if start < end:
                line.stylize(self._TOKEN_STYLE, start - line_start, end - line_start)
        return line

    def render_line(self, y: int):
        strip = super().render_line(y)
        scroll_y = self.scroll_offset.y
        try:
            line_info = self.wrapped_document._offset_to_line_info[scroll_y + y]
        except IndexError:
            return strip
        if line_info is None:
            return strip
        line_index, _ = line_info
        line_start = self._location_to_position((line_index, 0))
        line_end = line_start + len(self.document.get_line(line_index))
        token_ranges = [
            (max(token.start, line_start) - line_start, min(token.end, line_end) - line_start)
            for token in self._decorated_tokens
            if max(token.start, line_start) < min(token.end, line_end)
        ]
        if not token_ranges:
            return strip

        segments: list[Segment] = []
        offset = 0
        for segment in strip._segments:
            text = segment.text
            end_offset = offset + len(text)
            style = segment.style
            for start, end in token_ranges:
                if offset < end and end_offset > start:
                    style = self._with_token_style(style)
                    break
            segments.append(Segment(text, style, segment.control))
            offset = end_offset
        return strip.__class__(segments, strip.cell_length)

    def action_insert_newline(self) -> None:
        self._replace_via_keyboard("\n", *self.selection)

    def action_delete_left(self) -> None:
        if self.read_only:
            return
        start, end = self._delete_left_positions()
        self._delete_range_expanding_tokens(start, end)

    def action_delete_right(self) -> None:
        if self.read_only:
            return
        start, end = self._delete_right_positions()
        self._delete_range_expanding_tokens(start, end)

    def action_copy(self) -> None:
        start, end = self._selection_positions()
        if start != end:
            start, end = self._range_expanded_for_tokens(start, end)
            self.app.copy_to_clipboard(self._expanded_text_between(start, end))
        else:
            raise SkipAction()

    def action_cut(self) -> None:
        if self.read_only:
            return
        start, end = self._selection_positions()
        if start == end:
            return super().action_cut()
        start, end = self._range_expanded_for_tokens(start, end)
        self.app.copy_to_clipboard(self._expanded_text_between(start, end))
        if result := self._delete_via_keyboard(
            self._position_to_location(start),
            self._position_to_location(end),
        ):
            self.move_cursor(result.end_location)

    def action_paste(self) -> None:
        if self.read_only:
            return
        self.insert_clipboard_text(self.app.clipboard)

    async def _on_paste(self, event: events.Paste) -> None:
        event.stop()
        event.prevent_default()
        if self.read_only:
            return
        self.insert_clipboard_text(event.text)
        self.focus()

    def insert_clipboard_text(self, text: str) -> None:
        if not text:
            return

        file_paths = self._file_paths_from_clipboard(text)
        if file_paths is not None:
            file_tokens = [f"@{path}" for path in file_paths]
            inserted_text = "\n".join(file_tokens)
            result = self._insert_text(inserted_text)
            if result is not None:
                start = self._location_to_position(result.end_location) - len(inserted_text)
                offset = start
                for token in file_tokens:
                    self._register_decorated_token(token, offset, offset + len(token))
                    offset += len(token) + 1
            return

        display_text = self._display_text_for_paste(text)
        result = self._insert_text(display_text)
        if result is not None and display_text != text:
            end = self._location_to_position(result.end_location)
            self._register_decorated_token(
                display_text,
                end - len(display_text),
                end,
                content=text,
            )

    def insert_path_completion(self, start: int, end: int, path: str) -> None:
        token_start = start - 1 if start > 0 and self.value[start - 1] == "@" else start
        result = self.replace(path, start, end)
        token_end = self._location_to_position(result.end_location)
        self._register_decorated_token(
            self.value[token_start:token_end],
            token_start,
            token_end,
        )

    def expand_pasted_blocks(self, text: str) -> str:
        if text == self.value:
            return self.submit_value
        expanded = text
        for token in self._decorated_tokens:
            if token.content is not None:
                expanded = expanded.replace(token.display_text, token.content, 1)
        return expanded

    def _replace_via_keyboard(
        self,
        insert: str,
        start: tuple[int, int],
        end: tuple[int, int],
    ):
        start_pos = self._location_to_position(start)
        end_pos = self._location_to_position(end)
        if start_pos == end_pos:
            token = self._token_containing_position(start_pos)
            if token is not None:
                start_pos, end_pos = token.start, token.end
        else:
            start_pos, end_pos = self._range_expanded_for_tokens(start_pos, end_pos)
        return super()._replace_via_keyboard(
            insert,
            self._position_to_location(start_pos),
            self._position_to_location(end_pos),
        )

    def _insert_text(self, text: str):
        if result := self._replace_via_keyboard(text, *self.selection):
            self.move_cursor(result.end_location)
            return result
        return None

    def _display_text_for_paste(self, text: str) -> str:
        if self._line_count(text) < self._COMPACT_PASTE_MIN_LINES:
            return text
        preview = self._preview(text)
        return f"[Pasted {self._line_count(text)} lines: {preview}...]"

    def _line_count(self, text: str) -> int:
        line_count = len(text.splitlines())
        if text.endswith(("\r\n", "\n", "\r")):
            line_count += 1
        return max(1, line_count)

    def _preview(self, text: str) -> str:
        return (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", " ")[: self._PASTE_PREVIEW_CHARS]
        )

    def _file_paths_from_clipboard(self, text: str) -> list[str] | None:
        candidates = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if not candidates:
            return None

        paths: list[str] = []
        for candidate in candidates:
            path = self._path_from_clipboard_item(candidate)
            if path is None or not os.path.isfile(path):
                return None
            paths.append(os.path.abspath(path))
        return paths

    def _path_from_clipboard_item(self, item: str) -> str | None:
        if item.startswith("file://"):
            parsed = urlparse(item)
            if parsed.scheme != "file":
                return None
            return os.path.expanduser(unquote(parsed.path))
        return os.path.expanduser(item)

    def _selection_positions(self) -> tuple[int, int]:
        start, end = sorted(self.selection)
        return self._location_to_position(start), self._location_to_position(end)

    def _delete_left_positions(self) -> tuple[int, int]:
        start, end = self._selection_positions()
        if start != end:
            return start, end
        if end == 0:
            return end, end
        return end - 1, end

    def _delete_right_positions(self) -> tuple[int, int]:
        start, end = self._selection_positions()
        if start != end:
            return start, end
        if start >= len(self.value):
            return start, start
        return start, start + 1

    def _delete_range_expanding_tokens(self, start: int, end: int) -> None:
        if start == end:
            return
        start, end = self._range_expanded_for_tokens(start, end)
        self._delete_via_keyboard(
            self._position_to_location(start),
            self._position_to_location(end),
        )

    def _range_expanded_for_tokens(self, start: int, end: int) -> tuple[int, int]:
        start, end = sorted((start, end))
        changed = True
        while changed:
            changed = False
            for token in self._decorated_tokens:
                if start < token.end and end > token.start:
                    expanded_start = min(start, token.start)
                    expanded_end = max(end, token.end)
                    if expanded_start != start or expanded_end != end:
                        start, end = expanded_start, expanded_end
                        changed = True
        return start, end

    def _token_containing_position(self, position: int) -> _DecoratedToken | None:
        for token in self._decorated_tokens:
            if token.start < position < token.end:
                return token
        return None

    def _expanded_text_between(self, start: int, end: int) -> str:
        start, end = sorted((start, end))
        parts: list[str] = []
        cursor = start
        for token in sorted(self._decorated_tokens, key=lambda token: token.start):
            if token.content is None or token.start < start or token.end > end:
                continue
            parts.append(self.value[cursor : token.start])
            parts.append(token.content)
            cursor = token.end
        parts.append(self.value[cursor:end])
        return "".join(parts)

    def _with_token_style(self, style: Style | None) -> Style:
        if style is None:
            return self._TOKEN_STYLE
        return Style(
            color=self._TOKEN_STYLE.color,
            bgcolor=style.bgcolor,
            bold=True,
            dim=style.dim,
            italic=style.italic,
            underline=style.underline,
            blink=style.blink,
            blink2=style.blink2,
            reverse=style.reverse,
            conceal=style.conceal,
            strike=style.strike,
            underline2=style.underline2,
            frame=style.frame,
            encircle=style.encircle,
            overline=style.overline,
            link=style.link,
            meta=style.meta or None,
        )

    def _register_decorated_token(
        self,
        display_text: str,
        start: int,
        end: int,
        *,
        content: str | None = None,
    ) -> None:
        if not display_text or start < 0 or end <= start:
            return
        if self.value[start:end] != display_text:
            return
        self._decorated_tokens = [
            token
            for token in self._decorated_tokens
            if token.end <= start or token.start >= end
        ]
        self._decorated_tokens.append(_DecoratedToken(display_text, start, end, content))
        self._decorated_tokens.sort(key=lambda token: token.start)
        self._refresh_decorations()

    def _sync_decorated_tokens_after_edit(
        self,
        start: int,
        end: int,
        inserted_length: int,
    ) -> None:
        removed_length = end - start
        delta = inserted_length - removed_length
        synced: list[_DecoratedToken] = []
        for token in self._decorated_tokens:
            if end <= token.start:
                token.start += delta
                token.end += delta
                synced.append(token)
            elif start >= token.end:
                synced.append(token)
            else:
                continue
        self._decorated_tokens = synced
        self._refresh_decorations()

    def _prune_decorated_tokens(self) -> None:
        kept = [
            token
            for token in self._decorated_tokens
            if self.value[token.start : token.end] == token.display_text
        ]
        if len(kept) != len(self._decorated_tokens):
            self._decorated_tokens = kept
            self._refresh_decorations()

    def _refresh_decorations(self) -> None:
        self._line_cache.clear()
        self.refresh()
