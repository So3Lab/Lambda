"""Theme helpers for the LambdaCodingAgent Textual UI."""

from __future__ import annotations

from rich.terminal_theme import TerminalTheme
from textual.color import Color
from textual.theme import Theme


def _theme_variables(theme: Theme) -> dict[str, str]:
    variables = theme.to_color_system().generate()
    variables.update(theme.variables)
    return variables


def _rgb(variables: dict[str, str], key: str, fallback: str) -> tuple[int, int, int]:
    value = variables.get(key, fallback)
    try:
        return Color.parse(value).rgb
    except Exception:
        return Color.parse(fallback).rgb


def terminal_theme_from_textual_theme(theme: Theme) -> TerminalTheme:
    """Build a Rich ANSI palette from Textual's active theme palette."""
    variables = _theme_variables(theme)
    background = _rgb(variables, "background", "#000000" if theme.dark else "#ffffff")
    foreground = _rgb(variables, "foreground", "#ffffff" if theme.dark else "#000000")
    normal = [
        _rgb(variables, "background-darken-2", "#000000"),
        _rgb(variables, "error", "#ff0000"),
        _rgb(variables, "success", "#00ff00"),
        _rgb(variables, "warning", "#ffff00"),
        _rgb(variables, "primary", "#0000ff"),
        _rgb(variables, "secondary", "#ff00ff"),
        _rgb(variables, "accent", "#00ffff"),
        _rgb(variables, "foreground-muted", variables.get("foreground", "#ffffff")),
    ]
    bright = [
        _rgb(variables, "foreground-disabled", variables.get("foreground", "#808080")),
        _rgb(variables, "error-lighten-1", variables.get("error", "#ff0000")),
        _rgb(variables, "success-lighten-1", variables.get("success", "#00ff00")),
        _rgb(variables, "warning-lighten-1", variables.get("warning", "#ffff00")),
        _rgb(variables, "primary-lighten-1", variables.get("primary", "#0000ff")),
        _rgb(variables, "secondary-lighten-1", variables.get("secondary", "#ff00ff")),
        _rgb(variables, "accent-lighten-1", variables.get("accent", "#00ffff")),
        foreground,
    ]
    return TerminalTheme(background, foreground, normal, bright)
