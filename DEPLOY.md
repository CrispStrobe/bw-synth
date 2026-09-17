# Deploying bw-synth on a real host

The Vercel attempt is documented in `README.md`: it deploys, enforces the licence
rule, and **cannot synthesise**, because the toolchain unpacks ~100 MB of
WebAssembly into a `/tmp` that has 0 bytes free. Anywhere with a real disk runs
the whole flow in seconds. This is that deployment.

Measured on the reference host (2 vCPU, 8 GB): **368 MB installed, ~200 MB peak
RSS, ~8 s per build**, replying a 4.6 MB bitstream. It fits almost anywhere.

## Shape

A hardened Docker container behind a reverse proxy:

- **non-root** (`uid 10001`), **read-only root filesystem**, **all Linux
  capabilities dropped**, `no-new-privileges`
- **egress blocked** at the host firewall, scoped to the container's subnet — a
  synthesiser has no reason to make an outbound connection, and blocking it means
  an exploited toolchain cannot exfiltrate
- published **only on `127.0.0.1`**; the reverse proxy terminates TLS and adds CORS
- memory / pids / cpu limited; `--restart unless-stopped`

```
docker build -t bwsynth:latest .
sudo IMAGE=bwsynth:latest deploy/run.sh
sudo install -m750 deploy/egress-block.sh /usr/local/sbin/bwsynth-egress.sh
sudo install -m644 deploy/bwsynth-egress.service /etc/systemd/system/
sudo systemctl enable --now bwsynth-egress.service   # re-asserts on docker restart
# reverse proxy: adapt deploy/nginx-synth.conf, then certbot --nginx -d synth.<domain>
```

## Three things that will bite you, and did

**1. The YoWASP cache path is off by one, on a read-only rootfs.**
`_flow.py`'s `_subprocess_env()` hands the tools `YOWASP_CACHE_DIR=$BW_SYNTH_CACHE`.
YoWASP stores its unpacked WebAssembly under `<XDG_CACHE_HOME>/YoWASP/` — the
`YoWASP` *subdirectory*. Point `BW_SYNTH_CACHE` at the parent and every call is a
cache miss; on a read-only rootfs a miss does not error, it **hangs** trying to
re-unpack into a directory it cannot write. The `Dockerfile` bakes the wasm at
build time and sets `BW_SYNTH_CACHE=/home/app/.cache/YoWASP` so runtime is a hit
with nothing to unpack. If health hangs for minutes with the container otherwise
idle, this is why.

**2. An `--internal` Docker network cannot publish a port.**
It is the obvious way to block egress, and it also silently breaks inbound: an
internal network has no gateway, so `-p 127.0.0.1:8091:8091` accepts the
connection and then nothing answers. Egress is blocked at the firewall instead
(`deploy/egress-block.sh`), which leaves publishing intact.

**3. It only misbehaves under a real WSGI server.**
The cache hang never appears on Vercel or in CI, because their serving models
close or redirect the tools' stdin/stdout and cache differently. It appears the
first time gunicorn hands a worker a live pipe. Test against `deploy/run.sh`, not
against `python -c`.

## Verifying a deployment

```
curl -s https://synth.<domain>/api/health         # ok:true, three tool versions
# a blinky must return the reference bitstream, byte for byte:
#   sha256 = 586e54acb48e90cf22e9181a63d8454efdaf9540af1ea1c41499a9c2764e257d
# a GPL-3.0 source must be refused BY NAME, code source-licence-refused,
#   alternative local-tier, with synthesis never reached.
```

The health check is fail-closed on purpose: brickwright-lite's backend selector
does not offer a backend that cannot answer, so a broken deploy must say 503
rather than accept work it will fail.
