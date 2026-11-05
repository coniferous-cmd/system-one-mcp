use std::time::Duration;

use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::config::Config;

/// Maximum number of questions the upstream gateway accepts per request.
const MAX_QUESTIONS: usize = 32;

/// A single choice option: the option name plus its optional description.
#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct ChoiceOption {
    /// Option identifier returned in the answer, e.g. `billing`.
    pub name: String,
    /// What this option means. Strongly recommended; helps the model decide.
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct ChoiceQuestion {
    /// Unique question id, returned verbatim in the answer. 1-100 chars of
    /// `A-Z a-z 0-9 _ . -`.
    pub id: String,
    /// What the model must decide, in natural language.
    pub instructions: String,
    /// Candidate options. At least two are recommended.
    pub criteria: Vec<ChoiceOption>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct ScoreQuestion {
    /// Unique question id, returned verbatim in the answer.
    pub id: String,
    /// What the model must rate, in natural language.
    pub instructions: String,
    /// Ordered rating levels, lowest first. 2-10 non-empty strings.
    pub criteria: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct NoulCriteria {
    /// Meaning of the answer being `true`.
    #[serde(rename = "true", default)]
    pub when_true: Option<String>,
    /// Meaning of the answer being `false`.
    #[serde(rename = "false", default)]
    pub when_false: Option<String>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct NoulQuestion {
    /// Unique question id, returned verbatim in the answer.
    pub id: String,
    /// The yes/no proposition to evaluate.
    pub instructions: String,
    /// Optional descriptions of the two outcomes.
    #[serde(default)]
    pub criteria: Option<NoulCriteria>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct ChoiceParams {
    /// Shared context for every question: a string, JSON object, or JSON array.
    pub state: Value,
    /// One or more choice questions evaluated against the same `state`.
    pub questions: Vec<ChoiceQuestion>,
    /// Optional stable key. Reuse the same key when retrying the same request so
    /// the upstream does not bill the operation twice.
    #[serde(default)]
    pub idempotency_key: Option<String>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct ScoreParams {
    /// Shared context for every question: a string, JSON object, or JSON array.
    pub state: Value,
    /// One or more score questions evaluated against the same `state`.
    pub questions: Vec<ScoreQuestion>,
    /// Optional stable key. Reuse the same key when retrying the same request so
    /// the upstream does not bill the operation twice.
    #[serde(default)]
    pub idempotency_key: Option<String>,
}

#[derive(Debug, Clone, Deserialize, schemars::JsonSchema)]
pub struct NoulParams {
    /// Shared context for every question: a string, JSON object, or JSON array.
    pub state: Value,
    /// One or more yes/no questions evaluated against the same `state`.
    pub questions: Vec<NoulQuestion>,
    /// Optional stable key. Reuse the same key when retrying the same request so
    /// the upstream does not bill the operation twice.
    #[serde(default)]
    pub idempotency_key: Option<String>,
}

/// Upstream call failure, shaped so the MCP caller can act on it.
#[derive(Debug)]
pub struct UpstreamError {
    pub status: Option<u16>,
    pub code: Option<String>,
    pub message: String,
    pub request_id: Option<String>,
}

impl UpstreamError {
    pub fn to_json(&self) -> Value {
        json!({
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "request_id": self.request_id,
        })
    }
}

pub struct Upstream {
    http: reqwest::Client,
    endpoint: String,
    api_key: String,
    model: String,
}

/// Result of one upstream call, keeping raw answers untouched.
pub struct UpstreamOutcome {
    pub request_model: String,
    pub response: Value,
    pub idempotency_key: String,
    pub request_id: Option<String>,
    pub generation_id: Option<String>,
    pub provider: Option<String>,
    pub credits: Option<String>,
    pub replayed: Option<String>,
}

impl Upstream {
    pub fn new(config: &Config) -> Result<Self, reqwest::Error> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(config.request_timeout_secs))
            .build()?;
        Ok(Self {
            http,
            endpoint: config.endpoint.clone(),
            api_key: config.api_key.clone(),
            model: config.model.clone(),
        })
    }

    pub async fn choice(&self, params: ChoiceParams) -> Result<UpstreamOutcome, UpstreamError> {
        let questions = params
            .questions
            .iter()
            .map(|q| {
                let mut criteria = Map::new();
                for option in &q.criteria {
                    criteria.insert(
                        option.name.clone(),
                        option
                            .description
                            .clone()
                            .map(Value::String)
                            .unwrap_or(Value::Null),
                    );
                }
                (
                    q.id.clone(),
                    json!({
                        "type": "choice",
                        "instructions": q.instructions,
                        "criteria": Value::Object(criteria),
                    }),
                )
            })
            .collect::<Vec<_>>();
        self.post(params.state, questions, params.idempotency_key)
            .await
    }

    pub async fn score(&self, params: ScoreParams) -> Result<UpstreamOutcome, UpstreamError> {
        let questions = params
            .questions
            .iter()
            .map(|q| {
                (
                    q.id.clone(),
                    json!({
                        "type": "score",
                        "instructions": q.instructions,
                        "criteria": q.criteria,
                    }),
                )
            })
            .collect::<Vec<_>>();
        self.post(params.state, questions, params.idempotency_key)
            .await
    }

    pub async fn noul(&self, params: NoulParams) -> Result<UpstreamOutcome, UpstreamError> {
        let questions = params
            .questions
            .iter()
            .map(|q| {
                let mut question = json!({
                    "type": "noul",
                    "instructions": q.instructions,
                });
                if let Some(criteria) = &q.criteria {
                    let mut map = Map::new();
                    if let Some(text) = &criteria.when_true {
                        map.insert("true".into(), Value::String(text.clone()));
                    }
                    if let Some(text) = &criteria.when_false {
                        map.insert("false".into(), Value::String(text.clone()));
                    }
                    if !map.is_empty() {
                        question["criteria"] = Value::Object(map);
                    }
                }
                (q.id.clone(), question)
            })
            .collect::<Vec<_>>();
        self.post(params.state, questions, params.idempotency_key)
            .await
    }

    async fn post(
        &self,
        state: Value,
        questions: Vec<(String, Value)>,
        idempotency_key: Option<String>,
    ) -> Result<UpstreamOutcome, UpstreamError> {
        validate_state(&state)?;
        if questions.is_empty() {
            return Err(UpstreamError {
                status: None,
                code: Some("invalid_params".into()),
                message: "questions must contain at least one question".into(),
                request_id: None,
            });
        }
        if questions.len() > MAX_QUESTIONS {
            return Err(UpstreamError {
                status: None,
                code: Some("invalid_params".into()),
                message: format!("at most {MAX_QUESTIONS} questions per request"),
                request_id: None,
            });
        }

        let mut map = Map::new();
        for (id, question) in questions {
            if map.insert(id.clone(), question).is_some() {
                return Err(UpstreamError {
                    status: None,
                    code: Some("invalid_params".into()),
                    message: format!("duplicate question id: {id}"),
                    request_id: None,
                });
            }
        }

        let idempotency_key = idempotency_key
            .map(|k| k.trim().to_string())
            .filter(|k| !k.is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        let body = json!({
            "model": self.model,
            "state": state,
            "questions": Value::Object(map),
        });

        let response = self
            .http
            .post(&self.endpoint)
            .bearer_auth(&self.api_key)
            .header("Content-Type", "application/json")
            .header("Idempotency-Key", &idempotency_key)
            .json(&body)
            .send()
            .await
            .map_err(|e| UpstreamError {
                status: None,
                code: Some("transport_error".into()),
                message: format!("request to {} failed: {e}", self.endpoint),
                request_id: None,
            })?;

        let status = response.status();
        let headers = response.headers().clone();
        let text = response.text().await.map_err(|e| UpstreamError {
            status: Some(status.as_u16()),
            code: Some("transport_error".into()),
            message: format!("failed to read upstream body: {e}"),
            request_id: None,
        })?;

        let request_id = header(&headers, "x-request-id")
            .or_else(|| header(&headers, "x-generation-id"))
            .or_else(|| header(&headers, "x-typesafe-request-id"));

        let parsed: Value = serde_json::from_str(&text).unwrap_or(Value::String(text));

        if !status.is_success() {
            let (code, message) = upstream_error_detail(&parsed);
            return Err(UpstreamError {
                status: Some(status.as_u16()),
                code: code.or_else(|| header(&headers, "x-system-one-error-code")),
                message,
                request_id,
            });
        }

        Ok(UpstreamOutcome {
            request_model: self.model.clone(),
            response: parsed,
            idempotency_key,
            request_id,
            generation_id: header(&headers, "x-generation-id"),
            provider: header(&headers, "x-provider-name"),
            credits: header(&headers, "x-system-one-credits"),
            replayed: header(&headers, "x-idempotency-replayed"),
        })
    }
}

/// Extract a message/code from either System One or OpenRouter style errors.
fn upstream_error_detail(body: &Value) -> (Option<String>, String) {
    if let Some(error) = body.get("error") {
        let code = error
            .get("code")
            .map(|c| match c {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .or_else(|| {
                error
                    .get("type")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            });
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| error.to_string());
        return (code, message);
    }
    (None, body.to_string())
}

fn header(headers: &reqwest::header::HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string)
}

fn validate_state(state: &Value) -> Result<(), UpstreamError> {
    match state {
        Value::String(_) | Value::Object(_) | Value::Array(_) => Ok(()),
        _ => Err(UpstreamError {
            status: None,
            code: Some("invalid_params".into()),
            message: "state must be a string, JSON object, or JSON array".into(),
            request_id: None,
        }),
    }
}
