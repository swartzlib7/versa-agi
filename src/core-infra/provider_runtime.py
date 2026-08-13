"""Catalog-driven Provider routing and runtime client construction.

The exact catalog Model selects its Provider. Model-name prefixes and separators
never select transport, credentials, endpoints, or client classes.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from model_catalog import load_catalog, load_providers, resolve_models_ini_path

OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://versavoice.ai",
    "X-Title": "Versa AGi",
}

_PROVIDER_KEYS_ENV = "/etc/versa-agi/provider_keys.env"
_PROVIDER_KEYS_ENV_LEGACY = "/etc/versa-agi/inference_endpoint.env"
_PATHS_ENV = "/etc/versa-agi/paths.env"


@dataclass(frozen=True)
class _TransportSpec:
    client_type: str
    endpoint: str
    key_env: str = ""
    default_headers: Mapping[str, str] = field(default_factory=dict)


_CLOUD_TRANSPORTS: dict[str, _TransportSpec] = {
    "google": _TransportSpec(
        "ChatGoogleGenerativeAI",
        "google-generativeai",
        "GEMINI_API_KEY",
    ),
    "openai": _TransportSpec(
        "ChatOpenAI",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
    ),
    "anthropic": _TransportSpec(
        "ChatAnthropic",
        "https://api.anthropic.com",
        "ANTHROPIC_API_KEY",
    ),
    "xai": _TransportSpec(
        "ChatOpenAI",
        "https://api.x.ai/v1",
        "XAI_API_KEY",
    ),
    "openrouter": _TransportSpec(
        "ChatOpenAI",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        OPENROUTER_HEADERS,
    ),
}

_LOCAL_CLIENT_TYPES = {
    "ollama": "ChatOllama",
    "llamacpp": "ChatOpenAI",
}


class ProviderRuntimeError(ValueError):
    """Normalized Provider route/client failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ProviderRoute:
    """Resolved exact Model and Provider runtime route without secret material."""

    catalog_key: str
    model: dict[str, Any]
    provider_slug: str
    provider: dict[str, Any]
    client_type: str
    endpoint: str
    api_model: str
    key_env: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    gpu_backend: str = ""
    inference_url: str = ""
    local: bool = False

    @property
    def family(self) -> str:
        if self.client_type == "ChatGoogleGenerativeAI":
            return "google"
        if self.client_type == "ChatAnthropic":
            return "anthropic"
        if self.client_type == "ChatOllama":
            return "local"
        if self.client_type == "ChatOpenAI":
            return "openai_compat"
        return ""


def _read_local_paths_env(path: str = _PATHS_ENV) -> tuple[str, str]:
    """Return deployment backend and inference URL."""

    gpu_backend = "standard"
    inference_url = "http://127.0.0.1:11434"
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                if raw.startswith("VERSA_GPU_BACKEND="):
                    gpu_backend = raw.split("=", 1)[1].strip().strip('"')
                elif raw.startswith("VERSA_INFERENCE_URL="):
                    inference_url = raw.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return gpu_backend, inference_url


def _resolve_sycl_api_model(
    catalog_key: str,
    models_ini_path: str | None = None,
) -> str:
    """Map a catalog key to the llama-server API model ID."""

    path = models_ini_path or resolve_models_ini_path()
    try:
        ini = configparser.ConfigParser(delimiters=("=",))
        ini.read(path)
        if ini.has_section("sycl_models"):
            raw = ini.get("sycl_models", catalog_key, fallback="")
            if raw:
                parts = raw.strip().split(",")
                if len(parts) >= 2:
                    return parts[1].strip().removesuffix(".gguf")
    except (OSError, configparser.Error):
        pass
    return catalog_key


def resolve_provider_route(
    catalog_key: str,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
    gpu_backend: str | None = None,
    inference_url: str | None = None,
    models_ini_path: str | None = None,
) -> ProviderRoute:
    """Resolve an exact catalog Model to its Provider runtime route."""

    key = (catalog_key or "").strip()
    resolved_catalog = load_catalog() if catalog is None else catalog
    model = resolved_catalog.get(key)
    if not model:
        raise ProviderRuntimeError(
            "invalid_model",
            f"Catalog Model '{key}' was not found",
        )

    provider_slug = str(model.get("provider", "") or "").strip()
    resolved_providers = load_providers() if providers is None else providers
    provider = resolved_providers.get(provider_slug)
    if not provider:
        raise ProviderRuntimeError(
            "provider_missing",
            f"Provider '{provider_slug or '—'}' for catalog Model '{key}' was not found",
        )

    client_type = str(provider.get("cls", "") or "").strip()
    cloud = _CLOUD_TRANSPORTS.get(provider_slug)
    if cloud is not None:
        if client_type != cloud.client_type:
            raise ProviderRuntimeError(
                "provider_invalid",
                f"Provider '{provider_slug}' declares client '{client_type or '—'}'; "
                f"expected '{cloud.client_type}'",
            )
        return ProviderRoute(
            catalog_key=key,
            model=dict(model),
            provider_slug=provider_slug,
            provider=dict(provider),
            client_type=client_type,
            endpoint=cloud.endpoint,
            api_model=key,
            key_env=cloud.key_env,
            default_headers=dict(cloud.default_headers),
        )

    expected_local_client = _LOCAL_CLIENT_TYPES.get(provider_slug)
    if expected_local_client is None:
        raise ProviderRuntimeError(
            "provider_unsupported",
            f"Provider '{provider_slug}' for catalog Model '{key}' is not supported",
        )
    if client_type != expected_local_client:
        raise ProviderRuntimeError(
            "provider_invalid",
            f"Provider '{provider_slug}' declares client '{client_type or '—'}'; "
            f"expected '{expected_local_client}'",
        )

    detected_backend, detected_url = _read_local_paths_env()
    backend = (gpu_backend if gpu_backend is not None else detected_backend).strip()
    base_url = (
        inference_url if inference_url is not None else detected_url
    ).strip().rstrip("/")
    if provider_slug == "llamacpp":
        endpoint = f"{base_url}/v1"
        api_model = _resolve_sycl_api_model(key, models_ini_path)
    else:
        endpoint = base_url
        api_model = key
    return ProviderRoute(
        catalog_key=key,
        model=dict(model),
        provider_slug=provider_slug,
        provider=dict(provider),
        client_type=client_type,
        endpoint=endpoint,
        api_model=api_model,
        gpu_backend=backend,
        inference_url=base_url,
        local=True,
    )


