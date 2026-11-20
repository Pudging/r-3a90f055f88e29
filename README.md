# Poseidon

Poseidon is Team potato’s placement-focused Mech Royale bot. It plans its route from the current and forecast zone boundaries. It abandons fights at low health, stays in the zone to regenerate, and takes nearby cubes and supply drops when it can reach them safely. Its Python entry point reads the `mechfight/1` protocol from standard input and the arena files from the directory named in the match handshake.

Build from the root of this repository and run the organizer’s checker with the downloaded kit. The following commands use PowerShell.

```powershell
$kit = (Resolve-Path 'path/to/mechfight-kit').Path
$arena = (Resolve-Path "$kit/arena").Path
docker build --platform linux/amd64 -t poseidon .
python "$kit/check/check.py" --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "${arena}:/arena:ro" poseidon
```
