"""The IXM-VAS window -- the VA's workplace.

A dark, borderless window: brand top-left; the Shopify session pill and the
window controls top-right; a card in the middle holding INITIALIZE ALL SHOPIFY
STORES. Theme and widgets live in ui.py.

THE LOGIN GATE
  The pill is the only way in. Red (LOGGED OUT): clicking it opens the VA's
  own Chrome profile on the Shopify login; the moment the dashboard answers,
  Chrome closes by itself and the pill turns green. Only green (LOGGED IN)
  enables INITIALIZE. A run that finds the session gone turns the pill red
  again and opens nothing.

ONE TASK AT A TIME
  Checking, logging in and running all drive the same Chrome profile, and
  Chrome allows one process per profile -- a second launch kills the first
  mid-run. So the window runs at most one worker, and everything that would
  start another is disabled until it ends.

UPDATES
  Every minute the window asks GitHub for the current VERSION (updater.py).
  If it is newer than this copy, the window locks behind UPDATE NOW. A store
  run in progress is allowed to finish first; a check or a login is stopped.
"""

from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox
from typing import Optional

from . import paths, protocol, ui, updater, winframe

TITLE = "IXM-VAS"
APP_ID = "IXM.IXM-VAS"
SHOPIFY_TASK = "ixm_vas.tasks.shopify_open_all_stores"

UPDATE_CHECK_MS = 60_000
SESSION_RECHECK_MS = 15 * 60_000     # catch a session that expired while idle
POLL_MS = 100
LOG_MAX_LINES = 4000

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# session state -> (pill text, pill look)
SESSION_LOOK = {
    "checking":    ("CHECKING…", ui.WAIT),
    "logged_out":  ("LOGGED OUT", ui.BAD),
    "logging_in":  ("WAITING FOR LOGIN…", ui.WAIT),
    "logged_in":   ("LOGGED IN", ui.OK),
    "unreachable": ("UNREACHABLE", ui.BAD),
    "busy":        ("CHROME IN USE", ui.BAD),
}

# What the line under the button says when nothing more specific has happened.
SESSION_HINT = {
    "checking":    "Checking your Shopify login…",
    "logged_out":  "Log in first: click LOGGED OUT at the top right.",
    "logging_in":  "Chrome is open on the Shopify login. Sign in there; it closes by "
                   "itself once you're in.",
    "logged_in":   "Ready.",
    "unreachable": "Couldn't reach Shopify. Check your internet, then click "
                   "UNREACHABLE at the top right to try again.",
    "busy":        "Chrome is already open on your IXM-VAS profile. Close that Chrome "
                   "window, then click CHROME IN USE at the top right.",
}