def resolve_provider_api_key(
    provider_slug: str,
    *,
    environ: Mapping[str, str] | None = None,
    key_files: Sequence[str] | None = None,
) -> str:
    """Resolve runtime API key from inherited env then watchdog-owned stores."""

    spec = _CLOUD_TRANSPORTS.get(provider_slug)
    if spec is None or not spec.key_env:
        return ""
    environment = os.environ if environ is None else environ
    value = (environment.get(spec.key_env) or "").strip()
    if value:
        return value

    paths = (
        (_PROVIDER_KEYS_ENV, _PROVIDER_KEYS_ENV_LEGACY)
        if key_files is None
        else key_files
    )
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :].lstrip()
                    name, separator, raw_value = line.partition("=")
                    if not separator or name.strip() != spec.key_env:
                        continue
                    value = raw_value.strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError:
            continue
    return ""


def _required_api_key(
    route: ProviderRoute,
    key_resolver: Callable[[str], str],
) -> str:
    if route.local:
        return "sk-local"
    key = key_resolver(route.provider_slug)
    if key:
        return key
    raise ProviderRuntimeError(
        "no_key",
        f"{route.key_env} required for Provider '{route.provider_slug}'",
    )


def create_langchain_client(
    route: ProviderRoute,
    *,
    native_params: dict[str, Any] | None = None,
    num_ctx: int = 0,
    key_resolver: Callable[[str], str] = resolve_provider_api_key,
):
    """Create the route's LangChain client with already-translated params."""

    native = dict(native_params or {})
    if route.client_type == "ChatGoogleGenerativeAI":
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs: dict[str, Any] = {
            "model": route.api_model,
            "google_api_key": _required_api_key(route, key_resolver),
        }
        if "temperature" in native:
            kwargs["temperature"] = native["temperature"]
        if native.get("model_kwargs"):
            kwargs.update(native["model_kwargs"])
        return ChatGoogleGenerativeAI(**kwargs)

    if route.client_type == "ChatAnthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs = {
            "model": route.api_model,
            "api_key": _required_api_key(route, key_resolver),
        }
        if native.get("model_kwargs"):
            kwargs.update(native["model_kwargs"])
        elif "temperature" in native:
            kwargs["temperature"] = native["temperature"]
        return ChatAnthropic(**kwargs)

    if route.client_type == "ChatOpenAI":
        from langchain_openai import ChatOpenAI

        base: dict[str, Any] = {
            "model": route.api_model,
            "api_key": _required_api_key(route, key_resolver),
        }
        if route.provider_slug != "openai":
            base["base_url"] = route.endpoint
        if route.default_headers:
            base["default_headers"] = dict(route.default_headers)
        merged = {
            **base,
            **{key: value for key, value in native.items() if key not in base},
        }
        return ChatOpenAI(**merged)

    if route.client_type == "ChatOllama":
        from langchain_ollama import ChatOllama

        kwargs = {"base_url": route.endpoint, "model": route.api_model}
        if "temperature" in native:
            kwargs["temperature"] = native["temperature"]
        for key, value in native.items():
            if key not in (
                "temperature",
                "extra_body",
                "model_kwargs",
                "reasoning_effort",
            ):
                kwargs[key] = value
        if num_ctx > 0:
            kwargs["num_ctx"] = num_ctx
        return ChatOllama(**kwargs)

    raise ProviderRuntimeError(
        "client_unsupported",
        f"Provider client '{route.client_type or '—'}' is not supported",
    )


def create_openai_sdk_client(
    route: ProviderRoute,
    *,
    key_resolver: Callable[[str], str] = resolve_provider_api_key,
):
    """Create a raw OpenAI SDK client for compatible cloud Providers."""

    if route.local or route.provider_slug not in ("openai", "openrouter", "xai"):
        raise ProviderRuntimeError(
            "provider_unsupported",
            f"Provider '{route.provider_slug}' does not expose the raw OpenAI chat transport",
        )
    from openai import OpenAI

    return OpenAI(
        base_url=route.endpoint,
        api_key=_required_api_key(route, key_resolver),
        default_headers=dict(route.default_headers),
    )


def create_google_genai_client(
    route: ProviderRoute,
    *,
    key_resolver: Callable[[str], str] = resolve_provider_api_key,
):
    """Create the current Google Gen AI SDK client for native APIs."""

    if (
        route.local
        or route.provider_slug != "google"
        or route.client_type != "ChatGoogleGenerativeAI"
    ):
        raise ProviderRuntimeError(
            "provider_unsupported",
            f"Provider '{route.provider_slug}' does not expose native Google generateContent",
        )
    from google import genai

    return genai.Client(api_key=_required_api_key(route, key_resolver))
