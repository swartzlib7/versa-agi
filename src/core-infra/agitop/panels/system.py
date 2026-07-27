"""System panel — CRON, disk, memory, uptime, AI mode."""

import time
from typing import Optional
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Button

from agitop.data.system_reader import SystemReader
from agitop.data.config_reader import ConfigReader
from agitop.data.status_reader import StatusReader
from agitop.data.agent_reader import AgentReader
from agitop.widgets.atrium_display import AtriumPanel
from agitop.widgets.braille_spinner import DOTS2_INTERVAL_S, dots2_markup, parse_cycle_agent
from agitop.feature_flags import UTILITY_MODELS_UI_VISIBLE, SCRIPT_TASKS_UI_VISIBLE

_DETERMINISTIC_UI_VISIBLE = UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE


class MetricLabel(Static):
    """A single metric cell: label + value."""
    pass


class KillAgentsConfirmModal(ModalScreen):
    """Confirmation modal before killing all agent processes."""

    CSS = """
    KillAgentsConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #kill-agents-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #kill-agents-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #kill-agents-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(
        self,
        system_reader: SystemReader,
        agent_reader: Optional[AgentReader] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.system_reader = system_reader
        self.agent_reader = agent_reader

    def compose(self) -> ComposeResult:
        with Vertical(id="kill-agents-dialog"):
            yield Static("[bold red]⚠ Kill All Agents[/]\n")
            yield Static(
                "Stops every running agent harness process immediately.\n\n"
                "[bold]In-flight work may be interrupted.[/]"
            )
            with Horizontal(id="kill-agents-actions"):
                yield Button(
                    "Kill All",
                    variant="error",
                    id="btn-kill-agents-confirm",
                )
                yield Button(
                    "Close",
                    classes="dismiss-btn",
                    variant="default",
                    id="btn-kill-agents-cancel",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-kill-agents-confirm":
            os_users = None
            if self.agent_reader:
                os_users = list({
                    a["os_user"] for a in self.agent_reader.get_all_agents()
                    if a.get("os_user")
                })
            self.system_reader.kill_agents(os_users)
            self.dismiss(None)
            self.app.notify("Kill signal sent to all agents", title="Kill All")
            self.app.action_refresh_all()
        else:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class SystemPanel(Static):
    """Displays system-level infrastructure status using individual metric cells."""

    REFRESH_INTERVALS = [
        ("15s", 15), ("30s", 30), ("1m", 60), ("5m", 300),
        ("10m", 600), ("30m", 1800), ("1h", 3600),
    ]

    def __init__(self, system: SystemReader,
                 config: Optional[ConfigReader],
                 status: StatusReader, 
                 agent: Optional[AgentReader] = None, **kwargs):
        super().__init__(**kwargs)
        self.system_reader = system
        self.config_reader = config
        self.status_reader = status
        self.agent_reader = agent
        self._blink_until = 0.0
        self._spinner_tick = 0
        self._refresh_idx = 3  # Default 5m

    def compose(self) -> ComposeResult:
        with Horizontal(id="system-columns"):
            with Vertical(classes="sys-col"):
                yield Static(" [b]Machine[/b]", classes="col-header")
                yield MetricLabel(id="m-cpu")
                yield MetricLabel(id="m-disk")
                yield MetricLabel(id="m-mem")
                yield MetricLabel(id="m-up")
            with Vertical(classes="sys-col"):
                yield Static(" [b]System[/b]", classes="col-header")
                yield MetricLabel(id="m-cron")
                yield MetricLabel(id="m-inference_endpoint")
                yield MetricLabel(id="m-localai")
                yield MetricLabel(id="m-cloudproxy")
                yield MetricLabel(id="m-cooldown")
                yield MetricLabel(id="m-logging")
                yield MetricLabel(id="m-loginfo")
                yield MetricLabel(id="m-config-error")
            with Vertical(classes="sys-col"):
                yield Static(" [b]Agents[/b]", classes="col-header")
                yield MetricLabel(id="m-running")
                yield MetricLabel(id="m-count")
                yield MetricLabel(id="m-timer")
                if _DETERMINISTIC_UI_VISIBLE:
                    yield MetricLabel(id="m-util-running")
                    yield MetricLabel(id="m-util-lastrun")
            with Vertical(classes="sys-col controls-inline-col"):
                yield Static(" [b]Controls[/b]", classes="col-header")
                with Horizontal(classes="controls-btn-row"):
                    yield Button(
                        "LIFELINE: ON / OFF",
                        id="btn-cron-toggle",
                        classes="panel-btn-sm ctrl-inline-btn ctrl-lifeline",
                        tooltip=(
                            "OFF stops all Lifeline runs (CRON, Fetch, File Monitor) "
                            "— not just the schedule"
                        ),
                    )
                    yield Button(
                        "KILL ALL",
                        id="btn-kill-agents",
                        variant="error",
                        classes="panel-btn-sm ctrl-inline-btn",
                        tooltip="Kill all running agent processes",
                    )
                with Horizontal(classes="controls-btn-row"):
                    yield Button(
                        "LOG: ON / OFF",
                        id="btn-log-toggle",
                        classes="panel-btn-sm ctrl-inline-btn ctrl-log-toggle",
                    )
                    yield Button(
                        "VACUUM",
                        id="btn-vacuum",
                        classes="panel-btn-sm ctrl-inline-btn ctrl-vacuum",
                        tooltip="Vacuum system databases and reclaim space",
                    )
                with Horizontal(classes="controls-btn-row controls-refresh-row"):
                    yield Button(
                        "◀ REFRESH - 5m ▶",
                        id="btn-refresh-cycle",
                        classes="panel-btn-sm ctrl-inline-btn ctrl-refresh-span",
                    )
            with Vertical(classes="sys-col clock-col"):
                yield Static("[b]Clock[/b]", classes="col-header clock-header")
                yield Static("", id="m-clock")
                with Horizontal(classes="clock-force-row"):
                    yield Button(
                        "▶ RUN NOW",
                        id="btn-lifeline-force",
                        classes="panel-btn-sm clock-force-btn",
                        tooltip=(
                            "Run Lifeline now (skipped if already running "
                            "or within 5s of the CRON tick)"
                        ),
                    )
            with Vertical(classes="sys-col atrium-col"):
                yield AtriumPanel(id="sys-atrium", classes="sys-atrium-panel")

    def on_mount(self) -> None:
        self._clock_timer = self.set_interval(1, self._tick_clock)
        self.set_interval(DOTS2_INTERVAL_S, self._tick_running_spinner)
        self._update_refresh_label()
        self._update_control_labels()
        self.refresh_data()

    def get_refresh_seconds(self) -> int:
        return self.REFRESH_INTERVALS[self._refresh_idx][1]

    def _update_refresh_label(self) -> None:
        label, _ = self.REFRESH_INTERVALS[self._refresh_idx]
        try:
            self.query_one("#btn-refresh-cycle", Button).label = f"◀ REFRESH - {label} ▶"
        except Exception:
            pass

    def _update_control_labels(self) -> None:
        cron_on = self.system_reader.is_cron_enabled()
        log_on = self.system_reader.is_logging_enabled()
        try:
            self.query_one("#btn-cron-toggle", Button).label = (
                f"LIFELINE: {'ON' if cron_on else 'OFF'}"
            )
        except Exception:
            pass
        try:
            self.query_one("#btn-log-toggle", Button).label = (
                f"LOG: {'ON' if log_on else 'OFF'}"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-lifeline-force":
            ok, msg = self.system_reader.try_force_lifeline()
            self.app.notify(
                msg,
                title="Lifeline",
                severity="information" if ok else "warning",
            )
        elif button_id == "btn-cron-toggle":
            self.system_reader.toggle_cron()
            self._update_control_labels()
            self.refresh_data()
        elif button_id == "btn-log-toggle":
            self.system_reader.toggle_logging()
            self._update_control_labels()
            self.refresh_data()
        elif button_id == "btn-refresh-cycle":
            self._refresh_idx = (self._refresh_idx + 1) % len(self.REFRESH_INTERVALS)
            self._update_refresh_label()
            self.app.action_update_refresh_interval()
        elif button_id == "btn-kill-agents":
            self.app.push_screen(
                KillAgentsConfirmModal(self.system_reader, self.agent_reader)
            )
        elif button_id == "btn-vacuum":
            import subprocess as _sp
            try:
                result = _sp.run(
                    ["agictl", "system", "vacuum"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    import json as _json
                    data = _json.loads(result.stdout)
                    dbs = data.get("databases", [])
                    total_saved = sum(
                        float(d.get("saved", "0").replace(" KB", ""))
                        for d in dbs
                        if d.get("status") == "ok" and "KB" in str(d.get("saved", ""))
                    )
                    ok_count = sum(1 for d in dbs if d.get("status") == "ok")
                    self.app.notify(
                        f"✓ Vacuumed {ok_count} database(s) — {total_saved:.1f} KB reclaimed",
                        title="Vacuum",
                    )
                else:
                    self.app.notify(
                        f"Vacuum failed: {result.stderr[:200]}", severity="error",
                    )
            except Exception as e:
                self.app.notify(f"Vacuum error: {e}", severity="error")

    def _tick_clock(self) -> None:
        """Update clock every second — blink at :00; refresh spawn timer when harness is up."""
        from datetime import timezone
        now = datetime.now()
        utc_now = datetime.now(timezone.utc)
        local_tz = now.astimezone().strftime("%Z")
        t_str = now.strftime("%H:%M:%S")
        utc_str = utc_now.strftime("%H:%M:%S")
        is_blink = now.second == 0 and time.time() < self._blink_until + 2
        if now.second == 0:
            self._blink_until = time.time()
        # Blink effect: reverse color at :00 for 2 seconds
        if now.second < 2 or is_blink:
            clock_markup = f"\n [bold reverse #00ff00]  {local_tz} {t_str}  [/]\n [dim cyan]UTC {utc_str}[/]\n"
        else:
            clock_markup = f"\n [bold #00ff00]  {local_tz} {t_str}  [/]\n [dim cyan]UTC {utc_str}[/]\n"

        try:
            self.query_one("#m-clock").update(clock_markup)
        except Exception:
            pass

        agent_running = self.system_reader.is_agent_process_running()
        cycle_id = self.status_reader.get_current_cycle_id()
        timer_str = "--:--"
        if cycle_id and agent_running:
            try:
                epoch = int(cycle_id.rsplit("-", 1)[-1])
                elapsed = int(time.time() - epoch)
                if elapsed >= 3600:
                    timer_str = f"{elapsed // 3600}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"
                else:
                    timer_str = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
            except (ValueError, IndexError):
                pass
        self._update_spawn_label(agent_running, cycle_id, timer_str)

    def _tick_running_spinner(self) -> None:
        if not self.agent_reader:
            return
        running_count = sum(
            1 for a in self.agent_reader.get_all_agents() if a.get("status") == "active"
        )
        self._spinner_tick += 1
        self._update_util_running_label()
        self._update_util_lastrun_label()
        if running_count <= 0:
            return
        self._update_running_label(running_count)

    def _dot(self, active: bool) -> str:
        return "[bold green]●[/]" if active else "[bold red]●[/]"

    def refresh_data(self) -> None:
        """Refresh all metric cells."""
        cpu = self.system_reader.get_cpu_usage()
        disk = self.system_reader.get_disk_free()
        mem = self.system_reader.get_memory_free()
        uptime = self.system_reader.get_uptime()
        
        cron_on = self.system_reader.is_cron_enabled()
        agent_running = self.system_reader.is_agent_process_running()

        running_count = 0
        total_agents = 0
        if self.agent_reader:
            all_agents = self.agent_reader.get_all_agents()
            total_agents = len(all_agents)
            running_count = sum(1 for a in all_agents if a.get("status") == "active")
        
        # Elapsed time for the current harness spawn (cycle_id format: agent-EPOCH)
        timer_str = "--:--"
        cycle_id = self.status_reader.get_current_cycle_id()
        if cycle_id and agent_running:
            try:
                epoch = int(cycle_id.rsplit("-", 1)[-1])
                elapsed = int(time.time() - epoch)
                if elapsed >= 3600:
                    timer_str = f"{elapsed // 3600}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"
                else:
                    timer_str = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
            except (ValueError, IndexError):
                pass

        self._update_running_label(running_count)
        self._update_spawn_label(agent_running, cycle_id, timer_str)
        self.query_one("#m-count").update(f" AGENTS: [bold]{total_agents}[/]")
        self._update_util_running_label()
        self._update_util_lastrun_label()
        self.query_one("#m-cpu").update(f" CPU:  [bold]{cpu}[/]")
        self.query_one("#m-disk").update(f" DISK: [bold]{disk}[/]")
        self.query_one("#m-mem").update(f" MEM:  [bold]{mem}[/]")
        self.query_one("#m-up").update(f" UP:   [dim]{uptime}[/]")

        self.query_one("#m-cron").update(
            f" LIFELINE:  {self._dot(cron_on)} {'active' if cron_on else 'idle'}"
        )

        log_on = self.system_reader.is_logging_enabled()
        self.query_one("#m-logging").update(
            f" LOGGING:   {self._dot(log_on)} {'on' if log_on else 'off'}"
        )
        self.query_one("#m-loginfo").update(
            " [dim]↳ /var/log/versa-agi-archive/ (weekly)[/]"
        )

        # Local AI Status
        local_ai_on = self.system_reader.is_local_ai_enabled()
        exec_mode = self.system_reader.get_execution_mode()
        mode_colors = {"cloud": "cyan", "local": "green", "hybrid": "yellow"}
        mode_color = mode_colors.get(exec_mode, "dim")
        exp_tag = " [dim italic](experimental)[/]" if exec_mode in ("local", "hybrid") else ""
        self.query_one("#m-localai").update(
            f" AI MODE:   {self._dot(local_ai_on)} [{mode_color}]{exec_mode}[/{mode_color}]{exp_tag}"
        )

        # Local inference (Ollama / llama-server) — not third-party cloud.
        # Cloud providers call APIs directly (LiteLLM proxy retired); gating on
        # third-party made Mac/cloud-only installs show INFERENCE: down falsely.
        proxy_on = self.system_reader.is_third_party_enabled()
        if local_ai_on:
            inference_endpoint_on = self.system_reader.is_inference_endpoint_running()
            self.query_one("#m-inference_endpoint").update(
                f" LOCAL AI:   {self._dot(inference_endpoint_on)} {'active' if inference_endpoint_on else 'down'}"
            )
        else:
            self.query_one("#m-inference_endpoint").update("")

        # Third-party cloud providers (OpenRouter / OpenAI / Anthropic / xAI)
        if proxy_on:
            self.query_one("#m-cloudproxy").update(
                f" PROVIDERS: {self._dot(proxy_on)} enabled"
            )
        else:
            self.query_one("#m-cloudproxy").update("")

        # API Cooldown Status
        cooldown = self.system_reader.get_cooldown_status()
        if cooldown:
            remaining = cooldown["remaining_seconds"]
            if remaining >= 3600:
                time_str = f"{remaining // 3600}h {(remaining % 3600) // 60:02d}m"
            else:
                time_str = f"{remaining // 60}m {remaining % 60:02d}s"
            agent = cooldown["agent"]
            if cooldown["type"] == "quota":
                self.query_one("#m-cooldown").update(
                    f" [bold blink yellow]⚠ QUOTA[/]  [yellow]{agent} — {time_str}[/]"
                )
            else:
                self.query_one("#m-cooldown").update(
                    f" [bold blink red]⚠ 429[/]    [red]{agent} — {time_str}[/]"
                )
        else:
            self.query_one("#m-cooldown").update("")

        # Config Error Alert — check for agents with invalid_config status
        invalid_count = 0
        breaker_count = 0
        halted_count = 0
        if self.agent_reader:
            for a in self.agent_reader.get_all_agents():
                if a.get("status") == "invalid_config":
                    invalid_count += 1
                elif a.get("status") == "circuit_breaker":
                    breaker_count += 1
                elif a.get("status") == "halted":
                    halted_count += 1
        bootstrap_banner = False
        try:
            from agitop.coa_bootstrap import should_show_remind_banner

            bootstrap_banner = should_show_remind_banner()
        except Exception:
            bootstrap_banner = False

        if bootstrap_banner:
            self.query_one("#m-config-error").update(
                " [bold blink yellow]⚠ COA SETUP:[/] [yellow]Connect a provider and pick a COA model — "
                "press [b]b[/b] or open API Keys[/]"
            )
        elif invalid_count > 0:
            self.query_one("#m-config-error").update(
                f" [bold blink yellow]⚠ AGENT CONFIG ERROR:[/] [yellow]{invalid_count} agent(s) have invalid model configuration[/]"
            )
        elif breaker_count > 0:
            self.query_one("#m-config-error").update(
                f" [bold blink red]⚡ CIRCUIT BREAKER:[/] [red]{breaker_count} agent(s) auto-frozen — click agent to clear[/]"
            )
        elif halted_count > 0:
            self.query_one("#m-config-error").update(
                f" [bold red]✋ HALTED:[/] [red]{halted_count} agent(s) manually stopped — click agent to re-activate[/]"
            )
        else:
            self.query_one("#m-config-error").update("")

        self._update_control_labels()

    def _update_running_label(self, running_count: int) -> None:
        if running_count > 0:
            self.query_one("#m-running").update(
                dots2_markup(self._spinner_tick, f"RUNNING: [bold]{running_count}[/]", "cyan")
            )
        else:
            self.query_one("#m-running").update(" RUNNING: [bold]0[/]")

    def _update_util_running_label(self) -> None:
        """Show in-flight headless Utility/Script task runs (lock-file count)."""
        if not _DETERMINISTIC_UI_VISIBLE:
            return
        try:
            util_n, script_n = self.system_reader.get_deterministic_run_counts()
        except Exception:
            util_n, script_n = 0, 0
        total = util_n + script_n
        try:
            label = self.query_one("#m-util-running")
        except Exception:
            return
        if total <= 0:
            label.update(" TASKS: [bold]0[/]")
            return
        parts = []
        if util_n:
            parts.append(f"U:{util_n}")
        if script_n:
            parts.append(f"S:{script_n}")
        detail = " ".join(parts)
        label.update(
            dots2_markup(self._spinner_tick, f"TASKS: [bold]{total}[/] [dim]({detail})[/]", "magenta")
        )

    @staticmethod
    def _fmt_local_ts(utc_iso: str | None) -> str:
        """Render a stored UTC-ISO timestamp as a compact user-local string."""
        if not utc_iso:
            return "—"
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(utc_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone()
            if local.date() == datetime.now().astimezone().date():
                return local.strftime("%H:%M")
            return local.strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            return "—"

    def _update_util_lastrun_label(self) -> None:
        """Show the last completed Utility/Script run times (user-local)."""
        if not _DETERMINISTIC_UI_VISIBLE:
            return
        try:
            util_ts, script_ts = self.system_reader.get_deterministic_last_runs()
        except Exception:
            util_ts, script_ts = None, None
        try:
            label = self.query_one("#m-util-lastrun")
        except Exception:
            return
        label.update(
            f" LAST: [cyan]U[/] {self._fmt_local_ts(util_ts)} · "
            f"[cyan]S[/] {self._fmt_local_ts(script_ts)}"
        )

    def _update_spawn_label(
        self,
        agent_running: bool,
        cycle_id: str | None,
        timer_str: str,
    ) -> None:
        timer_color = "yellow" if agent_running else "dim"
        if agent_running and cycle_id:
            agent_name = parse_cycle_agent(cycle_id) or "agent"
            label = f"{agent_name}  {timer_str}"
            self.query_one("#m-timer").update(
                f" SPAWN: [{timer_color}]{label}[/{timer_color}]"
            )
        else:
            self.query_one("#m-timer").update(
                f" SPAWN: [{timer_color}]{timer_str}[/{timer_color}]"
            )
