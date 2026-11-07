use std::env;

/// Runtime configuration, loaded from the process environment.
///
/// The API key is only ever held here and attached to outgoing requests; it is
/// never logged, echoed in tool results, or exposed as a tool parameter.
#[derive(Clone, Debug)]
pub struct Config {
    /// Full upstream endpoint, e.g. `https://system-one.dev/v1/systemone`.
    pub endpoint: String,
    pub api_key: String,
    pub model: String,
    pub bind_addr: String,
    pub mcp_path: String,
    pub allowed_hosts: Vec<String>,
    pub request_timeout_secs: u64,
}

#[derive(Debug)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ConfigError {}

impl Config {
    pub fn from_env() -> Result<Self, ConfigError> {
        let api_key = required("DECISION_API_KEY")?;
        let model = required("DECISION_MODEL")?;

        let endpoint = env::var("DECISION_ENDPOINT")
            .ok()
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| "https://system-one.dev/v1/systemone".to_string());

        if !endpoint.starts_with("http://") && !endpoint.starts_with("https://") {
            return Err(ConfigError(format!(
                "DECISION_ENDPOINT must start with http:// or https://, got {endpoint:?}"
            )));
        }

        let bind_addr = env::var("MCP_BIND_ADDR")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "0.0.0.0:8080".to_string());

        let mcp_path = env::var("MCP_PATH")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "/mcp".to_string());
        let mcp_path = if mcp_path.starts_with('/') {
            mcp_path
        } else {
            format!("/{mcp_path}")
        };

        // Streamable HTTP rejects non-loopback Host headers unless explicitly
        // allow-listed, so a remote deployment must declare its hostnames.
        let allowed_hosts = env::var("MCP_ALLOWED_HOSTS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(str::to_string)
            .collect::<Vec<_>>();

        let request_timeout_secs = env::var("DECISION_TIMEOUT_SECS")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .map(|v| {
                v.parse::<u64>().map_err(|_| {
                    ConfigError(format!(
                        "DECISION_TIMEOUT_SECS must be an integer, got {v:?}"
                    ))
                })
            })
            .transpose()?
            .unwrap_or(120);

        Ok(Self {
            endpoint,
            api_key,
            model,
            bind_addr,
            mcp_path,
            allowed_hosts,
            request_timeout_secs,
        })
    }
}

fn required(key: &str) -> Result<String, ConfigError> {
    env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .ok_or_else(|| ConfigError(format!("{key} is required but not set")))
}
