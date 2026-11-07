# syntax=docker/dockerfile:1

# ---- build stage -----------------------------------------------------------
# Alpine's Rust toolchain targets *-unknown-linux-musl, so the resulting binary
# is statically linked and does not depend on a glibc dynamic loader.
FROM rust:1.97-alpine AS builder

WORKDIR /src

# musl-dev provides the headers/linker glue used by ring's C/asm build.
RUN apk add --no-cache musl-dev

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
FROM alpine:3.22 AS runtime

# rustls is used for TLS, so no OpenSSL runtime is required. Only the CA bundle
# is needed to verify the upstream certificate.
RUN apk add --no-cache ca-certificates \
    && adduser --system --uid 10001 --home /home/app app

COPY --from=builder /src/target/release/decision-mcp /usr/local/bin/decision-mcp

# The server reads DECISION_API_KEY and DECISION_MODEL at startup and exits
# if either is missing, so they must be supplied at run time.
ENV MCP_BIND_ADDR=0.0.0.0:8080 \
    MCP_PATH=/mcp \
    RUST_LOG=decision_mcp=info

USER app
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/decision-mcp"]
