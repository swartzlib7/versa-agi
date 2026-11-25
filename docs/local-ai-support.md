# Local AI support

Local AI is **not one stack**. Chat and media use different Providers, stores, and binaries.

A capability on a model card is not product support. Support is an exact ModelDriver binding (`◆`) on that catalog key.

Architecture notes (same facts): [System Design §4.5.0](../design/Versa%20AGi%20-%20System%20Design.md). Add and activate: [Models](models.md).

## Three runtimes

### Ollama

<img src="https://img.shields.io/badge/Ollama-111111?style=flat-square&logo=ollama&logoColor=white" alt="Ollama">
<img src="https://img.shields.io/badge/NVIDIA-76B900?style=flat-square&logo=nvidia&logoColor=white" alt="NVIDIA">
<img src="https://img.shields.io/badge/AMD-ED1C24?style=flat-square&logo=amd&logoColor=white" alt="AMD">

| | |
|---|---|
| **Job** | Chat — Ollama library tags |
| **Install** | Setup: **Standard Ollama**, then `agictl model add <tag>` |
| **Do not use for** | Paint, video, raw Hugging Face GGUF dumps |

### SYCL (llama-server)

<img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
<img src="https://img.shields.io/badge/Intel-0071C5?style=flat-square&logo=intel&logoColor=white" alt="Intel">

| | |
|---|---|
| **Job** | Chat — GGUF; optional projector to *see* images |
| **GPU** | Intel ARC |
| **Install** | Setup: **Intel SYCL**, then `agictl model sycl import` / `model activate` |
| **Do not use for** | Paint, video, media GGUFs |

Verified on **Ubuntu 24.04** only.

### sd-cli (stable-diffusion.cpp)

<img src="https://img.shields.io/badge/Intel-0071C5?style=flat-square&logo=intel&logoColor=white" alt="Intel">
<img src="https://img.shields.io/badge/NVIDIA-76B900?style=flat-square&logo=nvidia&logoColor=white" alt="NVIDIA">

| | |
|---|---|
| **Job** | Media — Utility paint / video |
| **GPU** | Intel SYCL today; NVIDIA CUDA planned |
| **Install** | `agictl model media import` / `generate` (Provider `local_media`) |
| **Do not use for** | Chat, Ollama, llama-server |

## What works where

| What you want | Cloud (`◆` keys) | Ollama | SYCL chat | sd-cli media |
|---|---|---|---|---|
| Agent chat (text) | Yes | **Supported** | **Supported** | No |
| See an image in chat | Bound keys | Not yet | **Supported** on `qwen3.6:35b`, `qwen3.8:27b` | No |
| See video / hear audio in chat | Some keys / often not | No | No | No |
| Paint a still (Utility) | Some keys | No | No | **Supported** — Qwen-Image-2512, FLUX.1-dev |
| Generate video (Utility) | Some keys | No | No | **Experimental** (parked) — see below |
| Site TTS | Some keys | No | No | No |

**Utility video (parked).** The LTX-2.5 Distilled stack works at a short ~640 clip. Usable 720p-class needs **>64 GB VRAM**. Not supported on 32 GB / 64 GB.

## Weights and topology

Weights stay on the GPU host (`local` or `server`). A laptop **client** never downloads them — import on the server, then `sudo agictl model refresh`.

NVIDIA / AMD (including a future high-VRAM box) keeps **Ollama for chat** and **`local_media` + CUDA sd-cli for paint/video**. LTX and Flux are never `ollama pull`.

## Related

- [Models](models.md) — catalog, Import, first-login
- [Operations](operations.md) — agitop
- [Troubleshooting](troubleshooting.md)