class Worker:
    """One task process. Its stdout lines arrive on the window's queue."""

    def __init__(self, mode: str, events: "queue.Queue") -> None:
        self.mode = mode
        self.events = events
        self.proc = subprocess.Popen(
            [updater.console_python(), "-u", "-m", SHOPIFY_TASK, mode],
            cwd=str(paths.ROOT),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"),
            creationflags=NO_WINDOW,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self.events.put(("line", self, line.rstrip("\r\n")))
        self.events.put(("exit", self, self.proc.wait()))

    def kill(self) -> None:
        """Stop the task and the Chrome it started."""
        if self.proc.poll() is not None:
            return
        if sys.platform == "win32":
            # /T: the whole tree -- Playwright's driver and its Chrome too, so
            # nothing is left holding the profile.
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            self.proc.kill()


class App:
    def __init__(self, root: tk.Tk, instance: winframe.SingleInstance) -> None:
        self.root = root
        self.instance = instance
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.fonts = ui.Fonts(root)
        self.events: "queue.Queue" = queue.Queue()

        self.worker: Optional[Worker] = None
        self.session = "checking"
        self.account = ""
        self.message: Optional[tuple] = None     # (text, is_error) overriding the hint
        self.last_error = ""
        self.last_report: Optional[str] = None
        self.progress_total = 0

        self.checker = updater.Checker()
        self.checking_update = False
        self.update_info: Optional[updater.CheckResult] = None
        self.update_note = ""
        self.updating = False
        self.update_message: Optional[tuple] = None

        self.maximized = False
        self.normal_rect: Optional[tuple] = None
        self.log_visible = False
        self.confirming = False

        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)     # Alt+F4, taskbar Close
        root.report_callback_exception = self._on_callback_error
        root.after(POLL_MS, self._poll)
        root.after(200, lambda: self._start("check"))
        root.after(500, self._update_tick)
        root.after(SESSION_RECHECK_MS, self._session_tick)

    def px(self, n: float) -> int:
        return int(round(n * self.scale))

    def initial_rect(self) -> tuple:
        """Centered, a little above the middle, never larger than the screen."""
        area = winframe.work_area(self.root) or (0, 0, self.root.winfo_screenwidth(),
                                                 self.root.winfo_screenheight())
        ax, ay, aw, ah = area
        w, h = min(self.px(1120), aw), min(self.px(700), ah)
        return ax + (aw - w) // 2, ay + max(0, (ah - h) // 2 - self.px(16)), w, h

    # ============================================================
    # LAYOUT
    # ============================================================
    def _build(self) -> None:
        root, f, px = self.root, self.fonts, self.px
        root.title(TITLE)
        root.configure(bg=ui.OUTLINE)
        if paths.ICON_ICO.exists():
            try:
                root.iconbitmap(default=str(paths.ICON_ICO))
            except tk.TclError:
                pass
        x, y, w, h = self.initial_rect()
        root.geometry(f"{w}x{h}+{x}+{y}")

        # 1px outline: without its frame the window has no edge of its own.
        shell = tk.Frame(root, bg=ui.BG)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        self.shell = shell

        # --- top bar -------------------------------------------------------
        self.bar_h = px(52)
        top = tk.Frame(shell, bg=ui.SURFACE, height=self.bar_h)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        tk.Frame(shell, bg=ui.BORDER, height=1).pack(side="top", fill="x")
        self.topbar = top

        mark = tk.Canvas(top, width=px(24), height=px(24), bg=ui.SURFACE,
                         highlightthickness=0, borderwidth=0)
        mark.create_image(0, 0, anchor="nw",
                          image=ui.rounded(px(24), px(24), px(6), ui.ACCENT, ui.SURFACE))
        mark.create_text(px(12), px(12), text="IX", fill=ui.ON_ACCENT, font=f.overline)
        mark.pack(side="left", padx=(px(16), px(10)))
        brand = tk.Label(top, text="IXM-VAS", bg=ui.SURFACE, fg=ui.TEXT, font=f.brand)
        brand.pack(side="left")
        tag = tk.Label(top, text="VA workplace", bg=ui.SURFACE, fg=ui.TEXT_FAINT, font=f.small)
        tag.pack(side="left", padx=(px(8), 0))

        self.btn_close = self._window_button(top, ui.GLYPH_CLOSE, self._on_close,
                                             hover_bg=ui.CLOSE_HOVER, hover_fg="#FFFFFF")
        self.btn_max = self._window_button(top, ui.GLYPH_MAX, self._toggle_max)
        self.btn_min = self._window_button(top, ui.GLYPH_MIN, self._minimize)

        self.pill = ui.Button(top, "", self._on_pill, look=ui.WAIT, font=f.small_semi,
                              bg=ui.SURFACE, padx=px(14), pady=px(6), radius=px(15),
                              prefix="SHOPIFY", grey_when_disabled=False)
        self.pill.pack(side="right", padx=(0, px(14)))

        for wdg in (top, mark, brand, tag):
            wdg.bind("<ButtonPress-1>", self._drag)
            wdg.bind("<Double-Button-1>", lambda _: self._toggle_max())

        # --- footer --------------------------------------------------------
        foot = tk.Frame(shell, bg=ui.BG)
        foot.pack(side="bottom", fill="x", padx=px(18), pady=(px(4), px(12)))
        self.version_lbl = tk.Label(foot, bg=ui.BG, fg=ui.TEXT_FAINT, font=f.small)
        self.version_lbl.pack(side="left")
        self.log_toggle = ui.Link(foot, "Show log", self._toggle_log, font=f.small, bg=ui.BG)
        self.log_toggle.pack(side="right")
        self.account_lbl = tk.Label(foot, bg=ui.BG, fg=ui.TEXT_MUTED, font=f.small)
        self.account_lbl.pack(side="right", padx=px(18))

        grip = tk.Frame(shell, bg=ui.BG, width=px(12), height=px(12), cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)

        # --- log panel (hidden until "Show log") ---------------------------
        self.log_frame = tk.Frame(shell, bg=ui.BORDER)
        self.log_text = tk.Text(self.log_frame, height=11, bg=ui.LOG_BG, fg=ui.LOG_TEXT,
                                insertbackground=ui.LOG_TEXT, selectbackground=ui.SURFACE_3,
                                relief="flat", borderwidth=0, highlightthickness=0,
                                font=f.mono, wrap="none", padx=px(12), pady=px(10),
                                state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=1, pady=1)

        # --- body: the Shopify card ----------------------------------------
        body = tk.Frame(shell, bg=ui.BG)
        body.pack(side="top", fill="both", expand=True)
        self.body = body

        content_w = px(440)
        card = ui.Card(body, bg=ui.BG, radius=px(14), pad=px(30))
        card.place(relx=0.5, rely=0.48, anchor="center")
        c = card.inner
        tk.Label(c, text="SHOPIFY PARTNERS", bg=ui.SURFACE, fg=ui.TEXT_FAINT,
                 font=f.overline).pack(anchor="w")
        tk.Label(c, text="Open all stores", bg=ui.SURFACE, fg=ui.TEXT,
                 font=f.title).pack(anchor="w", pady=(px(2), px(6)))
        tk.Label(c, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=f.body, justify="left",
                 wraplength=content_w,
                 text="Opens every collaborator store for the Shopify account you're "
                      "logged in with, so direct admin links work. Takes about two "
                      "minutes.").pack(anchor="w")
        self.init_btn = ui.Button(c, "INITIALIZE ALL SHOPIFY STORES", self._on_initialize,
                                  look=ui.PRIMARY, font=f.button, bg=ui.SURFACE,
                                  padx=px(20), pady=px(12), radius=px(8), min_width=content_w)
        self.init_btn.pack(anchor="w", pady=(px(24), 0))
        self.bar = tk.Canvas(c, width=content_w, height=px(4), bg=ui.TRACK,
                             highlightthickness=0, borderwidth=0)
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, px(4), fill=ui.ACCENT, width=0)
        self.status_lbl = tk.Label(c, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=f.body,
                                   justify="left", anchor="w", wraplength=content_w)
        self.status_lbl.pack(anchor="w", fill="x", pady=(px(14), 0))
        self.report_link = ui.Link(c, "Open report  →", self._open_report, font=f.small_semi,
                                   bg=ui.SURFACE, fg=ui.ACCENT, hover=ui.ACCENT_HOVER)

        # --- update lock: covers everything under the top bar --------------
        self.overlay = tk.Frame(shell, bg=ui.BG)
        ocard = ui.Card(self.overlay, bg=ui.BG, radius=px(14), pad=px(30))
        ocard.place(relx=0.5, rely=0.48, anchor="center")
        o = ocard.inner
        tk.Label(o, text="NEW VERSION", bg=ui.SURFACE, fg=ui.ACCENT,
                 font=f.overline).pack(anchor="w")
        tk.Label(o, text="Update required", bg=ui.SURFACE, fg=ui.TEXT,
                 font=f.title).pack(anchor="w", pady=(px(2), px(6)))
        self.ov_text = tk.Label(o, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=f.body,
                                justify="left", wraplength=px(380))
        self.ov_text.pack(anchor="w")
        self.ov_btn = ui.Button(o, "UPDATE NOW", self._on_update, look=ui.PRIMARY,
                                font=f.button, bg=ui.SURFACE, padx=px(20), pady=px(12),
                                radius=px(8), min_width=px(380))
        self.ov_btn.pack(anchor="w", pady=(px(22), 0))
        self.ov_status = tk.Label(o, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=f.body,
                                  justify="left", anchor="w", wraplength=px(380))
        self.ov_status.pack(anchor="w", fill="x", pady=(px(12), 0))

        # --- "stop the run?" -- in the window's own style, not a system box -
        self.confirm = tk.Frame(shell, bg=ui.BG)
        ccard = ui.Card(self.confirm, bg=ui.BG, radius=px(14), pad=px(30))
        ccard.place(relx=0.5, rely=0.48, anchor="center")
        k = ccard.inner
        tk.Label(k, text="Stop the run?", bg=ui.SURFACE, fg=ui.TEXT,
                 font=f.title).pack(anchor="w", pady=(0, px(6)))
        tk.Label(k, bg=ui.SURFACE, fg=ui.TEXT_MUTED, font=f.body, justify="left",
                 wraplength=px(380),
                 text="Stores are still being opened. Closing IXM-VAS stops the run "
                      "part-way; you can start it again any time.").pack(anchor="w")
        row = tk.Frame(k, bg=ui.SURFACE)
        row.pack(anchor="w", pady=(px(22), 0))
        ui.Button(row, "Keep running", self._cancel_close, look=ui.SECONDARY, font=f.button,
                  bg=ui.SURFACE, padx=px(18), pady=px(10), radius=px(8),
                  min_width=px(184)).pack(side="left")
        ui.Button(row, "Stop and close", self._force_close, look=ui.DANGER, font=f.button,
                  bg=ui.SURFACE, padx=px(18), pady=px(10), radius=px(8),
                  min_width=px(184)).pack(side="left", padx=(px(12), 0))

        self._refresh()

    def _window_button(self, parent, glyph, command, hover_bg=ui.SURFACE_3,
                       hover_fg=ui.TEXT) -> ui.WindowButton:
        box = tk.Frame(parent, width=self.px(46), height=self.bar_h, bg=ui.SURFACE)
        box.pack(side="right")
        box.pack_propagate(False)
        btn = ui.WindowButton(box, glyph, command, fonts=self.fonts,
                              hover_bg=hover_bg, hover_fg=hover_fg)
        btn.pack(fill="both", expand=True)
        return btn

    # ============================================================
    # STATE -> SCREEN
    # ============================================================
    @property
    def blocked(self) -> bool:
        return self.update_info is not None and self.update_info.update_required

    def _refresh(self) -> None:
        text, look = SESSION_LOOK[self.session]
        self.pill.update_look(text=text, look=look)
        idle = self.worker is None and not self.blocked
        self.pill.set_enabled(idle and self.session in ("logged_out", "logged_in",
                                                        "unreachable", "busy"))

        running = self.worker is not None and self.worker.mode == "run"
        if not running:
            self.init_btn.update_look(text="INITIALIZE ALL SHOPIFY STORES")
            self.bar.pack_forget()
        self.init_btn.set_enabled(idle and self.session == "logged_in")

        if self.message:
            text, is_error = self.message
        else:
            text, is_error = SESSION_HINT[self.session], False
        self.status_lbl.configure(text=text, fg=ui.ERROR_TEXT if is_error else ui.TEXT_MUTED)

        if self.last_report and not running:
            self.report_link.pack(anchor="w", pady=(self.px(10), 0))
        else:
            self.report_link.pack_forget()

        self.account_lbl.configure(
            text=f"Signed in as {self.account}" if self.session == "logged_in" and self.account else "")

        local = paths.read_version()
        note = "  ·  dev checkout" if paths.is_git_checkout() else ""
        self.version_lbl.configure(text=f"v{local}{note}{self.update_note}")

        self._refresh_overlay()

    def _refresh_overlay(self) -> None:
        if not self.blocked:
            self.overlay.place_forget()
            return
        info = self.update_info
        self.ov_text.configure(
            text=f"IXM-VAS {info.remote} is out; you're on {info.local}. Update to keep "
                 "working. It takes about a minute and keeps your Shopify login.")
        waiting = self.worker is not None
        self.ov_btn.set_enabled(not self.updating and not waiting)
        if self.update_message:
            text, is_error = self.update_message
        elif waiting:
            text, is_error = "Finishing the current run first…", False
        else:
            text, is_error = "", False
        self.ov_status.configure(text=text, fg=ui.ERROR_TEXT if is_error else ui.TEXT_MUTED)
        if text:
            self.ov_status.pack(anchor="w", fill="x", pady=(self.px(12), 0))
        else:
            self.ov_status.pack_forget()
        if self.confirming:
            return
        self.overlay.place(x=0, y=self.bar_h + 1, relwidth=1.0, relheight=1.0,
                           height=-(self.bar_h + 1))
        self.overlay.lift()

    def _say(self, text: str, error: bool = False) -> None:
        self.message = (text, error)

    # ============================================================
    # WORKERS
    # ============================================================
    def _start(self, mode: str) -> None:
        if self.worker is not None or self.blocked:
            return
        self.message = None
        self.last_error = ""
        self._log(f"--- {datetime.now():%H:%M:%S}  shopify {mode} ---")
        try:
            self.worker = Worker(mode, self.events)
        except OSError as e:
            self._say(f"Couldn't start the task: {e}", error=True)
            self._refresh()
            return
        if mode == "check":
            self.session = "checking"
        elif mode == "login":
            self.session = "logging_in"
        elif mode == "run":
            self.progress_total = 0
            self.init_btn.update_look(text="INITIALIZING…")
            self._set_bar(0.0)
            self.bar.pack(anchor="w", pady=(self.px(16), 0), before=self.status_lbl)
            self._say("Checking your Shopify login…")
        self._refresh()

    def _on_line(self, worker: Worker, line: str) -> None:
        msg = protocol.parse(line)
        if msg is None:
            self._log(line)
            return
        kind = msg.get("kind")

        if kind == "session":
            state = msg.get("state")
            if state == "logged_in":
                self.session = "logged_in"
                self.account = msg.get("account") or ""
            elif state == "logged_out":
                self.session = "logged_out"
                self.account = ""
                if msg.get("reason") == "timeout":
                    self._say("Stopped waiting for the login after 10 minutes. Click "
                              "LOGGED OUT to try again.")
        elif kind == "login_window_open":
            self.message = None
        elif kind == "profile_busy":
            self.session = "busy"
            self.message = None
        elif kind == "phase" and msg.get("name") == "inventory":
            self._say("Reading your store list…")
        elif kind == "inventory":
            self._say(f"Reading your store list… {msg.get('found', 0)} stores so far")
        elif kind == "phase" and msg.get("name") == "open":
            self.progress_total = int(msg.get("total") or 0)
            self._say(f"Opening {self.progress_total} stores…")
        elif kind == "progress":
            done, total = int(msg.get("done", 0)), int(msg.get("total") or 1)
            self._set_bar(done / total)
            self._say(f"Opening stores… {done} of {total}")
        elif kind == "result":
            self.last_report = msg.get("report")
            self._say(self._summary(msg))
        elif kind == "error":
            self.last_error = msg.get("message") or ""
        self._refresh()

    @staticmethod
    def _summary(r: dict) -> str:
        parts = [f"{r.get('opened', 0)} opened"]
        if r.get("dormant"):
            parts.append(f"{r['dormant']} dormant")
        if r.get("revoked"):
            parts.append(f"{r['revoked']} revoked or pending")
        head = f"Done. {r.get('stores', 0)} stores: " + ", ".join(parts) + "."
        failed = r.get("failed", 0)
        if failed:
            return head + f"\n{failed} need attention; they're listed at the top of the report."
        return head + "\nEvery openable store is open."

    def _on_exit(self, worker: Worker, code: int) -> None:
        if worker is not self.worker:
            return
        self.worker = None
        mode = worker.mode
        self._log(f"--- {mode} ended (exit {code}) ---")

        if code == protocol.EXIT_PROFILE_BUSY:
            self.session = "busy"
            self.message = None
        elif mode == "check":
            if code not in (protocol.EXIT_OK, protocol.EXIT_LOGGED_OUT) or self.session == "checking":
                self.session = "unreachable"
                if self.last_error:
                    self._say(f"Couldn't check the Shopify login: {self.last_error}", error=True)
        elif mode == "login":
            if code == protocol.EXIT_OK and self.session == "logged_in":
                self._say("You're logged in. You can initialize the stores now.")
            elif self.session != "logged_out" or code not in (protocol.EXIT_OK, protocol.EXIT_LOGIN_ABORTED):
                self.session = "logged_out"
                self._say(f"Login didn't finish: {self.last_error or 'see the log'}", error=True)
        elif mode == "run":
            if code == protocol.EXIT_LOGGED_OUT:
                self._say("Your Shopify session has ended, so nothing was opened. Log in "
                          "again (top right), then initialize.", error=True)
            elif code not in (protocol.EXIT_OK, protocol.EXIT_SOME_FAILED):
                self._say(f"The run stopped: {self.last_error or 'see the log'}", error=True)
        if self.confirming:
            self._cancel_close()          # nothing left to stop
        self._refresh()

    # ============================================================
    # ACTIONS
    # ============================================================
    def _on_pill(self) -> None:
        if self.worker is not None or self.blocked:
            return
        if self.session == "logged_out":
            self._start("login")
        else:
            # Green: re-verify. Unreachable / busy: try again.
            self._start("check")

    def _on_initialize(self) -> None:
        if self.session == "logged_in" and self.worker is None and not self.blocked:
            self._start("run")

    def _set_bar(self, fraction: float) -> None:
        width = int(int(self.bar.cget("width")) * max(0.0, min(1.0, fraction)))
        self.bar.coords(self.bar_fill, 0, 0, width, self.px(4))

    def _open_report(self) -> None:
        if self.last_report and os.path.exists(self.last_report):
            os.startfile(self.last_report)          # Notepad, for a .txt

    def _toggle_log(self) -> None:
        if self.log_visible:
            self.log_frame.pack_forget()
            self.log_toggle.configure(text="Show log")
        else:
            self.log_frame.pack(side="bottom", fill="x", padx=self.px(18),
                                pady=(0, self.px(4)), before=self.body)
            self.log_toggle.configure(text="Hide log")
            self.log_text.see("end")
        self.log_visible = not self.log_visible

    def _log(self, line: str) -> None:
        t = self.log_text
        t.configure(state="normal")
        t.insert("end", line + "\n")
        excess = int(t.index("end-1c").split(".")[0]) - LOG_MAX_LINES
        if excess > 0:
            t.delete("1.0", f"{excess + 1}.0")
        t.configure(state="disabled")
        t.see("end")

    # ============================================================
    # UPDATES
    # ============================================================
    def _update_tick(self) -> None:
        if not self.checking_update and not self.updating:
            self.checking_update = True
            threading.Thread(target=self._update_check_bg, daemon=True).start()
        self.root.after(UPDATE_CHECK_MS, self._update_tick)

    def _update_check_bg(self) -> None:
        try:
            self.events.put(("update", None, self.checker.check()))
        except Exception as e:
            self.events.put(("update_error", None, f"{type(e).__name__}: {e}"))

    def _on_update_result(self, result: updater.CheckResult) -> None:
        self.checking_update = False
        self.update_note = ""
        was_blocked = self.blocked
        self.update_info = result
        if self.blocked and not was_blocked:
            self._log(f"--- update required: {result.local} -> {result.remote} ---")
            self.update_message = None
            # A check or a login can simply be redone after the update; a run
            # in progress is left to finish.
            if self.worker is not None and self.worker.mode != "run":
                self.worker.kill()
        self._refresh()

    def _on_update(self) -> None:
        if self.updating or self.worker is not None or not self.blocked:
            return
        if paths.is_git_checkout():
            self.update_message = ("This copy is a git checkout. Update it with git pull, "
                                   "then restart IXM-VAS.", True)
            self._refresh()
            return
        self.updating = True
        self.update_message = ("Starting the update…", False)
        self._refresh()
        sha = self.update_info.sha
        threading.Thread(target=self._update_bg, args=(sha,), daemon=True).start()

    def _update_bg(self, sha: str) -> None:
        try:
            updater.install(sha, on_step=lambda m: self.events.put(("update_step", None, m)))
            self.events.put(("update_done", None, None))
        except Exception as e:
            self.events.put(("update_failed", None, str(e)))

    def _restart_after_update(self) -> None:
        self.update_message = ("Updated. Restarting…", False)
        self._refresh()
        self.root.update_idletasks()
        self.instance.release()      # the new copy must be able to take it
        updater.relaunch()
        self.root.destroy()

    # ============================================================
    # EVENT PUMP
    # ============================================================
    def _poll(self) -> None:
        try:
            while True:
                kind, src, data = self.events.get_nowait()
                if kind == "line":
                    self._on_line(src, data)
                elif kind == "exit":
                    self._on_exit(src, data)
                elif kind == "update":
                    self._on_update_result(data)
                elif kind == "update_error":
                    self.checking_update = False
                    self.update_note = "  ·  update check failed"
                    self._refresh()
                elif kind == "update_step":
                    self.update_message = (data, False)
                    self._refresh()
                elif kind == "update_done":
                    self._restart_after_update()
                    return
                elif kind == "update_failed":
                    self.updating = False
                    self.update_message = (f"The update failed: {data}\nClick UPDATE NOW to "
                                           "try again.", True)
                    self._refresh()
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _session_tick(self) -> None:
        if self.worker is None and not self.blocked and self.session == "logged_in":
            self._start("check")
        self.root.after(SESSION_RECHECK_MS, self._session_tick)

    def _on_callback_error(self, exc, value, tb) -> None:
        text = "".join(traceback.format_exception(exc, value, tb))
        write_crash_log(text)
        self._log(text)
        self._say(f"Something went wrong: {value}. Details are in the log.", error=True)
        self._refresh()

    # ============================================================
    # WINDOW CONTROLS
    # ============================================================
    def _on_close(self) -> None:
        if self.updating:
            return
        if self.worker is not None and self.worker.mode == "run":
            self.confirming = True
            self.overlay.place_forget()
            self.confirm.place(x=0, y=self.bar_h + 1, relwidth=1.0, relheight=1.0,
                               height=-(self.bar_h + 1))
            self.confirm.lift()
            return
        self._force_close()

    def _cancel_close(self) -> None:
        self.confirming = False
        self.confirm.place_forget()
        self._refresh()

    def _force_close(self) -> None:
        if self.worker is not None:
            self.worker.kill()
        self.root.destroy()

    def _minimize(self) -> None:
        self.root.iconify()

    def _toggle_max(self) -> None:
        if self.maximized and self.normal_rect:
            winframe.set_rect(self.root, *self.normal_rect)
            self.maximized = False
            self.btn_max.set_glyph(ui.GLYPH_MAX)
            return
        area = winframe.work_area(self.root)
        if area is None:
            self.root.state("zoomed")
        else:
            self.normal_rect = winframe.get_rect(self.root)
            winframe.set_rect(self.root, *area)
        self.maximized = True
        self.btn_max.set_glyph(ui.GLYPH_RESTORE)

    def _drag(self, _event) -> None:
        if not self.maximized:
            winframe.start_drag(self.root)

    def _resize_start(self, e) -> None:
        self._resize_from = (e.x_root, e.y_root, *winframe.get_rect(self.root))

    def _resize_move(self, e) -> None:
        x0, y0, wx, wy, w0, h0 = self._resize_from
        w = max(self.px(760), w0 + e.x_root - x0)
        h = max(self.px(520), h0 + e.y_root - y0)
        winframe.set_rect(self.root, wx, wy, w, h)
        self.maximized = False
        self.btn_max.set_glyph(ui.GLYPH_MAX)


def write_crash_log(text: str) -> None:
    try:
        paths.CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(paths.CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S}  v{paths.read_version()} ===\n{text}")
    except OSError:
        pass


def main() -> None:
    winframe.enable_dpi_awareness()
    winframe.set_app_id(APP_ID)

    # One window per install: two would fight over the one Chrome profile.
    tag = hashlib.sha1(str(paths.ROOT).lower().encode("utf-8")).hexdigest()[:12]
    instance = winframe.SingleInstance(f"Local\\IXM-VAS-{tag}")
    if instance.already_running:
        winframe.focus_existing(TITLE)
        return

    try:
        root = tk.Tk()
        root.configure(bg=ui.BG)
        root.attributes("-alpha", 0.0)       # no flash of the stock title bar
        app = App(root, instance)
        winframe.strip_frame(root, *app.initial_rect())
        winframe.round_corners(root, ui.OUTLINE)
        root.attributes("-alpha", 1.0)
        root.mainloop()
    except Exception:
        text = traceback.format_exc()
        write_crash_log(text)
        try:
            messagebox.showerror(TITLE, f"IXM-VAS could not start.\n\n{text[-1500:]}")
        except Exception:
            pass
        raise
    finally:
        instance.release()
