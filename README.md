# system-one-mcp

Rust MCP server that exposes System One structured decision primitives over
**Streamable HTTP**, so remote MCP clients can call them.

```text
MCP client ──Streamable HTTP──> system-one-mcp ──HTTPS──> System One API
                                  ├── Choice
                                  ├── Score
                                  └── Noul
```

## Tools

| Tool | Purpose | Upstream `type` |
|---|---|---|
| `Choice` | Pick one named option per question | `choice` |
| `Score` | Rate each question on ordered levels (may be fractional) | `score` |
| `Noul` | Probability that each yes/no proposition holds (0–1) | `noul` |

Every call handles **one or more questions of the same type** sharing a single
`state`. One tool call = one upstream request, so all questions must use the same
primitive. Up to 32 questions per call.

Answers are returned as-is: probabilities, `confidence`, and `score` precision are
never rounded, and `noul` is never coerced to a boolean.

## Configuration

Copy `.env.example` and fill it in. Required:

| Variable | Meaning |
|---|---|
| `SYSTEM_ONE_API_KEY` | Upstream Bearer key. Never exposed as a tool parameter. |
| `SYSTEM_ONE_MODEL` | Upstream model id, e.g. `typesafe/jev-1.13`. Not overridable per call. |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `SYSTEM_ONE_ENDPOINT` | `https://system-one.dev/v1/systemone` | Full upstream endpoint. |
| `MCP_BIND_ADDR` | `0.0.0.0:8080` | Listen address. |
| `MCP_PATH` | `/mcp` | MCP route path. |
| `MCP_ALLOWED_HOSTS` | empty | Comma-separated `Host` allow-list. Empty keeps loopback-only defaults. **Required for non-loopback deployments.** A standalone `*` entry allows any Host (disables DNS rebinding protection). |
| `SYSTEM_ONE_TIMEOUT_SECS` | `120` | Upstream request timeout. |
| `RUST_LOG` | `system_one_mcp=info` | Log filter. |

Missing `SYSTEM_ONE_API_KEY` or `SYSTEM_ONE_MODEL` aborts startup instead of
failing on the first tool call.
To accept any Host, set `MCP_ALLOWED_HOSTS='*'` in the server environment
and restart the server. A `*` entry also overrides other entries in a
comma-separated list. This disables Host validation, not authentication;
protect public endpoints with authentication and rate limits to prevent
unauthorized use of your upstream API quota.


## Run

### Download a binary (no Rust or Docker required)

Download the archive matching your machine from this repository's GitHub
**Releases** page. Each archive includes the executable, this README, and
`.env.example`.

| System | Architecture | Archive suffix |
|---|---|---|
| Windows | Intel / AMD 64-bit | `x86_64-pc-windows-msvc.zip` |
| macOS | Intel | `x86_64-apple-darwin.tar.gz` |
| macOS | Apple Silicon | `aarch64-apple-darwin.tar.gz` |
| Linux / Alpine Linux | Intel / AMD 64-bit | `x86_64-unknown-linux-musl.tar.gz` |
| Linux / Alpine Linux | ARM64 | `aarch64-unknown-linux-musl.tar.gz` |

Archive names start with `system-one-mcp-`. Linux binaries are statically linked
with musl: the same binary supports both Alpine and common glibc-based Linux
distributions without installing glibc or OpenSSL. Windows binaries statically
link the MSVC C runtime. macOS binaries use system libraries and are currently
unsigned and not notarized; releases do not include installers or auto-updaters.

Each archive has a matching `.sha256` file. Verify it **before extracting**:

```bash
# Linux / Alpine (replace <target> with the target from the table)
sha256sum -c system-one-mcp-<target>.tar.gz.sha256
# macOS
shasum -a 256 -c system-one-mcp-<target>.tar.gz.sha256

tar -xzf system-one-mcp-<target>.tar.gz
cd system-one-mcp-<target>
export SYSTEM_ONE_API_KEY='your-api-key'
export SYSTEM_ONE_MODEL='your-model-id'
export MCP_BIND_ADDR='127.0.0.1:8080'
./system-one-mcp
```

