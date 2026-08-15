# Local media — FLUX.1-dev

> **Purpose**: How to paint with the local FLUX.1-dev Utility Model. Read this (or `agictl model media usage`) **before** generating.
> **Scope**: All agents (`all`)
> **Official card:** [unsloth/FLUX.1-dev-GGUF](https://huggingface.co/unsloth/FLUX.1-dev-GGUF)
> **sd.cpp guide:** [Flux](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux.md)
> **License:** [FLUX.1 [dev] non-commercial](https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md)

> **Harness tools:** Shell form below. In a work cycle, `agictl_utility` / `agictl_model` take the part after `agictl`.

## What this is

Catalog key **`flux1-dev`** is a **Utility paint** model (image out). It is **not** a chat model and **not** a VLM. Do not activate it for agents. Do not put its GGUFs in SYCL / llama-server.

Provider: `local_media`. Runtime: pinned `sd-cli`. Driver: `◆` on output image. sd-cli uses `--clip_l` and `--t5xxl`, not `--llm`.

Do **not** follow ComfyUI or Diffusers paths. The product path is this skill + `agictl`.

Pinned files (one per role):

- DiT: `unsloth/FLUX.1-dev-GGUF` / `flux1-dev-Q8_0.gguf`
- CLIP-L: `comfyanonymous/flux_text_encoders` / `clip_l.safetensors`
- T5: `comfyanonymous/flux_text_encoders` / `t5xxl_fp16.safetensors`
- VAE: `black-forest-labs/FLUX.1-dev` / `ae.safetensors`

## Write the brief this way

The UM `system_prompt` / `--prompt` **is** the paint brief. Write a **long, specific scene**, not a three-word tag list. Include subject, setting, light, camera, and any text to render (quote it).

## Check usage first

```bash
agictl model media usage flux1-dev
```

## When to paint

- Paint runs on the GPU host. From a `topology=client` laptop, generate/utility SSH the paint and copy the PNG back here.
- Default size **768×768**. Defaults: **20 steps**, **CFG 1.0**, `--clip-on-cpu`.
- Prefer the pinned DiT **Q8_0**. Inspect of another Unsloth quant still plans that file.
- Non-commercial license — do not use for commercial work.
- Paint shares GPU memory with `versa-agi-sycl`. If VRAM is tight: `--offload` or stop chat first.

## How to paint

```bash
agictl model media generate --name flux1-dev --prompt 'your long scene brief'
```

Standing Utility Profile:

```bash
agictl utility run flux1-dev
```

## After you start a run

Image generation is slow. If the tool times out, **do not immediately re-run**. Follow `utility_models.md`.

Default PNG path: `/tmp/versa-agi-media-out/flux1-dev-<unix-time>.png`.
