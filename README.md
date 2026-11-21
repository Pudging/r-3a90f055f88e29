# Helios

Helios is a Python bot. It uses the official `mechfight/1` Python wrapper and the arena already mounted by the scorer. The build has no package dependencies.

Helios uses terrain and building checks to choose movement and reject obstructed shots. It leads moving targets with the gun, solves a ballistic intercept for the mortar, releases the charged laser when aligned, and times lightning around close targets and their charge state. It anticipates zone movement, attempts supply-drop captures, prioritizes nearby loot, and estimates which movement angles cross approaching projectiles.

## Verify

From the extracted official kit on Linux or WSL:

```sh
docker build --platform linux/amd64 -t helios-local ../../candidates/helios
python3 check/check.py --map-dir /arena -- docker run --rm -i \
  --platform linux/amd64 --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$(pwd)/arena:/arena:ro" helios-local
```

In the verified Linux run, it answered all 300 observations within 50 ms. Readiness took 809 ms and p99 response latency was 3.7 ms. The checker feeds fixed observations and verifies timing and protocol. Placement and survival need practice matches or a validated simulator.
