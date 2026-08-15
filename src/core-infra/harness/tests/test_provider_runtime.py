"""Unit tests for TD-DRIVER-001 MD-1 catalog-driven Provider routing."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)

from harness.model_params import detect_provider_family, to_native_kwargs  # noqa: E402
from model_catalog import load_catalog, load_providers  # noqa: E402
from provider_runtime import (  # noqa: E402
    ProviderRuntimeError,
    create_google_genai_client,
    create_langchain_client,
    create_openai_sdk_client,
    resolve_provider_api_key,
    resolve_provider_route,
)

REPOSITORY_MODELS_INI = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")
MODELS_INI = REPOSITORY_MODELS_INI if os.path.isfile(REPOSITORY_MODELS_INI) else None


PROVIDERS = {
    "google": {"cls": "ChatGoogleGenerativeAI", "enabled": True},
    "openai": {"cls": "ChatOpenAI", "enabled": True},
    "anthropic": {"cls": "ChatAnthropic", "enabled": True},
    "xai": {"cls": "ChatOpenAI", "enabled": True},
    "openrouter": {"cls": "ChatOpenAI", "enabled": True},
    "ollama": {"cls": "ChatOllama", "enabled": True},
    "llamacpp": {"cls": "ChatOpenAI", "enabled": True},
}


def _catalog_row(provider: str) -> dict:
    return {
        "provider": provider,
        "enabled": True,
        "input_modalities": "text",
        "output_modalities": "text",
    }


class TestExactProviderRoutes(unittest.TestCase):
    def test_provider_slug_not_model_shape_selects_cloud_transport(self):
        cases = {
            "openai/gpt-looking-google-model": (
                "google",
                "ChatGoogleGenerativeAI",
                "google-generativeai",
            ),
            "gemini-looking-openai-model": (
                "openai",
                "ChatOpenAI",
                "https://api.openai.com/v1",
            ),
            "grok-looking-anthropic-model": (
                "anthropic",
                "ChatAnthropic",
                "https://api.anthropic.com",
            ),
            "claude-looking-xai-model": (
                "xai",
                "ChatOpenAI",
                "https://api.x.ai/v1",
            ),
            "plain-openrouter-model": (
                "openrouter",
                "ChatOpenAI",
                "https://openrouter.ai/api/v1",
            ),
        }
        catalog = {
            key: _catalog_row(expected[0])
            for key, expected in cases.items()
        }
        for key, (slug, client, endpoint) in cases.items():
            with self.subTest(key=key):
                route = resolve_provider_route(
                    key,
                    catalog=catalog,
                    providers=PROVIDERS,
                )
                self.assertEqual(route.provider_slug, slug)
                self.assertEqual(route.client_type, client)
                self.assertEqual(route.endpoint, endpoint)
                self.assertEqual(route.api_model, key)
                self.assertFalse(route.local)

    def test_openrouter_headers_are_route_metadata(self):
        route = resolve_provider_route(
            "plain",
            catalog={"plain": _catalog_row("openrouter")},
            providers=PROVIDERS,
        )
        self.assertEqual(route.default_headers["X-Title"], "Versa AGi")

    def test_unknown_catalog_model_fails_cleanly(self):
        with self.assertRaises(ProviderRuntimeError) as raised:
            resolve_provider_route(
                "missing",
                catalog={},
                providers=PROVIDERS,
            )
        self.assertEqual(raised.exception.code, "invalid_model")

    def test_absent_provider_fails_cleanly(self):
        with self.assertRaises(ProviderRuntimeError) as raised:
            resolve_provider_route(
                "model",
                catalog={"model": _catalog_row("absent")},
                providers=PROVIDERS,
            )
        self.assertEqual(raised.exception.code, "provider_missing")

    def test_provider_class_mismatch_is_rejected(self):
        providers = {**PROVIDERS, "openai": {"cls": "ChatAnthropic"}}
        with self.assertRaises(ProviderRuntimeError) as raised:
            resolve_provider_route(
                "model",
                catalog={"model": _catalog_row("openai")},
                providers=providers,
            )
        self.assertEqual(raised.exception.code, "provider_invalid")

    def test_unsupported_provider_is_rejected(self):
        providers = {**PROVIDERS, "custom": {"cls": "CustomClient"}}
        with self.assertRaises(ProviderRuntimeError) as raised:
            resolve_provider_route(
                "model",
                catalog={"model": _catalog_row("custom")},
                providers=providers,
            )
        self.assertEqual(raised.exception.code, "provider_unsupported")

    def test_local_routes_preserve_deployment_endpoint_and_sycl_api_id(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("[sycl_models]\nlocal-model = model.gguf, served-model.gguf\n")
            ini_path = handle.name
        try:
            llama = resolve_provider_route(
                "local-model",
                catalog={"local-model": _catalog_row("llamacpp")},
                providers=PROVIDERS,
                gpu_backend="intel",
                inference_url="http://inference:8080/",
                models_ini_path=ini_path,
            )
            ollama = resolve_provider_route(
                "ollama-model",
                catalog={"ollama-model": _catalog_row("ollama")},
                providers=PROVIDERS,
                gpu_backend="standard",
                inference_url="http://ollama:11434/",
            )
        finally:
            os.unlink(ini_path)

        self.assertTrue(llama.local)
        self.assertEqual(llama.endpoint, "http://inference:8080/v1")
        self.assertEqual(llama.api_model, "served-model")
        self.assertEqual(llama.client_type, "ChatOpenAI")
        self.assertEqual(ollama.endpoint, "http://ollama:11434")
        self.assertEqual(ollama.api_model, "ollama-model")
        self.assertEqual(ollama.client_type, "ChatOllama")


class TestShippedCatalogRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(MODELS_INI)
        cls.providers = load_providers(MODELS_INI)

    def test_each_cloud_provider_has_an_exact_executable_route(self):
        expected_clients = {
            "google": "ChatGoogleGenerativeAI",
            "openai": "ChatOpenAI",
            "anthropic": "ChatAnthropic",
            "xai": "ChatOpenAI",
            "openrouter": "ChatOpenAI",
        }
        for slug, client_type in expected_clients.items():
            keys = [
                key
                for key, model in self.catalog.items()
                if model.get("provider") == slug
            ]
            self.assertTrue(keys, f"no shipped catalog Model for Provider '{slug}'")
            with self.subTest(provider=slug, catalog_key=keys[0]):
                route = resolve_provider_route(
                    keys[0],
                    catalog=self.catalog,
                    providers=self.providers,
                )
                self.assertEqual(route.provider_slug, slug)
                self.assertEqual(route.client_type, client_type)

    def test_bound_input_models_use_their_exact_catalog_provider_routes(self):
        cases = {
            "gpt-5.5-2026-04-23": (
                "openai",
                "ChatOpenAI",
                "https://api.openai.com/v1",
            ),
            "gemini-3.1-flash-lite": (
                "google",
                "ChatGoogleGenerativeAI",
                "google-generativeai",
            ),
            "grok-4.5": (
                "xai",
                "ChatOpenAI",
                "https://api.x.ai/v1",
            ),
            "openai/gpt-5.6-luna": (
                "openrouter",
                "ChatOpenAI",
                "https://openrouter.ai/api/v1",
            ),
        }
        for catalog_key, expected in cases.items():
            with self.subTest(catalog_key=catalog_key):
                route = resolve_provider_route(
                    catalog_key,
                    catalog=self.catalog,
                    providers=self.providers,
                )
                self.assertEqual(
                    (route.provider_slug, route.client_type, route.endpoint),
                    expected,
                )
                self.assertEqual(route.api_model, catalog_key)


class TestRuntimeKeysAndClients(unittest.TestCase):
    def test_key_resolution_prefers_environment(self):
        self.assertEqual(
            resolve_provider_api_key(
                "openai",
                environ={"OPENAI_API_KEY": "env-key"},
                key_files=[],
            ),
            "env-key",
        )

    def test_key_resolution_reads_exported_key_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("export XAI_API_KEY='file-key'\n")
            path = handle.name
        try:
            value = resolve_provider_api_key(
                "xai",
                environ={},
                key_files=[path],
            )
        finally:
            os.unlink(path)
        self.assertEqual(value, "file-key")

    def test_langchain_openrouter_client_uses_resolved_route(self):
        route = resolve_provider_route(
            "plain-name",
            catalog={"plain-name": _catalog_row("openrouter")},
            providers=PROVIDERS,
        )
        fake_module = types.ModuleType("langchain_openai")

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_module.ChatOpenAI = FakeChatOpenAI
        with patch.dict(sys.modules, {"langchain_openai": fake_module}):
            client = create_langchain_client(
                route,
                native_params={"temperature": 0.3},
                key_resolver=lambda slug: f"{slug}-key",
            )
        self.assertEqual(client.kwargs["model"], "plain-name")
        self.assertEqual(
            client.kwargs["base_url"],
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(client.kwargs["api_key"], "openrouter-key")
        self.assertEqual(client.kwargs["temperature"], 0.3)
        self.assertEqual(client.kwargs["default_headers"]["X-Title"], "Versa AGi")

    def test_raw_openai_client_uses_provider_route(self):
        route = resolve_provider_route(
            "not-a-grok-name",
            catalog={"not-a-grok-name": _catalog_row("xai")},
            providers=PROVIDERS,
        )
        fake_module = types.ModuleType("openai")

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_module.OpenAI = FakeOpenAI
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = create_openai_sdk_client(
                route,
                key_resolver=lambda slug: f"{slug}-key",
            )
        self.assertEqual(client.kwargs["base_url"], "https://api.x.ai/v1")
        self.assertEqual(client.kwargs["api_key"], "xai-key")

    def test_missing_key_is_normalized(self):
        route = resolve_provider_route(
            "model",
            catalog={"model": _catalog_row("openai")},
            providers=PROVIDERS,
        )
        fake_module = types.ModuleType("langchain_openai")
        fake_module.ChatOpenAI = object
        with patch.dict(sys.modules, {"langchain_openai": fake_module}):
            with self.assertRaises(ProviderRuntimeError) as raised:
                create_langchain_client(route, key_resolver=lambda slug: "")
        self.assertEqual(raised.exception.code, "no_key")

    def test_google_genai_client_uses_resolved_key(self):
        route = resolve_provider_route(
            "gemini-image",
            catalog={"gemini-image": _catalog_row("google")},
            providers=PROVIDERS,
        )
        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        genai_module.Client = FakeClient
        google_module.genai = genai_module
        with patch.dict(
            sys.modules,
            {"google": google_module, "google.genai": genai_module},
        ):
            client = create_google_genai_client(
                route,
                key_resolver=lambda slug: f"{slug}-key",
            )
        self.assertEqual(client.kwargs["api_key"], "google-key")

    def test_non_google_transport_rejects_google_genai_client(self):
        route = resolve_provider_route(
            "model",
            catalog={"model": _catalog_row("openrouter")},
            providers=PROVIDERS,
        )
        with self.assertRaises(ProviderRuntimeError) as raised:
            create_google_genai_client(route, key_resolver=lambda slug: "key")
        self.assertEqual(raised.exception.code, "provider_unsupported")

    def test_non_openai_transport_rejects_raw_openai_client(self):
        route = resolve_provider_route(
            "model",
            catalog={"model": _catalog_row("google")},
            providers=PROVIDERS,
        )
        with self.assertRaises(ProviderRuntimeError) as raised:
            create_openai_sdk_client(route, key_resolver=lambda slug: "key")
        self.assertEqual(raised.exception.code, "provider_unsupported")


class TestProviderFamilyCompatibility(unittest.TestCase):
    def test_catalog_provider_precedes_model_name_fallback(self):
        self.assertEqual(
            detect_provider_family("gemini-looking", "openai"),
            "openai_compat",
        )
        self.assertEqual(
            detect_provider_family("gpt-looking", "google"),
            "google",
        )

    def test_openrouter_reasoning_shape_uses_provider_not_slash(self):
        native = to_native_kwargs(
            "openai_compat",
            "plain-name",
            {
                "temperature": 0.2,
                "reasoning_effort": "high",
                "extra": {},
            },
            provider_slug="openrouter",
        )
        self.assertEqual(native["temperature"], 0.2)
        self.assertEqual(native["extra_body"]["reasoning"]["effort"], "high")

    def test_direct_openai_reasoning_ignores_slash_in_catalog_key(self):
        native = to_native_kwargs(
            "openai_compat",
            "vendor/name",
            {
                "temperature": 0.2,
                "reasoning_effort": "high",
                "extra": {},
            },
            provider_slug="openai",
        )
        self.assertEqual(native["reasoning_effort"], "high")
        self.assertNotIn("extra_body", native)
        self.assertNotIn("temperature", native)

    def test_direct_openai_sends_explicit_none_reasoning_for_tools(self):
        native = to_native_kwargs(
            "openai_compat",
            "gpt-5.6-terra",
            {
                "temperature": 0.2,
                "reasoning_effort": "none",
                "extra": {},
            },
            provider_slug="openai",
        )
        self.assertEqual(native["reasoning_effort"], "none")
        self.assertEqual(native["temperature"], 0.2)
        self.assertNotIn("extra_body", native)

    def test_openrouter_omits_none_reasoning_body(self):
        native = to_native_kwargs(
            "openai_compat",
            "openai/gpt-5.6-terra",
            {
                "temperature": 0.2,
                "reasoning_effort": "none",
                "extra": {},
            },
            provider_slug="openrouter",
        )
        self.assertNotIn("reasoning_effort", native)
        self.assertNotIn("extra_body", native)
        self.assertEqual(native["temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
