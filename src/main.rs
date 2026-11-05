mod config;
mod http;
mod server;
mod upstream;

use config::Config;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "system_one_mcp=info,tower_http=info".into()),
        )
        .with_writer(std::io::stderr)
        .init();

    // Missing API key or model is fatal: fail before accepting traffic rather
    // than surfacing a broken configuration on the first tool call.
    let config = Config::from_env()?;
    http::run(config).await
}
