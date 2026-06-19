"""The belfry Textual application (lazygit-inspired layout).

A two-pane TUI: a file list on the left, and a tabbed Summary / Source view on
the right. Static analysis and git lookups are performed lazily as files are
highlighted, so startup stays snappy even in large trees.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from belfry.analyzer import analyze
from belfry.gitinfo import last_commit
from belfry.models import ArgSpec, FileRecord, FileRef, ScriptInfo
from belfry.scanner import discover


def _short_date(mtime: float) -> str:
    """Render an mtime as a short human date."""
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "?"


def _cheap_badge(path: Path) -> str:
    """Guess a badge from raw text without a full parse (fast, best-effort)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "error"
    if "# %%" in text:
        return "cell-script"
    lowered = text.lower()
    if "argparse" in lowered or "import click" in lowered or "import typer" in lowered:
        return "cli"
    return "script"


class BelfryApp(App):
    """The belfry TUI application."""

    CSS = """
    #body {
        height: 1fr;
    }
    #left {
        height: 40%;
        border: round $primary;
        border-title-align: left;
    }
    #right {
        height: 60%;
        border: round $primary;
    }
    #files {
        height: 1fr;
    }
    #filter {
        display: none;
        dock: bottom;
        border: tall $accent;
    }
    #filter.visible {
        display: block;
    }
    .section {
        margin: 0 1 1 1;
    }
    #summary_scroll {
        padding: 1;
    }
    #source_scroll {
        padding: 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("J", "focus_lower", "Lower pane"),
        Binding("K", "focus_upper", "Upper pane"),
        Binding("h", "prev_tab", "Prev tab"),
        Binding("l", "next_tab", "Next tab"),
        Binding("tab", "next_tab", "Next tab", show=False),
        Binding("shift+tab", "prev_tab", "Prev tab", show=False),
        Binding("slash", "focus_filter", "Filter"),
        Binding("r", "toggle_recurse", "Recurse"),
        Binding("enter", "open_editor", "Edit"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self, root: Path, recurse: bool = True) -> None:
        super().__init__()
        self.root = Path(root)
        self.recurse = recurse
        self._records: list[FileRecord] = []
        # Caches keyed by the relative-path string shown in the table.
        self._rel_to_path: dict[str, Path] = {}
        self._info_cache: dict[Path, ScriptInfo] = {}
        self._git_cache: dict[Path, object] = {}  # GitInfo | None | sentinel
        self._filter = ""

    # ------------------------------------------------------------------ compose
    def compose(self) -> ComposeResult:
        with Vertical(id="body"):
            with VerticalScroll(id="left"):
                table = DataTable(id="files", cursor_type="row", zebra_stripes=True)
                yield table
                yield Input(placeholder="filter by name...", id="filter")
            with VerticalScroll(id="right"):
                with TabbedContent(id="tabs"):
                    with TabPane("Summary", id="tab-summary"):
                        with VerticalScroll(id="summary_scroll"):
                            yield Static("", id="sec_git", classes="section")
                            yield Static("", id="sec_desc", classes="section")
                            yield Static("", id="sec_args", classes="section")
                            yield Static("", id="sec_inputs", classes="section")
                            yield Static("", id="sec_outputs", classes="section")
                    with TabPane("Source", id="tab-source"):
                        with VerticalScroll(id="source_scroll"):
                            yield Static("", id="source", expand=True)
        yield Footer()

    # -------------------------------------------------------------------- mount
    def on_mount(self) -> None:
        table = self.query_one("#files", DataTable)
        table.add_column("name", key="name")
        table.add_column("modified", key="modified", width=12)
        table.add_column("badge", key="badge", width=12)
        try:
            self.query_one("#left").border_title = "files"
        except Exception:
            pass
        # Let the lower-panel scroll containers take focus so they can be
        # jumped to (J) and scrolled (j/k) like a vim split.
        for sid in ("#summary_scroll", "#source_scroll"):
            try:
                self.query_one(sid).can_focus = True
            except Exception:
                pass
        self.populate()

    # --------------------------------------------------------------- discovery
    def populate(self) -> None:
        """(Re)discover files and rebuild the table from the current filter."""
        try:
            self._records = discover(self.root, self.recurse)
        except Exception as exc:  # never let discovery crash the UI
            self._records = []
            self._set_summary_error(f"discover() failed: {exc}")
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table = self.query_one("#files", DataTable)
        table.clear()
        self._rel_to_path.clear()

        needle = self._filter.lower()
        rows = 0
        for rec in self._records:
            try:
                rel = str(rec.path.relative_to(self.root))
            except ValueError:
                rel = str(rec.path)
            if needle and needle not in rel.lower():
                continue
            badge = _cheap_badge(rec.path)
            self._rel_to_path[rel] = rec.path
            table.add_row(rel, _short_date(rec.mtime), badge, key=rel)
            rows += 1

        if rows == 0:
            if self._filter:
                self._show_empty("No files match the filter.")
            else:
                self._show_empty("No .py files found.")
        else:
            # Highlighting the first row triggers RowHighlighted -> detail render.
            table.move_cursor(row=0)
            table.focus()

    def _show_empty(self, message: str) -> None:
        self.query_one("#sec_git", Static).update("")
        self.query_one("#sec_desc", Static).update(Text(message, style="dim"))
        self.query_one("#sec_args", Static).update("")
        self.query_one("#sec_inputs", Static).update("")
        self.query_one("#sec_outputs", Static).update("")
        self.query_one("#source", Static).update("")

    # ------------------------------------------------------------- selection
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if key is None:
            return
        path = self._rel_to_path.get(key)
        if path is None:
            return
        self._render_details(path, key)

    def _get_info(self, path: Path) -> ScriptInfo | str:
        """Return cached ScriptInfo or an error string."""
        cached = self._info_cache.get(path)
        if cached is not None:
            return cached
        try:
            info = analyze(path)
        except Exception as exc:  # resilient: one bad file must not crash
            return f"{type(exc).__name__}: {exc}"
        self._info_cache[path] = info
        return info

    def _get_git(self, path: Path):
        if path in self._git_cache:
            return self._git_cache[path]
        try:
            git = last_commit(path, self.root)
        except Exception:
            git = None
        self._git_cache[path] = git
        return git

    def _render_details(self, path: Path, rel: str) -> None:
        info = self._get_info(path)

        if isinstance(info, str):
            # Analysis blew up -- surface the error, mark the badge.
            self._update_badge(rel, "error")
            self._set_summary_error(info)
            self._render_source(path, None)
            return

        # Backfill the real badge now that we have a parse.
        self._update_badge(rel, info.badge)
        try:
            self._render_git(path)
            self._render_description(info)
            self._render_args(info)
            self._render_inputs(info.inputs)
            self._render_outputs(info.outputs)
        except Exception as exc:  # a render bug must never crash the app
            self._set_summary_error(f"render error: {type(exc).__name__}: {exc}")
        self._render_source(path, info)

    def _update_badge(self, rel: str, badge: str) -> None:
        table = self.query_one("#files", DataTable)
        try:
            table.update_cell(rel, "badge", badge)
        except Exception:
            pass

    # ------------------------------------------------------- section renderers
    def _set_summary_error(self, message: str) -> None:
        self.query_one("#sec_git", Static).update("")
        t = Text()
        t.append("ANALYSIS ERROR\n", style="bold red")
        t.append(message)
        self.query_one("#sec_desc", Static).update(t)
        self.query_one("#sec_args", Static).update("")
        self.query_one("#sec_inputs", Static).update("")
        self.query_one("#sec_outputs", Static).update("")

    def _render_git(self, path: Path) -> None:
        git = self._get_git(path)
        t = Text()
        t.append("GIT / PROVENANCE\n", style="bold")
        if git is None:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            t.append(
                f"untracked / not a git repo -- modified {_short_date(mtime)}",
                style="dim",
            )
        else:
            t.append(git.short_hash, style="yellow")
            t.append(f"  {git.date}  {git.subject}")
        self.query_one("#sec_git", Static).update(t)

    def _render_description(self, info: ScriptInfo) -> None:
        t = Text()
        t.append("DESCRIPTION\n", style="bold")
        if info.docstring:
            t.append(info.docstring.strip())
        if info.lead_comments:
            if info.docstring:
                t.append("\n\n")
            t.append(info.lead_comments.strip(), style="dim")
        if not info.docstring and not info.lead_comments:
            t.append("(no docstring)", style="dim")
        self.query_one("#sec_desc", Static).update(t)

    def _render_args(self, info: ScriptInfo) -> None:
        spec: ArgSpec = info.args
        t = Text()
        t.append("ARGUMENTS\n", style="bold")
        t.append("style: ", style="cyan")
        t.append(spec.style)

        if spec.args:
            for a in spec.args:
                t.append("\n  ")
                t.append(a.name, style="green")
                if a.type:
                    t.append(f"  [{a.type}]", style="magenta")
                if a.default is not None:
                    t.append(f"  (default={a.default})", style="dim")
                if a.required:
                    t.append("  (required)", style="red")
                if a.choices:
                    t.append(f"  choices={', '.join(a.choices)}", style="dim")
                if a.help:
                    t.append(f"  -- {a.help}")
        else:
            t.append("\n  ")
            t.append("(none found)", style="dim")

        for note in spec.notes:
            t.append("\n  ")
            t.append(f"note: {note}", style="dim")

        # For cell scripts / no formal CLI, surface the constant "knobs",
        # one per line for easy reading.
        if spec.style in ("none", "cell-script") and info.constants:
            t.append("\n  ")
            t.append("Knobs:", style="cyan")
            for k, v in info.constants.items():
                t.append("\n    ")
                t.append(k, style="green")
                t.append(f" = {v}")

        self.query_one("#sec_args", Static).update(t)

    def _render_filerefs(self, header: str, refs: list[FileRef]) -> Text:
        t = Text()
        t.append(header + "\n", style="bold")
        if not refs:
            t.append("  ")
            t.append("(none found)", style="dim")
            return t
        for i, r in enumerate(refs):
            if i:
                t.append("\n")
            func = r.func if len(r.func) <= 48 else r.func[:47] + "…"
            t.append("  ")
            t.append(r.raw, style="white")
            t.append("   ")
            t.append(f"[{func}, line {r.lineno}]", style="dim")
            if not r.resolved:
                t.append(" (unresolved)", style="dim")
        return t

    def _render_inputs(self, refs: list[FileRef]) -> None:
        self.query_one("#sec_inputs", Static).update(
            self._render_filerefs("INPUTS", refs)
        )

    def _render_outputs(self, refs: list[FileRef]) -> None:
        self.query_one("#sec_outputs", Static).update(
            self._render_filerefs("OUTPUTS", refs)
        )

    def _render_source(self, path: Path, info: ScriptInfo | None) -> None:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.query_one("#source", Static).update(
                Text(f"could not read source: {exc}", style="red")
            )
            return
        syntax = Syntax(
            source,
            "python",
            line_numbers=True,
            theme="monokai",
            word_wrap=False,
        )
        self.query_one("#source", Static).update(syntax)

    # ---------------------------------------------------------------- panes
    def _active_scroll(self) -> VerticalScroll:
        """The scroll container of the currently-active lower-panel tab."""
        tabs = self.query_one("#tabs", TabbedContent)
        sid = "summary_scroll" if tabs.active == "tab-summary" else "source_scroll"
        return self.query_one(f"#{sid}", VerticalScroll)

    def _in_lower_panel(self) -> bool:
        """True when focus currently rests inside the lower (details) panel."""
        node = self.focused
        while node is not None:
            if getattr(node, "id", None) == "right":
                return True
            node = node.parent
        return False

    def _focus_active_scroll(self) -> None:
        try:
            self._active_scroll().focus()
        except Exception:
            pass

    def action_focus_lower(self) -> None:
        """Jump focus to the lower (details) panel."""
        self._focus_active_scroll()

    def action_focus_upper(self) -> None:
        """Jump focus to the upper (files) panel."""
        try:
            self.query_one("#files", DataTable).focus()
        except Exception:
            pass

    # -------------------------------------------------------------- actions
    def action_cursor_down(self) -> None:
        # j scrolls the details when the lower panel holds focus, else it
        # moves the file-list cursor.
        if self._in_lower_panel():
            self._active_scroll().scroll_down()
            return
        try:
            self.query_one("#files", DataTable).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        if self._in_lower_panel():
            self._active_scroll().scroll_up()
            return
        try:
            self.query_one("#files", DataTable).action_cursor_up()
        except Exception:
            pass

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        self._cycle_tab(-1)

    def _cycle_tab(self, step: int) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        order = ["tab-summary", "tab-source"]
        try:
            idx = order.index(tabs.active)
        except ValueError:
            idx = 0
        tabs.active = order[(idx + step) % len(order)]
        # Keep focus on the newly-active pane's scroll if the user is already
        # working in the lower panel, so j/k keep scrolling what's visible.
        if self._in_lower_panel():
            self.call_after_refresh(self._focus_active_scroll)

    def action_focus_filter(self) -> None:
        inp = self.query_one("#filter", Input)
        inp.add_class("visible")
        inp.focus()

    def action_clear_filter(self) -> None:
        inp = self.query_one("#filter", Input)
        if inp.has_focus or self._filter:
            inp.value = ""
            self._filter = ""
            inp.remove_class("visible")
            self._rebuild_table()
        else:
            inp.remove_class("visible")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        self._filter = event.value
        self._rebuild_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter":
            return
        self.query_one("#files", DataTable).focus()

    def action_toggle_recurse(self) -> None:
        self.recurse = not self.recurse
        self.notify(f"recurse = {self.recurse}")
        self.populate()

    def action_open_editor(self) -> None:
        # If the filter input is focused, Enter belongs to it, not the editor.
        if self.focused is not None and self.focused.id == "filter":
            return
        table = self.query_one("#files", DataTable)
        if table.row_count == 0:
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            rel = row_key.value
        except Exception:
            return
        if rel is None:
            return
        path = self._rel_to_path.get(rel)
        if path is None:
            return

        editor = os.environ.get("EDITOR", "vi")
        if not editor:
            self.notify("No $EDITOR set.", severity="warning")
            return

        with self.suspend():
            try:
                subprocess.run([editor, str(path)])
            except Exception as exc:  # editor missing / failed -> report, resume
                print(f"belfry: could not launch editor {editor!r}: {exc}")
        # Source may have changed on disk; drop caches for this file.
        self._info_cache.pop(path, None)
        if rel:
            self._render_details(path, rel)
