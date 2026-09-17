# bw-synth — Gowin synthesis service, hardened for a shared production host.
#
# The threat: this eats arbitrary Verilog from the internet and runs a toolchain
# over it. So the workload runs as a non-root user, on a read-only root
# filesystem, with every Linux capability dropped and no path to acquire more.
# Egress is cut at the network layer (see the run flags / compose) because a
# synthesiser has no reason to phone home.
FROM python:3.12-slim AS base

# Pinned toolchain (identical to the Vercel attempt) + a WSGI server. gunicorn
# is the only addition and it is pure-Python, so the licence inventory is
# unchanged from the three permissive tools.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt "gunicorn==23.0.0" && rm /tmp/requirements.txt

# A real, unprivileged runtime user. UID is high and fixed so it maps to nobody
# meaningful on the host.
RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app

COPY --chown=app:app . /app
WORKDIR /app
RUN rm -rf /app/.git /app/.vercel /app/__pycache__ /app/tests/__pycache__ 2>/dev/null || true

USER app
# bw-synth's _subprocess_env() points YOWASP_CACHE_DIR at $BW_SYNTH_CACHE. YoWASP
# stores its unpacked wasm under <XDG_CACHE_HOME>/YoWASP, so the dir the tools
# must be handed is that YoWASP subdir, not its parent: pointing one level too
# high is a cache miss, and on a read-only rootfs a miss HANGS trying to
# re-unpack. Baked here so the image is correct with no runtime flag.
ENV HOME=/home/app \
    XDG_CACHE_HOME=/home/app/.cache \
    PYTHONUNBUFFERED=1 \
    BW_SYNTH_CACHE=/home/app/.cache/YoWASP

# Bake the WebAssembly unpack INTO the image, as the runtime user, by running the
# whole flow once at build time. This is the step that could not happen on
# Vercel (525 MB of /tmp, 0 free); here it lands in an image layer, so a
# read-only container at runtime never needs to unpack anything and cold starts
# do not re-pay it. It also fails the build if the toolchain is broken.
RUN set -e; \
    printf 'module blink(output led); assign led = 1'"'"'b1; endmodule\n' > /tmp/blink.v; \
    printf 'IO_LOC "led" 73;\nIO_PORT "led" IO_TYPE=LVCMOS33;\n' > /tmp/blink.cst; \
    cd /tmp; \
    yowasp-yosys -q -p "read_verilog blink.v; synth_gowin -top blink -json blink.json"; \
    yowasp-nextpnr-himbaechel-gowin --json blink.json --write blink_pnr.json \
        --device "GW2AR-LV18QN88C8/I7" --vopt family=GW2A-18C --vopt cst=blink.cst; \
    gowin_pack -d GW2A-18C -o blink.fs blink_pnr.json; \
    test -s blink.fs; \
    echo "BAKED: bitstream $(wc -c < blink.fs) bytes"; \
    rm -f /tmp/blink.*

EXPOSE 8091
# --timeout 300 matches the flow's TIMEOUT_SECONDS (280) plus headroom.
# 2 workers on 2 cores; each build is CPU-bound and short.
CMD ["gunicorn","--bind","0.0.0.0:8091","--workers","2","--threads","1", \
     "--timeout","300","--graceful-timeout","310","--max-requests","200", \
     "--access-logfile","-","--error-logfile","-","app:app"]