On Windows, use PowerShell:

```powershell
$archive = 'system-one-mcp-x86_64-pc-windows-msvc.zip'
$expected = ((Get-Content "$archive.sha256") -split '\s+')[0]
if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'SHA-256 checksum mismatch'
}
Expand-Archive $archive -DestinationPath .
Set-Location system-one-mcp-x86_64-pc-windows-msvc
$env:SYSTEM_ONE_API_KEY = 'your-api-key'
$env:SYSTEM_ONE_MODEL = 'your-model-id'
$env:MCP_BIND_ADDR = '127.0.0.1:8080'
.\system-one-mcp.exe
```

The server reads environment variables; it does **not** automatically load
`.env.example` or `.env`. Bind to loopback for local use. For remote access,
configure `MCP_ALLOWED_HOSTS` and protect the endpoint as described below.

### Build from source

Install Rust 1.88 or newer, export the configuration variables above, then run:

```bash
cargo run --locked --release
```

Point the client at `http://127.0.0.1:8080/mcp` for local use.

### Static binary for Alpine / other musl hosts

`cargo build --release` on a glibc host produces a glibc dynamically linked
binary; Alpine has no glibc loader, so that binary fails with `not found`
(exit 127). Build for the musl target instead to get a fully static binary:

```bash
# Debian/Ubuntu: rustup target add x86_64-unknown-linux-musl
#                sudo apt install musl-tools
# Fedora:        sudo dnf install musl-gcc
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
```

The output is `target/x86_64-unknown-linux-musl/release/system-one-mcp` and runs
unchanged on Alpine, distroless and scratch-style images; only a CA bundle is
needed for TLS. For cross-arch builds, `cargo-zigbuild` is a convenient
alternative. The Docker build already uses this musl target.

## Docker

```bash
docker build -t system-one-mcp .
docker run --rm -p 8080:8080 \
  -e SYSTEM_ONE_API_KEY=sk-or-v1-... \
  -e SYSTEM_ONE_MODEL=typesafe/jev-1.13 \
  -e SYSTEM_ONE_ENDPOINT=https://openrouter.ai/api/alpha/decisions \
  -e MCP_ALLOWED_HOSTS=localhost,127.0.0.1 \
  system-one-mcp
```

The image builds with a dependency-caching layer, runs as a non-root user
(uid 10001), needs no OpenSSL (rustls), and listens on `0.0.0.0:8080` by
default. The builder is Alpine's musl toolchain, so the binary copied into the
`alpine:3.22` runtime is statically linked — it also runs on any other musl or
glibc host. Secrets are read from the environment at start; the container exits
immediately if `SYSTEM_ONE_API_KEY` or `SYSTEM_ONE_MODEL` is missing.

## GitHub Actions

| Workflow | Trigger | Does |
|---|---|---|
| `.github/workflows/ci.yml` | push to `main`, pull requests | `cargo fmt --check`, `clippy -D warnings`, `cargo test`, and a Docker build (no push) |
| `.github/workflows/docker.yml` | push to `main`, tags `v*` | multi-arch (`amd64`/`arm64`) build pushed to `ghcr.io/<owner>/<repo>` |
| `.github/workflows/binaries.yml` | push to `main`, pull requests, tags `v*`, manual dispatch | builds and tests all five binary targets, uploads archives/checksums as workflow artifacts; tag pushes also publish them to GitHub Releases |

Image tags come from `docker/metadata-action`: branch name, `sha-<short>`, and
semver tags for `v*` releases.

