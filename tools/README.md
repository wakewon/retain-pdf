# RetainPDF Tools

## MinerU API Bridge

`mineru_api_bridge.py` exposes a MinerU installation from the current Python environment as a RetainPDF-compatible HTTP API. Use it when MinerU already works in a host uv / conda environment and you do not want to build the large Docker sidecar image.

macOS uv example:

```bash
cd /workspace/example-project
uv run --directory /workspace/example-mineru-service \
  python tools/mineru_api_bridge.py --host 127.0.0.1 --port 18080
```

Windows conda example:

```powershell
conda activate zotero-mcp
cd C:\path\to\retain-pdf
python tools\mineru_api_bridge.py --host 0.0.0.0 --port 18080
```

Useful environment variables:

- `MINERU_BACKEND`: MinerU backend, default `pipeline`.
- `MINERU_LANG`: default language, default `ch`.
- `MINERU_PYTHON`: optional diagnostic value for the intended Python interpreter. Start the bridge with that interpreter, for example `"$MINERU_PYTHON" tools/mineru_api_bridge.py`.
- `MINERU_WORKDIR`: temporary working directory for uploaded PDFs and MinerU outputs.
- `MINERU_TIMEOUT_SECS`: request timeout for command mode, default `1800`.
- `MINERU_COMMAND`: optional external MinerU executable. If omitted, the bridge uses MinerU's Python API.
- `MINERU_COMMAND_TEMPLATE`: optional full command template with `{input}`, `{output}`, `{backend}`, `{parse_method}`, `{lang}`.

RetainPDF Docker app should point `RETAIN_MINERU_LOCAL_BASE_URL` to:

```text
http://host.docker.internal:18080
```
