"""agictl surfaces for the live voice call (state_live_voice_call.md).

- ``model live-call list|set|unset`` — call-capable catalog keys (set/unset: PU or root).
- ``message calls list|show`` — read-only call log (COA, PU, root).
- ``message call-bridge …`` — hidden plumbing for harness call mode only. Placing a
  call is a harness tool (D10); these commands refuse any caller that is not the
  COA harness in call mode (``VERSA_CALL_BRIDGE=harness``). Containment is by
  convention, like IDE mode: the harness never exports that variable to agent
  tool processes, and ``_run_agictl`` refuses ``call-bridge``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import click

import call_log_store
import live_call_config

BRIDGE_ENV = "VERSA_CALL_BRIDGE"
BRIDGE_ENV_VALUE = "harness"
READ_ROLES = ("coa", "watchdog")


def _caller() -> str:
    return os.getenv("AGICTL_AGENT_USER", "")


def _stdin_text() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def _stdin_json() -> dict:
    raw = _stdin_text().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def register(
    message_group,
    model_group,
    *,
    json_response,
    get_config,
    messages_db_path,
    models_ini_write_targets,
    upsert_ini_entry,
    remove_ini_entry,
    load_catalog,
):
    def _fail(error: str, **extra):
        json_response(False, error=error, **extra)
        sys.exit(1)

    def _require_reader():
        caller = _caller()
        if caller and caller not in READ_ROLES:
            _fail("The call log is available to COA only.")

    def _require_pu_or_root():
        if _caller():
            _fail("Permission denied. Only the Primary User can change call-capable models "
                  "(agitop → Model Manager, or sudo agictl model live-call …).")

    def _require_bridge():
        if os.getenv(BRIDGE_ENV) != BRIDGE_ENV_VALUE or _caller() != live_call_config.CALL_AGENT:
            _fail("Calls are placed only by the harness call tool (agictl_call_pu). "
                  "There is no terminal command for placing a call.")

    def _vv_identity():
        cfg = get_config()
        vv = cfg.get("versavoice", {}) or {}
        token = vv.get("api_token")
        sub = vv.get("sub_account_id")
        if not token or not sub:
            _fail("VersaVoice identity not configured")
        pu_uid = str((cfg.get("primary_user", {}) or {}).get("uid") or "")
        return token, sub, pu_uid

    # ── model live-call ────────────────────────────────────────────────
    @model_group.group("live-call")
    def live_call_models():
        """Call-capable catalog keys for live voice call turns."""

    @live_call_models.command("list")
    def live_call_list():
        """Shipped, site, and effective call-capable keys, plus the selectable set."""
        shipped = sorted(live_call_config.shipped_call_capable_keys())
        effective = sorted(live_call_config.call_capable_keys())
        extra = {}
        try:
            selectable = live_call_config.selectable_call_models()
        except Exception as exc:
            selectable = []
            extra["warning"] = str(exc)
        json_response(True, shipped=shipped, effective=effective, selectable=selectable,
                      settings=_settings_payload(), **extra)

    def _settings_payload() -> dict:
        s = live_call_config.read_settings()
        return {
            "enabled": s.enabled,
            "call_model": s.call_model,
            "voice_model": s.voice_model,
            "max_minutes": s.max_minutes,
            "join_timeout_seconds": s.join_timeout_seconds,
            "calls_per_cycle": s.calls_per_cycle,
        }

    def _write_site(key: str, enabled: bool):
        _require_pu_or_root()
        key = (key or "").strip()
        if not key:
            _fail("catalog key required")
        cat = load_catalog()
        if key not in cat:
            _fail(f"'{key}' is not in the live catalog")
        targets = models_ini_write_targets()
        if not targets:
            _fail("models.ini not found")
        shipped = live_call_config.shipped_call_capable_keys(targets[0])
        value = live_call_config.site_layer_value(key, enabled, shipped)
        for path in targets:
            if value is None:
                remove_ini_entry(path, live_call_config.CALL_CAPABLE_SITE_SECTION, key)
            else:
                upsert_ini_entry(path, live_call_config.CALL_CAPABLE_SITE_SECTION, key, value)
        json_response(True, key=key, call_capable=enabled,
                      effective=sorted(live_call_config.call_capable_keys(targets[0])))

    @live_call_models.command("set")
    @click.argument("key")
    def live_call_set(key):
        """Mark a catalog key call-capable (PU/root)."""
        _write_site(key, True)

    @live_call_models.command("unset")
    @click.argument("key")
    def live_call_unset(key):
        """Remove a catalog key from the call-capable set (PU/root)."""
        _write_site(key, False)

    # ── message calls (read-only) ──────────────────────────────────────
    @message_group.group("calls")
    def calls_group():
        """Read the live voice call log (COA)."""

    @calls_group.command("list")
    @click.option("--limit", type=int, default=20, show_default=True)
    def calls_list(limit):
        """Recent calls: id, time, status, reason, voice seconds."""
        _require_reader()
        rows = call_log_store.list_calls(messages_db_path(), live_call_config.CALL_AGENT, limit=max(1, min(limit, 200)))
        print(json.dumps(rows, indent=2, default=str))

    @calls_group.command("show")
    @click.argument("call_id")
    def calls_show(call_id):
        """One call with its transcript and what was done during it."""
        _require_reader()
        row = call_log_store.get_call(messages_db_path(), call_id)
        if not row:
            _fail(f"Call '{call_id}' not found")
        transcript = call_log_store.transcript_text(row.pop("transcript", []))
        delegations = row.pop("delegations", [])
        print(json.dumps({"call": row, "transcript": transcript, "delegations": delegations},
                         indent=2, default=str, ensure_ascii=False))

    # ── message call-bridge (hidden harness plumbing) ──────────────────
    @message_group.group("call-bridge", hidden=True)
    def bridge():
        """Harness call-mode plumbing (not an agent command)."""

    def _callee_uid(call_id: str) -> str:
        row = call_log_store.get_call(messages_db_path(), call_id)
        return str((row or {}).get("callee_uid") or "")

    @bridge.command("open")
    @click.option("--reason", required=True)
    @click.option("--recipient", default="", help="Connection uid. Empty rings the sponsor.")
    def bridge_open(reason, recipient):
        _require_bridge()
        gate = live_call_config.evaluate_gate(live_call_config.CALL_AGENT)
        if not gate.ok:
            _fail(f"Live Call unavailable: {gate.summary()}", code="gate")
        settings = gate.settings
        stale_after = settings.join_timeout_seconds + settings.max_minutes * 60 + 120
        call_log_store.expire_stale_open_calls(messages_db_path(), live_call_config.CALL_AGENT, stale_after)
        if call_log_store.has_open_call(messages_db_path(), live_call_config.CALL_AGENT):
            _fail("A call is already in progress.", code="busy")

        token, sub, pu_uid = _vv_identity()
        from comms import call_open

        resp = call_open(token, sub, reason, settings.join_timeout_seconds,
                         recipient_id=recipient.strip())
        if not resp:
            _fail("VersaVoice call service unreachable", code="vv_unreachable")
        if not resp.get("success"):
            _fail(resp.get("message") or resp.get("error") or "VersaVoice refused the call",
                  code="vv_refused")
        data = resp.get("data") or {}
        status = str(data.get("status") or "calling")
        if status not in ("calling", "offline"):
            status = "failed"
        call_id = str(data.get("callId") or f"local_{uuid.uuid4().hex[:12]}")
        callee_uid = str(data.get("calleeUid") or "")
        call_log_store.insert_attempt(
            messages_db_path(),
            call_id=call_id,
            agent_name=live_call_config.CALL_AGENT,
            status=status,
            reason=reason,
            pu_uid=str(data.get("puUid") or pu_uid),
            callee_uid=callee_uid,
            channel_id=str(data.get("channelId") or ""),
            cycle_id=os.getenv("VERSA_CYCLE_ID", ""),
            call_model=gate.call_model,
            voice_model=settings.voice_model,
            close_reason="no_device" if status == "offline" else None,
        )
        json_response(True, call_id=call_id, status=status, callee_uid=callee_uid,
                      callee_name=str(data.get("calleeName") or ""),
                      callee_language=str(data.get("calleeLanguage") or ""))

    @bridge.command("wait-offer")
    @click.argument("call_id")
    @click.option("--wait", "wait_seconds", type=int, default=25)
    def bridge_wait_offer(call_id, wait_seconds):
        _require_bridge()
        token, sub, _ = _vv_identity()
        from comms import call_wait_offer

        resp = call_wait_offer(token, sub, call_id, max(1, min(wait_seconds, 30)),
                               callee_uid=_callee_uid(call_id))
        if not resp or not resp.get("success"):
            err = (resp or {}).get("message") or "VersaVoice call service unreachable"
            _fail(err, code="vv_unreachable")
        data = resp.get("data") or {}
        status = str(data.get("status") or "calling")
        if status in ("declined", "missed", "ended"):
            call_log_store.update_call(
                messages_db_path(), call_id,
                status="ended" if status == "ended" else status,
                close_reason=status,
                ended_at=call_log_store.utc_now(),
            )
        json_response(True, call_id=call_id, status=status, sdp_offer=data.get("sdpOffer") or "")

    @bridge.command("status")
    @click.argument("call_id")
    def bridge_status(call_id):
        """The call document's status in VersaVoice (the phone ends calls there)."""
        _require_bridge()
        token, sub, _ = _vv_identity()
        from comms import call_status

        resp = call_status(token, sub, call_id, callee_uid=_callee_uid(call_id))
        if not resp or not resp.get("success"):
            _fail((resp or {}).get("message") or "VersaVoice call service unreachable",
                  code="vv_unreachable")
        json_response(True, call_id=call_id, status=str((resp.get("data") or {}).get("status") or ""))

    @bridge.command("answer")
    @click.argument("call_id")
    @click.option("--session-id", required=True)
    def bridge_answer(call_id, session_id):
        """Relay the OpenAI SDP answer (stdin) to the phone."""
        _require_bridge()
        sdp_answer = _stdin_text()
        if not sdp_answer.strip():
            _fail("SDP answer required on stdin")
        token, sub, _ = _vv_identity()
        from comms import call_update

        resp = call_update(token, sub, call_id, callee_uid=_callee_uid(call_id),
                           sdpAnswer=sdp_answer, status="connecting")
        if not resp or not resp.get("success"):
            _fail((resp or {}).get("message") or "VersaVoice call service unreachable", code="vv_unreachable")
        call_log_store.update_call(
            messages_db_path(), call_id,
            status="connecting",
            openai_session_id=session_id,
            joined_at=call_log_store.utc_now(),
        )
        json_response(True, call_id=call_id, status="connecting")

    @bridge.command("log")
    @click.argument("call_id")
    @click.option("--status", type=click.Choice(["connecting", "live"]), default=None)
    def bridge_log(call_id, status):
        """Snapshot transcript/delegations (stdin JSON) while the call runs."""
        _require_bridge()
        data = _stdin_json()
        if status == "live":
            token, sub, _ = _vv_identity()
            from comms import call_update

            call_update(token, sub, call_id, callee_uid=_callee_uid(call_id), status="live")
        call_log_store.update_call(
            messages_db_path(), call_id,
            status=status,
            transcript_json=data.get("transcript"),
            delegations_json=data.get("delegations"),
            voice_seconds=data.get("voice_seconds"),
        )
        json_response(True, call_id=call_id)

    @bridge.command("summary")
    @click.argument("call_id")
    def bridge_summary(call_id):
        """Save COA's post-call summary (stdin) as the call's last-call note."""
        _require_bridge()
        summary = " ".join(_stdin_text().split())[:600]
        if not summary:
            _fail("summary text required on stdin")
        if not call_log_store.update_call(messages_db_path(), call_id, summary=summary):
            _fail(f"Call '{call_id}' not found")
        json_response(True, call_id=call_id)

    @bridge.command("end")
    @click.argument("call_id")
    @click.option("--status", "final_status", type=click.Choice(["ended", "missed", "declined", "failed"]), required=True)
    @click.option("--close-reason", default="")
    @click.option("--voice-seconds", type=int, default=None)
    def bridge_end(call_id, final_status, close_reason, voice_seconds):
        """Finalize the call record in VersaVoice and the local log (stdin JSON transcript)."""
        _require_bridge()
        data = _stdin_json()
        token, sub, _ = _vv_identity()
        from comms import call_update

        call_update(
            token, sub, call_id,
            callee_uid=_callee_uid(call_id),
            status=final_status,
            closeReason=close_reason or None,
            durationSeconds=voice_seconds,
        )
        call_log_store.update_call(
            messages_db_path(), call_id,
            status=final_status,
            close_reason=close_reason or None,
            voice_seconds=voice_seconds,
            ended_at=call_log_store.utc_now(),
            transcript_json=data.get("transcript"),
            delegations_json=data.get("delegations"),
        )
        json_response(True, call_id=call_id, status=final_status)
