# syntax=docker/dockerfile:1

# ---- build stage -----------------------------------------------------------
FROM rust:1.97-slim-trixie AS builder

WORKDIR /src

# Compile dependencies first so a source-only change reuses the cached layer.
# A stub main is used to populate the dependency graph, then removed.
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src \
    && echo 'fn main() {}' > src/main.rs \
    && cargo build --release \
    && rm -rf src

COPY src ./src
# Touching main.rs guarantees Cargo rebuilds the crate even if timestamps tie.
RUN touch src/main.rs && cargo build --release

# ---- runtime stage ---------------------------------------------------------
FROM debian:trixie-slim AS runtime

# rustls is used for TLS, so no OpenSSL runtime is required. Only the CA bundle
# is needed to verify the upstream certificate.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home app

COPY --from=builder /src/target/release/system-one-mcp /usr/local/bin/system-one-mcp

# The server reads SYSTEM_ONE_API_KEY and SYSTEM_ONE_MODEL at startup and exits
# if either is missing, so they must be supplied at run time.
ENV MCP_BIND_ADDR=0.0.0.0:8080 \
    MCP_PATH=/mcp \
    RUST_LOG=system_one_mcp=info

USER app
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/system-one-mcp"]
