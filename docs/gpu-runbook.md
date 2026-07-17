# GPU extraction runbook (proven 2026-07-17)

The recipe that took the Milestone 4 pilot from a fresh Lambda instance to
serving extractions, including every trap hit along the way. The pipeline
side needs only two env vars; everything here is server-side.

## Working configuration

- Instance: `gpu_1x_a100_sxm4` (40 GB, us-east-1), file system `Somnora-East`
  attached (persistent HF cache at `/lambda/nfs/Somnora-East/hf-cache`).
- Model: `Qwen/Qwen3.5-27B-FP8` (official). Does NOT fit an A10 (22 GB):
  even the GPTQ-Int4 build OOMs at load. A10-viable menu: `Qwen/Qwen3.5-9B`
  (dense bf16, tight) or `Qwen/Qwen3-14B-AWQ` (comfortable).
- Driver: Lambda's image ships a CUDA 12.8 driver; current vLLM wheels are
  CUDA 13. Fix: `sudo apt-get install -y nvidia-driver-590-server-open`
  (resolves to 595.x) + reboot, BEFORE first server start.
- venv: fully isolated (`python3 -m venv`, never `--system-site-packages` —
  system dist-packages carry a broken flatbuffers).
- Downloads: `HF_HUB_ENABLE_HF_TRANSFER=0 HF_HUB_DISABLE_XET=1`.

## Serve command

```sh
source ~/tally-venv/bin/activate
export HF_HOME=/lambda/nfs/Somnora-East/hf-cache \
       HF_HUB_ENABLE_HF_TRANSFER=0 HF_HUB_DISABLE_XET=1
vllm serve Qwen/Qwen3.5-27B-FP8 --host 127.0.0.1 --port 8000 \
  --max-model-len 8192 --gpu-memory-utilization 0.92 --max-num-seqs 8 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

Non-obvious flags:
- `--enable-auto-tool-choice --tool-call-parser hermes`: REQUIRED.
  pydantic-ai obtains structured output through tool calling
  (`tool_choice="required"`); without the parser vLLM 400s every request.
- `--host 127.0.0.1`: never bind a raw model server to all interfaces.
- Access from the laptop via SSH tunnel:
  `ssh -f -N -L 8801:127.0.0.1:8000 -i ~/.ssh/lambda_burst_ed25519 ubuntu@<ip>`
  then `.env`: `VLLM_BASE_URL=http://127.0.0.1:8801/v1`,
  `LOCAL_MODEL=Qwen/Qwen3.5-27B-FP8`.

## Timing / cost (observed)

Fresh instance to serving: ~20 min (boot 6 + venv/vllm 5 + driver/reboot 5 +
model load 4; first-ever model download adds ~5-10). Extraction, 53 docs /
83 chunks: ~13 min on the A100. Whole pilot run: well under $1 of A100 time.

## Instance lifecycle (resolved)

The two lost instances on the first night were NOT reaped by Manifold — its
audit log shows zero terminations for those boxes. The cause was this
harness's own relaunch-on-timeout logic (a terminate-plus-launch fired when
a readiness check timed out). With correct readiness polling, API-launched
instances are stable; Manifold also adopts externally-launched instances for
Files/chat/telemetry. Everything here is restartable regardless (weights on
NFS, extraction resumes via DBOS + per-document bookkeeping).
