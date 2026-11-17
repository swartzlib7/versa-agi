"""Source checks: the harness calls the trait, replay and refusal code in the right places.

A real cycle needs a model; these assert that the calls are there and in order.
"""

from __future__ import annotations

import os
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _source(*parts: str) -> str:
    with open(os.path.join(CORE_INFRA, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestHarnessWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source("harness", "agent_harness.py")

    def after(self, first: str, second: str) -> None:
        self.assertIn(first, self.source)
        self.assertIn(second, self.source)
        self.assertLess(self.source.index(first), self.source.index(second), (first, second))

    def after_in(self, text: str, first: str, second: str) -> None:
        self.assertIn(first, text)
        self.assertIn(second, text)
        self.assertLess(text.index(first), text.index(second), (first, second))

    def test_the_replay_guard_is_the_last_step_before_the_model(self):
        self.after("trimmed = trim_messages(verbatim, **trim_kwargs)",
                   "trimmed = replay_guard.apply(list(trimmed))")
        self.after("trimmed = [block] + list(trimmed)",
                   "trimmed = replay_guard.apply(list(trimmed))")
        self.after("trimmed = replay_guard.apply(list(trimmed))",
                   'return {"llm_input_messages": trimmed}')

    def test_call_turns_do_not_pass_through_the_cycle_models_guard(self):
        self.assertIn("if not in_call:\n            # Reasoning records", self.source)

    def test_the_guard_is_built_from_the_execution_models_traits(self):
        self.after("execution_traits = traits_for(",
                   "replay_guard = ReplayGuard(\n        execution_traits.replay, execution_model, log=tlog,")
        self.after("replay_guard = ReplayGuard(", "def pre_model_hook(state):")

    def test_the_guard_knows_the_wire_of_the_execution_model(self):
        self.assertIn(
            'wire=wire_for(_HARNESS_VIEW_CTX["provider_family"], execution_traits.api),', self.source,
        )

    def test_the_search_hint_joins_the_prompt_only_with_a_provider_search_tool(self):
        self.after("if provider_search_tools:", "enhanced_prompt += PROVIDER_SEARCH_HINT")

    def test_the_search_tool_follows_the_agents_own_reasoning_level(self):
        self.assertIn("builtin_search_tools(execution_model, agent_param_overrides)", self.source)
        self.assertIn("web_search_enabled(execution_model, agent_param_overrides)", self.source)

    def test_provider_search_tools_sit_beside_the_function_tools(self):
        self.assertIn('"tools": list(ALL_TOOLS) + provider_search_tools,', self.source)

    def test_a_binding_error_is_retried_once_without_records(self):
        self.after("if replay_guard.active and is_binding_error(e) and not binding_retry_used:",
                   "raise  # non-transient, or retry budget exhausted → crash")
        self.assertIn("replay_guard.strip_all_next()", self.source)

    def test_a_refused_server_tool_call_is_retried_once_without_provider_tool_parts(self):
        self.after("replay_guard.wire == WIRE_ANTHROPIC and is_server_tool_error(e)",
                   "raise  # non-transient, or retry budget exhausted → crash")
        self.after("and not replay_guard.server_tools_dropped", "replay_guard.drop_server_tools()")

    def test_a_declined_reply_is_removed_and_the_agent_is_told_once(self):
        self.after("_declined = refusal_reason(msg)",
                   "if stream_natural_end and declined_reply is not None:")
        self.after("if stream_natural_end and declined_reply is not None:",
                   'config, {"messages": [RemoveMessage(id=_declined_msg.id)]},')
        self.after('config, {"messages": [RemoveMessage(id=_declined_msg.id)]},',
                   "if refusals_in_row < REFUSALS_BEFORE_CLOSE:")
        self.after("if refusals_in_row < REFUSALS_BEFORE_CLOSE:",
                   "_note = HumanMessage(content=refusal_note(_declined_reason), id=str(uuid.uuid4()))")

    def test_an_ordinary_reply_resets_the_refusal_count(self):
        self.after("refusals_in_row += 1",
                   "refusals_in_row = 0\n                                declined_reply = None")

    def test_a_second_refusal_closes_the_cycle_with_the_line_lifeline_reads(self):
        self.after("if refusals_in_row < REFUSALS_BEFORE_CLOSE:",
                   "tlog(refusal_closed_line(_declined_reason, execution_model))")
        self.after("tlog(refusal_closed_line(_declined_reason, execution_model))",
                   '_close_cycle_from_harness(args.agent, refusal_stop, "REFUSAL")')

    def test_lifeline_waits_and_tells_the_primary_user_after_a_closed_refusal(self):
        lifeline = _source("lifeline.sh")
        self.assertIn('|| [ "${ALERT_KIND}" = "refusal" ]; then', lifeline)
        self.assertIn('[ "${ALERT_KIND}" = "refusal" ] && QUOTA_COOLDOWN=900', lifeline)
        self.assertIn("versa-agi-refusal-alert-${AGENT_NAME}", lifeline)

    def test_only_the_cycle_model_asks_for_the_cache(self):
        """§2.7 B16: triage, compaction and call models make one-off or foreign calls."""
        self.assertIn("llm = get_llm(execution_model, num_ctx=args.num_ctx, agent_overrides=agent_param_overrides,\n"
                      "                  prompt_cache=True,", self.source)
        self.assertEqual(self.source.count("prompt_cache=True"), 1)
        self.assertIn('"prompt": cached_system_prompt(execution_traits, enhanced_prompt),', self.source)
        # The call model shares the thread but not the cycle model's cache.
        self.assertIn("prompt=enhanced_prompt,\n                    pre_model_hook=call_pre_model_hook,", self.source)

    def test_cached_tokens_reach_the_cycle_record(self):
        self.assertIn('"cached_tokens": cache_read,', self.source)          # after every reply
        self.assertIn("cached_tokens=cache_read, cache_write_tokens=cache_write)", self.source)  # at the end
        self.assertIn("tlog(cache_line)", self.source)
        lifeline = _source("lifeline.sh")
        self.after_in(lifeline, "TOKENS_CACHED=$(jq -r '.cached_tokens // 0'", '--cached "${TOKENS_CACHED}"')

    def test_the_keep_alive_runs_in_the_chat_wait_and_its_tokens_are_counted(self):
        """§2.7 B17: built from the cycle model, its tools and what it was last sent."""
        self.after("extra_callbacks=[last_request])", "_CACHE_KEEPALIVE = KeepAlive(")
        self.assertIn('execution_traits, llm, agent_kwargs["tools"], last_request,', self.source)
        self.assertIn("close_reason = wait_for_mail(listening, _inject_midcycle_mail,\n"
                      "                                                     tick=_CACHE_KEEPALIVE.tick)", self.source)
        self.assertEqual(self.source.count("ping_in, ping_out, ping_read, ping_write = _keepalive_usage()"), 2)
        self.assertIn("cache_session=cache_session_id(args.agent, args.thread_id or \"\"),", self.source)

    def test_logs_show_reply_text_not_the_reasoning_record(self):
        self.assertIn("content = _reply_text(msg)", self.source)
        self.assertIn("final_message = _reply_text(messages[-1])", self.source)


class TestDeployLists(unittest.TestCase):
    def test_setup_deploys_model_traits_with_model_params(self):
        setup = _source("..", "setup.sh")
        self.assertIn("for hfile in model_context.py model_params.py model_traits.py; do", setup)
        self.assertIn("for hfile in model_params.py model_traits.py; do", setup)

    def test_model_params_imports_traits_from_its_own_package(self):
        self.assertIn("from . import model_traits", _source("harness", "model_params.py"))


if __name__ == "__main__":
    unittest.main()
