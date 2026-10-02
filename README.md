# Fluxion Spark Serving Console

A lightweight dashboard for deploying, switching, monitoring, and testing four
preinstalled model profiles on a DGX Spark. It controls the existing
`spark-dual-models` launchers and does not install model weights or runtimes.

## Requirements

- Python 3 with `venv` and `pip`
- `tmux`
- Network access to install Python packages from the configured package index
- An installed launcher bundle at `$HOME/ai/spark-dual-models`
- The model profiles you want to run must already be installed

## Install

Run this as the Linux account that owns the model launcher and runtimes:

```bash
cd "$HOME/ai"
git clone git@github.com:fluxion-sys-ai/spark-serving-console.git
cd spark-serving-console
./setup.sh
```

`setup.sh` creates the dashboard's local `.venv` and installs its Python
dependencies. The prebuilt frontend is included, so Node.js is not required on
the Spark.

## Start the dashboard

```bash
./console.sh start
./console.sh status
```

From your laptop, forward the dashboard port:

```bash
ssh -N -L 18080:127.0.0.1:18080 <user>@<spark-host>
```

Open the exact authenticated URL printed by `./console.sh status`.
Treat this URL as a secret because it contains the dashboard access token.

```bash
./console.sh stop
```

Stopping the dashboard does not stop a deployed model.

## Model profiles

| Profile | Model | Engine | kvcached | Default API port |
|---|---|---|---|---:|
| `4b` | Qwen3.5-4B NVFP4 | vLLM 0.24 | On | 12462 |
| `7b` | Qwen2.5-VL-7B NVFP4 | vLLM 0.24 | On | 12461 |
| `27b` | Qwen3.8-27B NVFP4 | vLLM 0.28 | Off | 12528 |
| `47b` | BigQwen2.5-Echo-47B Q4_K_M | llama.cpp | Off | 12547 |

The dashboard reads the actual model alias, port, and runtime path from the
launcher. It supports one active profile at a time.

Use the UI to:

- **Deploy** an installed model.
- **Switch** after the current engine is idle.
- **Stop** the selected model.
- **Try API** with a short OpenAI-compatible request.
- View GPU, memory, request, token, latency, throughput, and acceptance metrics.

The dashboard blocks deployment when another account or unrelated process is
using the GPU. It never stops foreign GPU workloads.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SPARK_BUNDLE` | `$HOME/ai/spark-dual-models` | Model launcher directory |
| `SPARK_CONSOLE_DATA` | `./var` | Dashboard token, logs, and event database |
| `SPARK_CONSOLE_PORT` | `18080` | Loopback dashboard port |

## Test and rebuild

Run the backend tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Node.js and pnpm are needed only when changing the frontend:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
```

Commit the rebuilt `frontend/dist` so Spark installations can run without
Node.js. UI styling and components are adapted from
[`workstation-serving-stack`](https://github.com/fluxion-sys-ai/workstation-serving-stack);
see [`UPSTREAM.json`](UPSTREAM.json) for provenance.
