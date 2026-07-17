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
VENV="$HOME/tally-venv"
MIN_DRIVER=580   # CUDA 13 wheels need >= 580; Lambda images ship 570 (12.8)

driver_major() {
    nvidia-smi --query-gpu=driver_version --format=csv,noheader | cut -d. -f1
}

if [ ! -d "$VENV" ] || [ "$(driver_major)" -lt "$MIN_DRIVER" ]; then
    echo "== stage SETUP =="
    if [ ! -d "$VENV" ]; then
        # Fully isolated venv: system dist-packages carry a broken flatbuffers.
        python3 -m venv "$VENV"
        source "$VENV/bin/activate"
        pip install -q --upgrade pip
        pip install -q vllm
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
