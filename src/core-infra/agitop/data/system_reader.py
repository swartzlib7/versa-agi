"""
System reader — CRON state, disk, memory, processes.
"""

import os
import shutil
import subprocess
from typing import Optional


class SystemReader:
    """Reads system-level infrastructure state."""

    def __init__(self, coa_user: str = "coa", watchdog_user: str = "watchdog"):
        self.coa_user = coa_user
        self.watchdog_user = watchdog_user

    def get_cpu_usage(self) -> str:
        """Get current CPU utilization using psutil if available."""
        try:
            import psutil
            pct = psutil.cpu_percent(interval=0.1)
            cores = psutil.cpu_count(logical=True) or 0
            freq = psutil.cpu_freq()
            speed_ghz = (freq.current / 1000.0) if freq else 0.0
            if speed_ghz:
                return f"{pct:.0f}% · {cores} cores @ {speed_ghz:.1f}GHz"
            return f"{pct:.0f}% · {cores} cores"
        except ImportError:
            return "--"

    @staticmethod
    def _format_bytes_gb(num_bytes: float) -> str:
        gb = num_bytes / (1024 ** 3)
        if gb >= 100:
            return f"{gb:.0f}G"
        if gb >= 10:
            return f"{gb:.1f}G"
        return f"{gb:.2f}G"

    def get_disk_free(self) -> str:
        """Get free and total disk space on root partition."""
        try:
            usage = shutil.disk_usage("/")
            free = self._format_bytes_gb(usage.free)
            total = self._format_bytes_gb(usage.total)
            return f"{free} free / {total}"
        except Exception:
            return "--"

    def get_memory_free(self) -> str:
        """Get available and total memory."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            avail = self._format_bytes_gb(mem.available)
            total = self._format_bytes_gb(mem.total)
            return f"{avail} free / {total}"
        except ImportError:
            pass
        try:
            result = subprocess.run(
                ["free", "-m"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.splitlines():
                if line.startswith("Mem:"):
                    parts = line.split()
                    total_mb = int(parts[1]) if len(parts) >= 2 else 0
                    avail_mb = int(parts[6]) if len(parts) >= 7 else int(parts[3])
                    total = self._format_bytes_gb(total_mb * 1024 * 1024)
                    avail = self._format_bytes_gb(avail_mb * 1024 * 1024)
                    return f"{avail} free / {total}"
        except Exception:
            pass
        return "--"

    def is_cron_enabled(self) -> bool:
        """Check if watchdog CRON is active."""
        def check_output(out: str) -> bool:
            for line in out.splitlines():
                line = line.strip()
                if line and "lifeline" in line.lower() and not line.startswith("#"):
                    return True
            return False

        try:
            result = subprocess.run(
                ["crontab", "-u", self.watchdog_user, "-l"],
                capture_output=True, text=True, timeout=3,
            )
            if check_output(result.stdout):
                return True
        except Exception:
            pass

        # May need sudo — try without -u
        try:
            result = subprocess.run(
                ["sudo", "crontab", "-u", self.watchdog_user, "-l"],
                capture_output=True, text=True, timeout=5,
            )
            if check_output(result.stdout):
                return True
        except Exception:
            pass
            
        return False

    def get_cron_interval(self) -> Optional[str]:
        """Get the CRON schedule expression."""
        try:
            result = subprocess.run(
                ["sudo", "crontab", "-u", self.watchdog_user, "-l"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "lifeline" in line.lower():
                    # Return the cron schedule part (first 5 fields)
                    parts = line.split()
                    if len(parts) >= 5:
                        return " ".join(parts[:5])
        except Exception:
            pass
        return None

    def toggle_cron(self) -> bool:
        """Toggle CRON lifeline schedule. Returns new state (True=enabled)."""
        try:
            result = subprocess.run(
                ["crontab", "-u", self.watchdog_user, "-l"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return self.is_cron_enabled()

            is_on = self.is_cron_enabled()
            new_lines = []
            for line in result.stdout.splitlines():
                if "lifeline" in line.lower():
                    if is_on and not line.startswith("#"):
                        # Disable: comment out with plain #
                        line = "#" + line
                    elif not is_on and line.startswith("#"):
                        # Enable: strip leading # (one character)
                        line = line[1:]
                new_lines.append(line)

            new_content = "\n".join(new_lines) + "\n"
            write_result = subprocess.run(
                ["crontab", "-u", self.watchdog_user, "-"],
                input=new_content, capture_output=True, text=True, timeout=5,
            )
            if write_result.returncode != 0:
                return self.is_cron_enabled()
            return not is_on
        except Exception:
            return self.is_cron_enabled()

    def kill_agents(self, os_users: Optional[list[str]] = None) -> None:
        """Kill all LangGraph harness processes across agent OS users.

        Args:
            os_users: OS usernames to target. Defaults to [self.coa_user].
        """
        targets = os_users or [self.coa_user]
        for user in targets:
            try:
                subprocess.run(
                    ["pkill", "-f", "harness.agent_harness", "-u", user],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
                
        try:
            # Reset zombie 'active' statuses using native Python sqlite3
            # (the sqlite3 CLI fails with 'readonly database' due to WAL journal permissions)
            import os, sqlite3
            db_path = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE agents SET status='idle' WHERE status='active';")
            conn.commit()
            conn.close()
        except Exception:
            pass

    def is_agent_process_running(self) -> bool:
        """Check if a LangGraph harness process is running for the agent user."""
        try:
            result = subprocess.run(
                ["pgrep", "-u", self.coa_user, "-f", "harness.agent_harness"],
                capture_output=True, text=True, timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def is_sentinel_running(self) -> bool:
        """Check if the Sentinel file watcher (inotifywait) is running."""
        try:
            result = subprocess.run(
                ["pgrep", "-u", self.watchdog_user, "-f", "inotifywait"],
                capture_output=True, text=True, timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_uptime(self) -> str:
        """Get system uptime."""
        try:
            result = subprocess.run(
                ["uptime", "-p"],
                capture_output=True, text=True, timeout=3,
            )
            text = result.stdout.strip()
            # "up 3 days, 14 hours, 22 minutes" → "3d 14h"
            text = text.replace("up ", "")
            parts = []
            for segment in text.split(","):
                segment = segment.strip()
                if "day" in segment:
                    parts.append(segment.split()[0] + "d")
                elif "hour" in segment:
                    parts.append(segment.split()[0] + "h")
                elif "minute" in segment:
                    parts.append(segment.split()[0] + "m")
            return " ".join(parts) if parts else text
        except Exception:
            return "--"

    def is_logging_enabled(self) -> bool:
        """Check if lifeline logging is enabled via paths.env."""
        paths_env = "/etc/versa-agi/paths.env"
        try:
            with open(paths_env, "r") as f:
                for line in f:
                    if line.startswith("VERSA_LOGGING_ENABLED="):
                        return line.strip().split("=", 1)[1].strip('"').lower() == "true"
        except Exception:
            pass
        return True  # default enabled

    def toggle_logging(self) -> bool:
        """Toggle VERSA_LOGGING_ENABLED in paths.env. Returns new state."""
        paths_env = "/etc/versa-agi/paths.env"
        current = self.is_logging_enabled()
        new_val = "false" if current else "true"
        try:
            # Check if variable exists in file
            result = subprocess.run(
                ["sudo", "grep", "-q", "^VERSA_LOGGING_ENABLED=", paths_env],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                # Variable exists — replace in-place
                subprocess.run(
                    ["sudo", "sed", "-i",
                     f"s/^VERSA_LOGGING_ENABLED=.*/VERSA_LOGGING_ENABLED=\"{new_val}\"/",
                     paths_env],
                    capture_output=True, timeout=5,
                )
            else:
                # Variable missing — append it
                subprocess.run(
                    ["sudo", "bash", "-c",
                     f"echo 'VERSA_LOGGING_ENABLED=\"{new_val}\"' >> {paths_env}"],
                    capture_output=True, timeout=5,
                )
            return not current
        except Exception:
            return current

    def get_cooldown_status(self) -> Optional[dict]:
        """Check if any agent is in API cooldown.

        Reads /tmp/versa_agi_{agent}.cooldown marker files written by
        lifeline.sh's rate-limit handler. Each file contains a Unix epoch
        timestamp indicating when the agent may resume.

        Returns:
            dict with 'type' ('quota' or 'rate_limit'), 'agent', and
            'remaining_seconds', or None if no cooldown is active.
        """
        import glob
        import time as _time

        now = int(_time.time())
        worst: Optional[dict] = None

        for path in glob.glob("/tmp/versa_agi_*.cooldown"):
            try:
                with open(path) as f:
                    resume_at = int(f.read().strip())
                if resume_at <= now:
                    continue  # expired

                remaining = resume_at - now
                # Extract agent name from filename: versa_agi_{name}.cooldown
                filename = path.rsplit("/", 1)[-1]
                agent_name = filename.replace("versa_agi_", "").replace(".cooldown", "")

                # Determine tier: ≥ 1800s remaining ≈ daily quota, else rate limit
                cooldown_type = "quota" if remaining >= 1800 else "rate_limit"

                entry = {
                    "type": cooldown_type,
                    "agent": agent_name,
                    "remaining_seconds": remaining,
                }

                # Keep the worst (longest) cooldown for display
                if worst is None or remaining > worst["remaining_seconds"]:
                    worst = entry
            except (ValueError, OSError):
                continue

        return worst

    def is_inference_endpoint_running(self) -> bool:
        """Check if Inference Endpoint is available.

        Handles multiple backends:
          - Ollama (standard/remote): 'ollama serve' process
          - Intel SYCL: llama-server process
          - Remote: HTTP reachability check on VERSA_INFERENCE_URL
        """
        # Check for local Ollama process
        try:
            result = subprocess.run(
                ["pgrep", "-f", "ollama serve"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # Check for Intel SYCL / llama-server process (legacy pgrep name retained)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "llama-server"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # Fallback: HTTP health check on configured inference URL (handles remote backends)
        inference_url = self._read_paths_env("VERSA_INFERENCE_URL", "")
        if inference_url:
            try:
                import urllib.request
                req = urllib.request.Request(inference_url, method="GET")
                urllib.request.urlopen(req, timeout=3)
                return True
            except Exception:
                pass

        return False

    def is_local_ai_enabled(self) -> bool:
        """Check VERSA_LOCAL_AI_ENABLED in paths.env."""
        return self._read_paths_env("VERSA_LOCAL_AI_ENABLED", "false").lower() == "true"

    def get_execution_mode(self) -> str:
        """Get execution mode from paths.env: cloud, local, or hybrid."""
        return self._read_paths_env("VERSA_EXECUTION_MODE", "cloud")

    def get_gpu_backend(self) -> str:
        """Get GPU backend from paths.env: standard, intel, or remote."""
        return self._read_paths_env("VERSA_GPU_BACKEND", "standard")

    def get_topology(self) -> str:
        """Get deployment topology: local, server, or client.

        Reads from setup.ini [local_ai] topology key.
        Falls back to 'local' if not configured.
        """
        ini_path = "/etc/versa-agi/setup.ini"
        if not os.path.isfile(ini_path):
            return "local"
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(ini_path)
            return cfg.get("local_ai", "topology", fallback="local")
        except Exception:
            return "local"

    def get_local_models(self) -> list[str]:
        """Get comma-separated local model list from paths.env."""
        raw = self._read_paths_env("VERSA_LOCAL_MODELS", "")
        return [m.strip() for m in raw.split(",") if m.strip()] if raw else []

    def get_cloud_models(self) -> list[str]:
        """Get comma-separated cloud model list from paths.env."""
        raw = self._read_paths_env("VERSA_CLOUD_MODELS", "")
        return [m.strip() for m in raw.split(",") if m.strip()] if raw else []

    def get_third_party_models(self) -> list[str]:
        """Get comma-separated cloud third-party model list from paths.env."""
        raw = self._read_paths_env("VERSA_THIRD_PARTY_MODELS", "")
        return [m.strip() for m in raw.split(",") if m.strip()] if raw else []

    def is_third_party_enabled(self) -> bool:
        """Check VERSA_THIRD_PARTY_ENABLED in paths.env."""
        return self._read_paths_env("VERSA_THIRD_PARTY_ENABLED", "false").lower() == "true"

    def get_loading_strategy(self) -> str:
        """Get model loading strategy from paths.env: 'single' or 'router'.

        'router' — all downloaded models are available; the server loads on demand.
        'single' — one model loaded at a time (legacy SYCL behaviour).
        """
        return self._read_paths_env("VERSA_MODEL_LOADING_STRATEGY", "single")

    def get_active_local_model(self) -> str:
        """Get the single active (VRAM-resident) local model from paths.env.

        Only meaningful when model_loading_strategy=single. In router mode
        all models are available and this returns empty string.

        Written by ``agictl model activate`` and synced by
        ``agictl model refresh`` on client topologies.

        Returns empty string if not set or in router mode.
        """
        if self.get_loading_strategy() == "router":
            return ""  # No single active model in router mode
        return self._read_paths_env("VERSA_ACTIVE_LOCAL_MODEL", "")

    def get_coa_approved_models(self) -> list[str]:
        """Get COA-approved model allowlist from paths.env.

        Only these models are selectable for the COA agent in the dashboard.
        Falls back to the full cloud models list if not configured.
        """
        raw = self._read_paths_env("VERSA_COA_APPROVED_MODELS", "")
        if raw:
            return [m.strip() for m in raw.split(",") if m.strip()]
        return self.get_cloud_models()

    def get_tunnel_host(self) -> str:
        """Get the remote inference server hostname from client_config.json.

        Only meaningful when topology=client. Returns empty string if
        not configured or file missing.
        """
        import json
        cfg_path = "/etc/versa-agi/client_config.json"
        try:
            with open(cfg_path, "r") as f:
                cfg = json.load(f)
            return cfg.get("tunnel_host", "")
        except Exception:
            return ""

    def get_watchdog_ssh_key(self) -> str:
        """Get the SSH key path for the watchdog user.

        This key is used by the SSH tunnel and for remote command
        execution on inference servers (client topology).
        """
        return f"/home/{self.watchdog_user}/.ssh/versa_agi_ed25519"

    def _read_paths_env(self, key: str, default: str = "") -> str:
        """Read a single key from /etc/versa-agi/paths.env."""
        paths_env = "/etc/versa-agi/paths.env"
        try:
            with open(paths_env, "r") as f:
                for line in f:
                    if line.startswith(f"{key}="):
                        return line.strip().split("=", 1)[1].strip('"')
        except Exception:
            pass
        return default
