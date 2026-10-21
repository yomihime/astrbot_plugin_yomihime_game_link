# Real AstrBot ZIP smoke

Run in a disposable Python 3.12+ virtual environment. Prepare a clean official AstrBot v4.29.1 checkout at commit `dffe922773725e9c178392c423676ad65044748c`; install its dependencies and this plugin's `requirements.txt` into the environment before testing. Do not use production configuration, credentials, or data. Dependency installation is a separate online preparation step; the smoke itself stays offline.

```sh
python scripts/smoke_real_astrbot_zip.py \
  --host-source /absolute/path/to/AstrBot \
  --plugin-zip /absolute/path/to/plugin.zip \
  --work-root /absolute/path/to/new-disposable-root \
  --font /absolute/path/to/plugin/presentation/assets/fonts/NotoSansCJKsc-Regular.otf
```

The work root must not exist. The canonical font hash is checked before startup; `--font` defaults to the repository fixture when the script lives in `scripts/`. The input ZIP is preserved: the host receives a copy. The real host creates only disposable configuration and databases beneath the selected work root. Do not run with `python -O`.

Coverage: real host lifecycle and plugin ZIP installation; plugin-local SDK bootstrap; Core ready and active FF14 module without `YGL_SECRET_KEY`; ordinary Dashboard JWT management requests and rejection of missing/API-key/forged/cross-origin requests; native view-title localization; new scoped `/views/.../_t/...` HTML and nested assets; cross-view token rejection; safe credential/font/module status; actual PNG help rendering with a fixed canonical font and captured exact-event send; host shutdown.

This script is an isolated smoke for its stated Host version, input ZIP and disposable data root. It does not establish current user-instance state, saved credential recovery, live upstream behavior or IM delivery. Storage and recovery behavior is defined in the current [credential storage contract](credential-storage.md); plugin-specific feature acceptance must be assessed for its exact source and environment. Never send synthetic FFLogs credentials upstream.

Outputs: `zip-smoke.json` (ZIP hash, host commit, results and sanitized blocked HTTP destinations), `help.png`, and disposable runtime files. Capture stdout/stderr externally if logs are desired.

## Explicit offline fixtures and limits

- `ASTRBOT_DISABLE_METRICS=1` and `disable_metrics=true` use official telemetry opt-outs
- ZIP dependencies are checked before startup; subprocess creation is denied during the smoke to prevent dependency-recovery commands from escaping the Python guards
- Before importing the host, DNS lookup and socket connect/connect_ex/sendto operations are denied; aiohttp requests are rejected before transport/DNS. ASGI requests are in-process and use no network
- Only two unrelated host background jobs are replaced with recorded no-op coroutines: official T2I endpoint discovery and LLM metadata refresh
- No plugin installer, bootstrap, authentication, view-serving, runtime, rendering or business response is mocked. Providers/platforms are empty. Dashboard identity and signing key are synthetic disposable test values
- Fixed public calendar synchronization may attempt startup requests; optional Host subprocess probes may also be denied. These are rejected, not fabricated; the report separates rejected attempts from allowed network connections (zero). No live FFLogs, calendar success, provider, IM delivery, browser rendering, or production-security claim is made
- Python-level test guards are not an OS network sandbox. Use an externally network-disabled container when adversarial code isolation is needed. The supplied official host and reviewed plugin are the trusted test subjects
