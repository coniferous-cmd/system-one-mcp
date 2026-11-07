use std::sync::Arc;

use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
};
use tokio_util::sync::CancellationToken;
use tracing::info;

use crate::{config::Config, server::DecisionServer, upstream::Upstream};

pub async fn run(config: Config) -> Result<(), Box<dyn std::error::Error>> {
    let upstream = Arc::new(Upstream::new(&config)?);
    let shutdown = CancellationToken::new();

    let mut http_config = StreamableHttpServerConfig::default()
        .with_json_response(true)
        .with_sse_keep_alive(None)
        .with_cancellation_token(shutdown.child_token());

    // The SDK only accepts loopback Host headers by default. A remote deployment
    // must declare its public hostnames, otherwise every request is rejected.
    if config.allowed_hosts.iter().any(|host| host == "*") {
        http_config = http_config.disable_allowed_hosts();
    } else if !config.allowed_hosts.is_empty() {
        http_config = http_config.with_allowed_hosts(config.allowed_hosts.clone());
    }

    let service = StreamableHttpService::new(
        {
            let upstream = upstream.clone();
            move || Ok(DecisionServer::new(upstream.clone()))
        },
        Arc::new(LocalSessionManager::default()),
        http_config,
    );

    let router = axum::Router::new().nest_service(config.mcp_path.as_str(), service);
    let listener = tokio::net::TcpListener::bind(&config.bind_addr).await?;

    info!(
        endpoint = %config.endpoint,
        model = %config.model,
        path = %config.mcp_path,
        "decision-mcp listening on {}",
        listener.local_addr()?
    );

    axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_signal(shutdown))
        .await?;

    Ok(())
}

async fn shutdown_signal(token: CancellationToken) {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };

    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut signal) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            signal.recv().await;
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }

    info!("shutdown signal received");
    token.cancel();
}
