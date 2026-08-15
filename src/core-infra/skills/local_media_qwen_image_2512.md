# Local media — Qwen-Image-2512

> **Purpose**: How to paint with the local Qwen-Image-2512 Utility Model. Read this (or `agictl model media usage`) **before** generating.
> **Scope**: All agents (`all`)
> **Official card:** [unsloth/Qwen-Image-2512-GGUF](https://huggingface.co/unsloth/Qwen-Image-2512-GGUF)
> **Official sd.cpp guide:** [Run Qwen-Image-2512 in stable-diffusion.cpp](https://unsloth.ai/docs/models/tutorials/qwen-image-2512/stable-diffusion.cpp)

> **Harness tools:** Shell form below. In a work cycle, `agictl_utility` / `agictl_model` take the part after `agictl`.

## What this is

Catalog key **`qwen-image-2512`** is a **Utility paint** model (image out). December update of Qwen-Image. License: Apache-2.0. It is **not** a chat model and **not** a VLM. Do not activate it for agents. Do not put its GGUFs in SYCL / llama-server.

Provider: `local_media`. Runtime: pinned `sd-cli` ([stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)). Driver: `◆` on output image.

Do **not** follow the card’s ComfyUI, Diffusers, or Unsloth Studio paths. Those are upstream demos. The product path is this skill + `agictl`.

## What 2512 is good at

From the [official card](https://huggingface.co/unsloth/Qwen-Image-2512-GGUF):

- **Human realism** — faces, age, skin, hair strands; less “AI-smooth” than the August Qwen-Image.
- **Natural detail** — landscapes, water, foliage, animal fur.
- **Text in the image** — posters, slides, signs, spelled-out words. Quote the exact string in the brief.

English and Chinese briefs both work.

## Write the brief this way

The UM `system_prompt` / `--prompt` **is** the paint brief. Write a **long, specific scene**, not a three-word tag list.

Include:

1. **Subject** — who/what, age or species, expression, clothing or materials.
2. **Setting** — place, objects that must be readable, time of day.
3. **Light and camera** — indoor ambient, golden hour, phone snapshot, aerial, close-up.
4. **Any text to render** — quote it exactly (`the text "Versa AGi" on the sign`).
5. **Style** — photoreal, crayon, poster, infographic — say it once.

Unsloth’s sd.cpp sample is this shape (long scene + embedded text + lighting + viewpoint):

> Aerial drone photograph of a vast field of bright yellow wildflowers with the text "…" spelled out in deep purple lavender flowers, sharp contrast, golden hour lighting, high altitude looking straight down, photorealistic.

The official Diffusers snippet uses a **negative** prompt. Our `generate` CLI does **not** take `--negative`. Put “avoid” cues in the brief if needed (`no extra fingers, no unreadable text`).

Do not invent a second chat agent to “improve” the brief after you start a run.

## Check usage first

```bash
agictl model media usage qwen-image-2512
```

Same host facts as below. Not token/cost accounting.

## When to paint (this host, not the card)

- GPU host only (`topology=local` or `server`). Not the remote client laptop.
- Default size **768×768**. The card’s native examples are much larger (e.g. 1328²). Unsloth’s sd.cpp demo is **1024²**. On Intel B70 32 GB with llama-server up, **1024 crashed**. Use 768 unless the PU asks for 1024 and accepts `--offload` or stopping chat.
- Prefer DiT quant **Q8_0** (~21.8 GB on the card). Unsloth’s download example is **Q4_K_M** — **do not use it here**. Q4_K can paint **black** in sd.cpp. Fallback quant is **Q5_0**.
- Adapter already uses Unsloth’s sd.cpp knobs: cfg 2.5, euler, 40 steps, flow-shift 3. Do not re-tune those unless the PU asks.
- Paint does **not** use chat `models-max` / LRU. It still **shares GPU memory** with `versa-agi-sycl`. If VRAM is tight: `--offload` or stop chat first.

## How to paint

One-off brief (GPU host):

```bash
agictl model media generate --name qwen-image-2512 --prompt 'your long scene brief'
```

Standing Utility Profile (this laptop or a full-stack host):

```bash
agictl utility run qwen-image-2512
```

On `topology=client`, paint still runs on the GPU host. The PNG is copied back to `--out` or the UM output dir on this machine. Weights stay on the GPU host.

Edit the Utility Model in agitop (System Settings → Utility Models) or `agictl utility model update`.

## After you start a run

Image generation is slow. If the tool times out, **do not immediately re-run**. Follow `utility_models.md` (lock, inspect output path, snooze).

Default PNG path for generate: `/tmp/versa-agi-media-out/qwen-image-2512-<unix-time>.png`.

**Seed:** we do not send `--seed` unless you pass one (sd-cli then uses 42). Seed is a variation lock, not the subject. The brief is the subject.
