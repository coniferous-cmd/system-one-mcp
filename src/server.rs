use std::sync::Arc;

use rmcp::{
    ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, Implementation, ServerCapabilities, ServerConfig},
    tool, tool_handler, tool_router,
};
use serde_json::{Map, Value, json};

use crate::upstream::{ChoiceParams, NoulParams, ScoreParams, Upstream, UpstreamOutcome};

const INSTRUCTIONS: &str = "\
This server exposes System One structured decision primitives. Each tool sends one upstream \
request for one or more questions of the same type, sharing a single `state` context.\n\
- `Choice`: pick one named option per question.\n\
- `Score`: rate each question on an ordered list of levels; the score may be fractional.\n\
- `Noul`: return the probability that each proposition is true (0-1), not a boolean.\n\
Results are returned verbatim from the upstream: probabilities, confidence, and score \
precision are not rounded or reinterpreted. Question ids in the result match the ids you sent. \
The model is configured server-side and cannot be chosen per call.";

#[derive(Clone)]
pub struct SystemOneServer {
    upstream: Arc<Upstream>,
    tool_router: ToolRouter<Self>,
}

#[tool_router(router = tool_router)]
impl SystemOneServer {
    pub fn new(upstream: Arc<Upstream>) -> Self {
        Self {
            upstream,
            tool_router: Self::tool_router(),
        }
    }

    /// Ask one or more multiple-choice questions about a shared context.
    #[tool(
        name = "Choice",
        description = "Pick one named option per question from a shared context. Sends a single \
upstream request; every question must be a choice question. Returns the chosen option, per-option \
probabilities, and confidence for each question id."
    )]
    async fn choice(&self, Parameters(params): Parameters<ChoiceParams>) -> CallToolResult {
        match self.upstream.choice(params).await {
            Ok(outcome) => success(outcome),
            Err(err) => failure(&err),
        }
    }

    /// Rate one or more questions on ordered levels about a shared context.
    #[tool(
        name = "Score",
        description = "Rate each question on an ordered list of levels (lowest first) from a \
shared context. Sends a single upstream request; every question must be a score question. Returns \
a possibly fractional score, per-level probabilities, the level legend, and confidence per \
question id."
    )]
    async fn score(&self, Parameters(params): Parameters<ScoreParams>) -> CallToolResult {
        match self.upstream.score(params).await {
            Ok(outcome) => success(outcome),
            Err(err) => failure(&err),
        }
    }

    /// Ask one or more yes/no questions, answered as probabilities.
    #[tool(
        name = "Noul",
        description = "Return the probability (0-1) that each yes/no proposition holds, given a \
shared context. Sends a single upstream request; every question must be a noul question. The \
result is a probability, not a boolean, so do not treat 0.97 as certain."
    )]
    async fn noul(&self, Parameters(params): Parameters<NoulParams>) -> CallToolResult {
        match self.upstream.noul(params).await {
            Ok(outcome) => success(outcome),
            Err(err) => failure(&err),
        }
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for SystemOneServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(
                "system-one-mcp",
                env!("CARGO_PKG_VERSION"),
            ))
            .with_instructions(INSTRUCTIONS)
    }
}

/// Build the tool result without reshaping upstream answers.
fn success(outcome: UpstreamOutcome) -> CallToolResult {
    let mut result = Map::new();

    if let Some(answers) = outcome.response.get("answers") {
        result.insert("answers".into(), answers.clone());
    }
    // Preserve every other upstream field (e.g. `model`, `usage`, `id`,
    // `provider`) except `answers`, which is already promoted above.
    if let Some(object) = outcome.response.as_object() {
        for (key, value) in object {
            result.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }

    result.insert(
        "requested_model".into(),
        Value::String(outcome.request_model.clone()),
    );
    result.insert(
        "idempotency_key".into(),
        Value::String(outcome.idempotency_key.clone()),
    );

    let mut meta = Map::new();
    insert_opt(&mut meta, "request_id", outcome.request_id);
    insert_opt(&mut meta, "generation_id", outcome.generation_id);
    insert_opt(&mut meta, "provider", outcome.provider);
    // Billing metadata is reported by headers when available; absence means
    // unknown, not zero.
    insert_opt(&mut meta, "credits", outcome.credits);
    insert_opt(&mut meta, "replayed", outcome.replayed);
    if !meta.is_empty() {
        result.insert("meta".into(), Value::Object(meta));
    }

    CallToolResult::structured(Value::Object(result))
}

fn failure(err: &crate::upstream::UpstreamError) -> CallToolResult {
    // `is_error` so the calling model sees the failure instead of treating it
    // as an answer.
    CallToolResult::structured_error(json!({ "error": err.to_json() }))
}

fn insert_opt(map: &mut Map<String, Value>, key: &str, value: Option<String>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::String(value));
    }
}
