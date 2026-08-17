#!/usr/bin/env bash
# Tally extraction server — runs ON a fresh Lambda GPU instance.
# Idempotent and self-selecting:
#   * driver too old / venv missing  -> stage SETUP (installs, then reboots)
#   * otherwise                      -> stage SERVE (starts vLLM)
# Laptop side:
#   scp scripts/gpu_serve.sh ubuntu@<ip>:serve.sh
#   ssh ubuntu@<ip> bash serve.sh      # 1st run: setup + reboot
#   ssh ubuntu@<ip> bash serve.sh      # 2nd run: serves
#   ssh -f -N -L 8801:127.0.0.1:8000 ubuntu@<ip>   # tunnel
# See docs/gpu-runbook.md for the why behind every flag.
set -euo pipefail

MODEL="${TALLY_MODEL:-Qwen/Qwen3.5-27B-FP8}"
NFS=/lambda/nfs/Somnora-East
# On the NFS, not in $HOME: $HOME dies with the instance, and installing vllm
# costs 10-12 minutes of billed A100 time. We paid that three times in one
# night. The share already holds the model cache, so the venv sits beside it
# and a second launch goes straight to SERVE.
VENV="$NFS/tally-venv"
MIN_DRIVER=580   # CUDA 13 wheels need >= 580; Lambda images ship 570 (12.8)
# Lambda's image defaults to python3.10, and two things need 3.12 here.
# vllm hard-depends on flashinfer, whose fd_exchange.py annotates
# array.array[int] at import time, which only parses on 3.11+; on 3.10 the
# engine dies with "'type' object is not subscriptable", and removing
# flashinfer just moves the failure to an unguarded import elsewhere.
# python3.12-dev matters just as much: triton compiles cuda_utils.c at first
# use and needs Python.h, and without it the engine dies during profiling
# with a bare CalledProcessError from gcc.
PYTHON=python3.12

driver_major() {
    nvidia-smi --query-gpu=driver_version --format=csv,noheader | cut -d. -f1
}

# A persisted venv is only worth reusing if it actually imports. Testing for
# the directory alone was safe when it lived in $HOME and was always fresh;
# on the NFS it survives, so a half-finished install or one built against a
# different Python would be silently trusted and then fail at serve time,
# after the model spent ten minutes loading.
venv_ok() {
    [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import vllm" >/dev/null 2>&1
}

# Reassert cache ownership before ANY pip or vllm run: the sudo apt calls
# below create ~/.cache as root, and both pip (silently, losing its cache)
# and vllm (fatally, EACCES on ~/.cache/flashinfer) trip over it afterwards.
sudo chown -R "$(id -un):$(id -gn)" "$HOME/.cache" 2>/dev/null || true

# python3.12 is per-instance even though the venv is not, and it has to be
# installed BEFORE the venv is judged. A venv on the NFS symlinks bin/python
# at an interpreter that lives on the box, so on a fresh instance that symlink
# dangles and venv_ok is false for a venv that is otherwise perfectly good.
# Testing first and installing second would delete it and reinstall vllm, which
# is the whole cost this was meant to avoid.
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "installing $PYTHON (needed before the NFS venv can be judged)"
    sudo apt-get update -qq >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3.12 python3.12-venv python3.12-dev >/dev/null 2>&1
fi

if ! venv_ok || [ "$(driver_major)" -lt "$MIN_DRIVER" ]; then
    echo "== stage SETUP =="
    if ! venv_ok; then
        # A directory that exists but does not import is worse than none: it
        # would defeat the check above on the next run too. Clear it out.
        [ -d "$VENV" ] && echo "unusable venv at $VENV; rebuilding" && rm -rf "$VENV"
        # Fully isolated venv: system dist-packages carry a broken flatbuffers.
        "$PYTHON" -m venv "$VENV"
        source "$VENV/bin/activate"
        pip install -q --upgrade pip
        pip install -q vllm
    else
        echo "reusing the venv on $NFS (skipping a 10-12 minute install)"
    fi
    if [ "$(driver_major)" -lt "$MIN_DRIVER" ]; then
        sudo apt-get update -qq >/dev/null
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            nvidia-driver-590-server-open 2>&1 | tail -1
        echo "SETUP_DONE_REBOOTING (run this script again after the box returns)"
        sudo reboot
    fi
    echo "SETUP_DONE_NO_REBOOT_NEEDED"
fi

echo "== stage SERVE ($MODEL) =="
# The sudo apt calls above create ~/.cache owned by root, and vllm then dies
# with EACCES writing ~/.cache/flashinfer. Cheap to reassert every run.
sudo chown -R "$(id -un):$(id -gn)" "$HOME/.cache" 2>/dev/null || true
source "$VENV/bin/activate"
export HF_HOME=$NFS/hf-cache HF_HUB_ENABLE_HF_TRANSFER=0 HF_HUB_DISABLE_XET=1
mkdir -p "$HF_HOME"
pkill -f "[v]llm serve" 2>/dev/null && sleep 3 || true
# --enable-auto-tool-choice + parser: pydantic-ai gets structured output via
# tool calls; vLLM 400s every request without these.
nohup vllm serve "$MODEL" --host 127.0.0.1 --port 8000 \
    --max-model-len 8192 --gpu-memory-utilization 0.92 --max-num-seqs 8 \
    --enable-auto-tool-choice --tool-call-parser hermes > "$HOME/vllm.log" 2>&1 &

echo "loading; polling for readiness..."
for _ in $(seq 1 60); do
    if curl -s --max-time 3 http://127.0.0.1:8000/v1/models 2>/dev/null | grep -q "$MODEL"; then
        echo "SERVING $MODEL"
        exit 0
    fi
    if ! pgrep -f "[v]llm serve" > /dev/null; then
        echo "CRASHED — tail of ~/vllm.log:"
        tail -5 "$HOME/vllm.log"
        exit 1
    fi
    sleep 10
done
echo "TIMEOUT after 10m — check ~/vllm.log"
exit 1