Before the first push:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
```

GitHub Packages uses the built-in `GITHUB_TOKEN`, so no extra secret is needed.
If your repository name contains uppercase letters, the workflow lowercases it
automatically because GHCR rejects mixed-case image names.

Binary builds run on native Windows, Intel/ARM macOS, and Intel/ARM Linux
runners. Each target runs Rust unit tests and the free Python startup/protocol
suite against its **release binary**. Linux binaries also run the free suite
inside Alpine 3.22. No release tests call the paid upstream API.

To publish binaries (requires Actions to have permission to write repository
contents):

```bash
git tag v0.1.0
git push origin v0.1.0
```

Only a `v*` tag push publishes a GitHub Release, after all five build jobs pass.
Branch, pull request, and manual runs only upload workflow artifacts. Each
Release asset is a `.zip` / `.tar.gz` archive or its `.sha256` checksum. These
checksums detect corruption; they are not code signatures.

## Example call

```json
{
  "state": "客户反馈：同一笔订单被扣款两次，希望今天内回复。",
  "questions": [
    {
      "id": "team",
      "instructions": "选择负责审核的团队。",
      "criteria": [
        { "name": "billing", "description": "付款与退款" },
        { "name": "support", "description": "技术支持" }
      ]
    }
  ]
}
```

Response keeps the upstream shape:

```json
{
  "answers": {
    "team": {
      "type": "choice",
      "choice": "billing",
      "probabilities": { "billing": 1, "support": 0 },
      "confidence": 1
    }
  },
  "model": "typesafe/jev-1.13-20260917",
  "requested_model": "typesafe/jev-1.13",
  "usage": { "input_tokens": 398, "output_tokens": 59, "cost": 0.0000167 },
  "idempotency_key": "c4ec2c62-...",
  "meta": { "request_id": "...", "provider": "TypeSafe" }
}
```

`model` is what the upstream actually served and may differ from
`requested_model`; record both. Billing metadata is read from response headers
when present — absence means *unknown*, not zero.

## Idempotency and cost

Each call sends `Idempotency-Key`. If omitted, the server generates a UUID and
returns it as `idempotency_key`. To retry safely without double billing, pass the
same `idempotency_key` with the same body.

Failures are returned as MCP tool errors (`isError: true`) with
`{"error": {status, code, message, request_id}}`, so the calling model can read
the reason instead of receiving an opaque protocol error.

## Security notes

This server does **not** authenticate MCP callers. Anyone who can reach the
endpoint can spend your upstream credits. Keep it on a private network, a VPN, or
behind an authenticating proxy until caller auth is added.

## Tests

```bash
cargo test                          # unit tests for request shaping
docker build -t system-one-mcp .     # container image

# black-box tests that talk MCP over HTTP
python scripts/run_all.py            # startup + protocol, no upstream cost
python -m unittest discover -s scripts -p test_package_binary.py -v
LIVE=1 python scripts/run_all.py     # adds real (billable) upstream calls
```

| Script | What it proves | Cost |
|---|---|---|
| `test_startup.py` | missing key/model aborts boot, blank key rejected, non-http endpoint rejected, host allow-list blocks foreign `Host`, API key never appears in logs or tool results | free |
| `test_protocol.py` | `initialize` / `tools/list` shape, exactly three tools, `model` never exposed as a parameter, bad arguments rejected, `invalid_params` guards (state type, empty list, 33 questions, duplicate ids) | free |
| `test_live.py` | Choice returns an offered option and coherent probabilities, Score stays in range without being snapped to an integer, Noul stays a 0–1 float, batching maps ids both ways, 32-question ceiling | ~$0.00008 / run |

The scripts use only the standard library — no `pip install`. `run_all.py`
starts and stops the server itself, forwarding any `SYSTEM_ONE_*` env vars, and
skips live tests unless `LIVE=1` is set so CI never spends your credits.

Set `MCP_TEST_BINARY` to an executable path to test a particular release binary
instead of auto-detecting `target/debug` or `target/release`. Package a locally
built supported target with
`python scripts/package_binary.py --target <target>` (Python 3.11+); outputs go
to `dist/`.

Set `MCP_DEBUG=1` to trace every JSON-RPC request to stderr when a session
misbehaves.
