# Local media — LTX-2.5 Distilled

> **Purpose**: How to generate video with the local LTX-2.5 Distilled Utility Model. Read this (or `agictl model media usage`) **before** generating.
> **Scope**: All agents (`all`)
> **Official Distilled GGUF:** [Abiray/LTX-2.5-Distilled-GGUF](https://huggingface.co/Abiray/LTX-2.5-Distilled-GGUF)
> **sd.cpp guide:** [LTX-2.3/LTX-2.5](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ltx2.md)
> **Companions:** [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) (gated — needs `hf_token` + license accept)
> **License:** LTX-2 community; commercial use is free under $10M annual revenue

> **Harness tools:** Shell form below. In a work cycle, `agictl_utility` / `agictl_model` take the part after `agictl`.

## What this is

**Experimental.** Catalog keys **`ltx-2.5-distilled`** (Q4, default), **`ltx-2.5-d-q6`**, and **`ltx-2.5-d-q8`** are **Utility video** models (video out). The stack works; usable 720p-class is parked for **>64 GB VRAM**. They are **not** chat models. Do not activate them for agents. Do not put their GGUFs in SYCL / llama-server.

Provider: `local_media`. Runtime: pinned `sd-cli` **`-M vid_gen`**. Driver: `◆` on output video.

Do **not** follow the Abiray ComfyUI T2V/I2V graphs. The product path is this skill + `agictl`.

Required files (one bundle per imported quant):

- DiT: `Abiray/LTX-2.5-Distilled-GGUF` / `LTX-2.5-Distilled-Q4_K_M.gguf` (or Q6_K / Q8_0)
- Text encoder: `Lightricks/LTX-2.5` / `text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors` — Gemma 4 12B **LTX-tuned**. Stock Gemma 4 is not a substitute. Projection is bundled; do not pass `--embeddings-connectors`.
- Video VAE: **`vae/ltx-2.5-video-vae-conv-bf16.safetensors`** only. The default `ltx-2.5-video-vae-bf16.safetensors` is a diffusion decoder and is **not implemented** in sd.cpp.
- Audio VAE: `vae/ltx-2.5-audio-vae-bf16.safetensors` (synced sound in the WebM)

## Write the brief this way

The UM `system_prompt` / `--prompt` **is** the shot brief. Write a **long cinematic shot**: subject, motion, camera, light, and any speech. Not a three-word tag list.

## How to import (GPU host)

Do **not** `hf download` from the Lightricks card or follow their Python/Comfy/Diffusers snippets. Inspect the Abiray GGUF; import pulls the planned companions.

```bash
agictl model media recipes
agictl model media inspect \
  'hf://Abiray/LTX-2.5-Distilled-GGUF/LTX-2.5-Distilled-Q4_K_M.gguf' \
  --name ltx-2.5-distilled
sudo agictl model media import \
  'hf://Abiray/LTX-2.5-Distilled-GGUF/LTX-2.5-Distilled-Q4_K_M.gguf' \
  --name ltx-2.5-distilled --runtime media
```

Q6 / Q8: same commands with `LTX-2.5-Distilled-Q6_K.gguf` → `--name ltx-2.5-d-q6`, or `…Q8_0.gguf` → `--name ltx-2.5-d-q8`.

Lightricks companions are **gated**. Accept the license on [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) with the same account as `setup.ini` `[local_ai] hf_token`. Then `--update` so pinned `sd-cli` includes LTX-2.5.

From a client laptop, use Model Manager **▣ Media Import** (it SSHs). Do not import on the laptop.

## Check usage first

```bash
agictl model media usage ltx-2.5-distilled
```

## When to generate

- **Parked** except on **high-end GPU memory: >64 GB VRAM** (video RAM — not NVRAM). Do not run usable 720p / 1080p / 4K on a 32 GB or 64 GB card.
- `--vae-tiling` does **not** remove `--offload-to-cpu`. Distilled weights are ~44 GB (DiT + Gemma 4 LTX TE + VAEs), so they already overflow 32 GB VRAM. Offload made 720p × 121 sample at ~119 s/step.
- Intel B70 32 GB proved decode only at 640×384 × 33 (~1.3 s) — rejected. 720p × 121 sampled (~17 min) then VAE decode OOM (~49–83 GB).
- Source defaults stay **`--size 720p`** → **1280×736** × **121** for a later >64 GB host. Native: `--size 1080p`, `--size 2k`. **4K** is 1080p + spatial 2× (not shipped).
- Orientation: `--orientation landscape` or `portrait`. I2V: `--image /path/to/start.png`.
- Runtime is still `local_media` + `sd-cli`, never Ollama.

## How to generate (text-to-video)

```bash
agictl model media generate --name ltx-2.5-distilled --prompt 'your long cinematic shot'
agictl model media generate --name ltx-2.5-distilled --size 1080p --prompt 'your long cinematic shot'
agictl model media generate --name ltx-2.5-distilled --orientation portrait --prompt 'your long cinematic shot'
```

Image-to-video:

```bash
agictl model media generate --name ltx-2.5-distilled --image start.png --prompt 'the same subject blinking, gentle camera push in'
```

Standing Utility Profile (Q4):

```bash
agictl utility run ltx-2.5-distilled
```

## After you start a run

Do not start a 720p-class run on a 32 GB or 64 GB card. On a **>64 GB VRAM** host, 720p × 121 can take tens of minutes (text encoder load first, then steps). Watch sd-cli progress. **Do not Ctrl+C** just because the first minutes are quiet. If the tool times out, **do not immediately re-run**. Follow `utility_models.md`. Check `docker ps` after a cancel — `versa-agi-sd-cli` may still be running.

Default WebM path: `/tmp/versa-agi-media-out/ltx-2.5-distilled-<unix-time>.webm`.
