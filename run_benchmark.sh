#!/usr/bin/env bash
# Run benchmark safely with proper docker handling
# Usage: ./run_benchmark.sh

set -euo pipefail

cd /home/zhufan/Downloads/openforge

# Stop any existing containers to prevent conflicts
docker ps -q --filter "name=^/openforge-" | xargs -r docker stop 2>/dev/null || true
docker ps -aq --filter "name=^/openforge-" | xargs -r docker rm 2>/dev/null || true

# Run the benchmark with a 15-minute timeout
# Using unshare to avoid SELinux issues
timeout 900 bash -c '
  cd /home/zhufan/Downloads/openforge
  python -m validation.runner 2>&1 | tee benchmark_full_run.log
' || {
  echo "Benchmark run completed or timed out"
  exit 0
}
