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

## Traps that appeared later (2026-08-16)

Both cost a failed server start on a fresh box. `scripts/gpu_serve.sh` now
handles each, but they are worth knowing when reading a crash log.

- **Python 3.10 is too old.** Lambda's image defaults to `python3.10`, and
  the original script built the venv with plain `python3`. Current vllm hard
  depends on flashinfer, whose `comm/fd_exchange.py` annotates
  `array.array[int]` at import time; that only parses on 3.11+. On 3.10 the
  engine dies with `TypeError: 'type' object is not subscriptable`.
  Uninstalling flashinfer does NOT help: the guarded import at
  `allreduce_rms_fusion.py` is skipped via `find_spec`, but another import
  is unguarded and turns it into `ModuleNotFoundError`. Build the venv with
  `python3.12` (available as `3.12.13-1+jammy1`, needs `python3.12-venv`).
- **`~/.cache` ends up root-owned.** The `sudo apt-get install` of the driver
  creates `/home/ubuntu/.cache` as root, after which vllm fails with
  `PermissionError: [Errno 13] Permission denied: '/home/ubuntu/.cache/flashinfer'`.
  The same cause makes pip print "cache has been disabled" warnings during
  setup, which is the early tell. `chown -R` before serving.

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

The venv now lives on the NFS (`$NFS/tally-venv`), so a SECOND launch skips
the vllm install entirely and goes boot -> serve. Measured 2026-08-16: a
reused venv reached serving in about 11 min against ~25 for a cold one. The
script tests whether the venv imports vllm rather than whether the directory
exists, because a persisted venv can be half-installed and would otherwise be
trusted and then fail after the model had already spent ten minutes loading.

torch.compile artifacts still land in `~/.cache/vllm`, which is ephemeral, so
each fresh box pays ~2 min of compile. Moving `VLLM_CACHE_ROOT` to the NFS
would remove that too; not done yet.

### The trap in a persistent venv, if you ever write another one

A venv symlinks `bin/python` at an interpreter that lives on the INSTANCE, so
on a fresh box that symlink dangles even though the 8GB of packages beside it
are perfectly good. The first version of the reuse check here tested whether
the venv imported vllm, which is the right question, but it asked it BEFORE
installing python3.12. The answer was therefore always no, the script deleted
the venv and reinstalled, and it printed "skipping a 10-12 minute install"
while doing the opposite. The optimisation would have paid off exactly never
and looked like it was working.

It surfaced on the second launch, because that is the first time the reuse
path can run at all: build it on launch one, get the truth on launch two.
Install the interpreter unconditionally first, then judge the venv. If you
find yourself watching an 8GB rebuild on a box that should have skipped it,
this is why, and it is not normal.

## Instance lifecycle (resolved)

The two lost instances on the first night were NOT reaped by Manifold — its
audit log shows zero terminations for those boxes. The cause was this
harness's own relaunch-on-timeout logic (a terminate-plus-launch fired when
a readiness check timed out). With correct readiness polling, API-launched
instances are stable; Manifold also adopts externally-launched instances for
Files/chat/telemetry. Everything here is restartable regardless (weights on
NFS, extraction resumes via DBOS + per-document bookkeeping).

## Sharing the account with other projects (2026-08-16)

Two A100s were terminated by a DIFFERENT project's agent session on this
machine, one during boot and one about 60 seconds before it would have
served. That session was not being careless: `GET /instances` returns no
owner and no launch note, so an unattributed box is all it could see, and
Manifold's `idle_status()` reported only `idle_seconds` and
`timeout_seconds`, which during warmup reads exactly like an abandoned
server. Its note was "verified idle... 0 users, no user processes, nothing
written to the NFS".

Every part of that is true of a healthy box loading a 27B model. There are no
logged-in users, no obvious user processes, and no NFS writes, because the
weights are being READ from the shared cache. The reliable tell is
`nvidia-smi`: a warming vLLM already holds 30GB of VRAM.

What to do about it:

- Pass a `note` on every `launch_gpu` naming the project. It is what another
  session sees via `get_launch_status`, and once Manifold's phase-94 work
  ships it surfaces directly in `list_instances` as `purpose`.
- Set `idle_timeout_seconds` at launch. The default sweep terminates a
  READY-but-silent server after 1800s, which a long local processing stage
  can exceed. We pass 7200 plus a `max_lifetime_seconds` ceiling as the
  runaway guard.
- Do not terminate a box this project did not launch, and expect the same in
  return.

Manifold's OpenAI proxy on localhost:8000 only routes to `vllm-serve` JOBS,
not to a server started by this script over SSH; it answers `no_model_served`
and lists no models. So the pipeline still reaches the model through
`ssh -f -N -L 8801:127.0.0.1:8000` and `VLLM_BASE_URL=http://127.0.0.1:8801/v1`.
The cost of that is real: tunnel traffic is invisible to Manifold, so it does
not reset the idle clock, which is the other reason to set the timeout
explicitly rather than rely on the default.
