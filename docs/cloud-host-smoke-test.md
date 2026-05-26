# Bambu Cloud Host — Manual Smoke Test

Confirms the full Phase 2 + 3 pipeline works against the real Bambu plugin.
Not in CI (requires network + Bambu's CDN).

## Steps

1. Build the image:

   ```bash
   docker build -t bambu-gateway:cloud .
   ```

2. Start with cloud mode on, NO printers configured (we only test the host):

   ```bash
   docker run --rm -it \
     -e BAMBU_CLOUD_ENABLED=true \
     -e BAMBU_CLOUD_REGION=US \
     -v $(mktemp -d):/data/bambu-plugin \
     bambu-gateway:cloud
   ```

3. Watch the logs for:
   - `Bambu plugin 02.05.02.58 installed to /data/bambu-plugin/active`
   - `bambu_cloud_host: started`
   - `Bambu plugin host ready`

4. In another terminal, exec a curl against `/api/health` (or whatever the
   gateway's health endpoint is). Confirm it responds.

5. Stop the container. The host should exit cleanly (look for
   `bambu_cloud_host: stdin closed, exiting`).

## Expected failure modes & diagnosis

- "dlopen failed: libagora_rtc_sdk.so: cannot open shared object file" —
  the plugin's dependencies aren't on LD_LIBRARY_PATH. Fix by setting
  `LD_LIBRARY_PATH=/data/bambu-plugin/active` in the runtime image's
  environment (the host inherits it).
- "host died (exit code 139)" — the plugin segfaulted. Check the discovery
  notes; the bootstrap sequence may be incomplete or out of order.
- "PJARCZAK_BAMBU_NETWORK_SO env var is not set" — the lifespan didn't
  pass it through. Check `app/main.py`.

## Known limitation: Apple Silicon + OrbStack

On Apple Silicon Macs running x86_64 containers under OrbStack (or Rosetta /
QEMU), `dlopen("libbambu_networking.so")` triggers SIGBUS during ELF
loading. The plugin has a `.tbss` section (thread-local BSS) which the
x86_64 emulation layer cannot allocate post-startup. This affects ANY
dlopen path (the C++ host, `ctypes` from Python, manual binaries — even
`/lib64/ld-linux-x86-64.so.2 --list libbambu_networking.so` crashes the
same way).

The plugin **runs correctly on native x86_64 Linux**. The smoke test
above is therefore a *production-target* test — it must be run on the
deploy server (`10.0.1.9`) or any native x86_64 Linux host, not on the
Mac development machine.

Recommended workflow:

```bash
# From the Mac dev box, deploy to the production server:
deploy-docker.sh bambu-gateway:cloud

# Then SSH to 10.0.1.9 and check container logs:
ssh root@10.0.1.9 'docker logs bambu-gateway --tail 50'
```

## Run history

| Date | Host | Result | Notes |
|------|------|--------|-------|
| _pending_ | _pending_ | _pending_ | First run deferred — to be executed manually on the next deploy to 10.0.1.9. |
