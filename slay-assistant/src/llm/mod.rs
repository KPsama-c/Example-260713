pub mod prompts;

use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use std::time::Duration;

use crate::config::ApiStyle;

/// LLM client for OpenAI-compatible chat completions and Anthropic Messages API.
pub struct LlmClient {
    http: reqwest::Client,
    base_url: String,
    api_key: String,
    model: String,
    max_tokens: u32,
    api_style: ApiStyle,
    /// When false, analysis layers skip remote calls (local-only mode).
    enabled: bool,
}

// ── OpenAI-compatible ─────────────────────────────────────────

#[derive(Debug, Serialize)]
struct OpenaiChatRequest {
    model: String,
    messages: Vec<RoleContent>,
    max_tokens: u32,
    temperature: f32,
}

#[derive(Debug, Serialize, Deserialize)]
struct RoleContent {
    role: String,
    content: String,
}

#[derive(Debug, Deserialize)]
struct OpenaiChatResponse {
    choices: Vec<OpenaiChoice>,
}

#[derive(Debug, Deserialize)]
struct OpenaiChoice {
    message: OpenaiMessage,
}

#[derive(Debug, Deserialize)]
struct OpenaiMessage {
    content: Option<String>,
}

// ── Anthropic Messages ────────────────────────────────────────

#[derive(Debug, Serialize)]
struct AnthropicRequest {
    model: String,
    max_tokens: u32,
    temperature: f32,
    system: String,
    messages: Vec<RoleContent>,
}

#[derive(Debug, Deserialize)]
struct AnthropicResponse {
    content: Vec<AnthropicBlock>,
}

#[derive(Debug, Deserialize)]
struct AnthropicBlock {
    #[serde(rename = "type")]
    block_type: String,
    text: Option<String>,
}

impl LlmClient {
    pub fn new(
        base_url: String,
        api_key: String,
        model: String,
        max_tokens: u32,
        api_style: ApiStyle,
        timeout_secs: u64,
        enabled: bool,
        proxy: Option<String>,
    ) -> Result<Self> {
        // Fail faster on dead networks; overall timeout still from config.
        let connect = Duration::from_secs(8.min(timeout_secs.max(5)));
        let mut builder = reqwest::Client::builder()
            .connect_timeout(connect)
            .timeout(Duration::from_secs(timeout_secs.max(5)))
            .pool_max_idle_per_host(2);

        if let Some(proxy_url) = proxy.filter(|s| !s.trim().is_empty()) {
            log::info!("LLM HTTP proxy: {proxy_url}");
            builder = builder.proxy(reqwest::Proxy::all(proxy_url.trim())?);
        }

        let http = builder.build()?;
        Ok(LlmClient {
            http,
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key,
            model,
            max_tokens,
            api_style,
            enabled,
        })
    }

    pub fn api_key_configured(&self) -> bool {
        !self.api_key.trim().is_empty()
    }

    /// Local heuristics always run; remote LLM only if enabled + key present.
    pub fn should_call_remote(&self) -> bool {
        self.enabled && self.api_key_configured()
    }

    pub fn api_style(&self) -> ApiStyle {
        self.api_style
    }

    /// Send a chat completion request, with one retry on transient errors.
    pub async fn chat(&self, system_prompt: &str, user_message: &str) -> Result<String> {
        if !self.enabled {
            bail!("LLM 已禁用（config llm.enabled=false 或 SLAY_SKIP_LLM=1）");
        }
        if !self.api_key_configured() {
            bail!("LLM api_key 为空 — 请编辑 config.toml 填入密钥");
        }

        let mut last_err = None;
        for attempt in 0..2 {
            let result = match self.api_style {
                ApiStyle::Openai => self.chat_openai(system_prompt, user_message).await,
                ApiStyle::Anthropic => self.chat_anthropic(system_prompt, user_message).await,
            };
            match result {
                Ok(text) => return Ok(text),
                Err(e) => {
                    let msg = e.to_string();
                    let transient = msg.contains("连接")
                        || msg.contains("timeout")
                        || msg.contains("timed out")
                        || msg.contains("connection")
                        || msg.contains("dns")
                        || msg.contains("tls")
                        || msg.contains("reset")
                        || msg.contains("refused")
                        || msg.contains("eof")
                        || msg.contains("broken pipe");
                    // Also retry on server errors (5xx)
                    let server_err = msg.contains("HTTP 5");
                    if (transient || server_err) && attempt == 0 {
                        log::warn!("LLM transient error (attempt 1/2): {msg}. Retrying…");
                        last_err = Some(msg);
                        tokio::time::sleep(std::time::Duration::from_millis(600)).await;
                        continue;
                    }
                    return Err(e);
                }
            }
        }
        bail!("LLM retry exhausted: {}", last_err.unwrap_or_default());
    }

    async fn chat_openai(&self, system_prompt: &str, user_message: &str) -> Result<String> {
        let url = format!("{}/v1/chat/completions", self.base_url);
        let request = OpenaiChatRequest {
            model: self.model.clone(),
            messages: vec![
                RoleContent {
                    role: "system".into(),
                    content: system_prompt.into(),
                },
                RoleContent {
                    role: "user".into(),
                    content: user_message.into(),
                },
            ],
            max_tokens: self.max_tokens,
            temperature: 0.3,
        };

        let resp = self
            .http
            .post(&url)
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("Content-Type", "application/json")
            .json(&request)
            .send()
            .await
            .map_err(|e| {
                anyhow::anyhow!(
                    "无法连接 {url} ({e})。请检查：1) 能否浏览器打开该站 2) 系统代理/VPN 3) 或改用 DeepSeek 官方 base_url"
                )
            })?;

        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            bail!(rate_limit_msg(status, &body));
        }

        let data: OpenaiChatResponse = serde_json::from_str(&body).map_err(|e| {
            anyhow::anyhow!(
                "Failed to parse OpenAI response: {e}; body={}",
                truncate_body(&body, 300)
            )
        })?;

        let content = data
            .choices
            .first()
            .and_then(|c| c.message.content.clone())
            .unwrap_or_default();
        Ok(content)
    }

    async fn chat_anthropic(&self, system_prompt: &str, user_message: &str) -> Result<String> {
        let url = format!("{}/v1/messages", self.base_url);
        let request = AnthropicRequest {
            model: self.model.clone(),
            max_tokens: self.max_tokens,
            temperature: 0.3,
            system: system_prompt.into(),
            messages: vec![RoleContent {
                role: "user".into(),
                content: user_message.into(),
            }],
        };

        // Some Anthropic-compatible gateways accept either header.
        let resp = self
            .http
            .post(&url)
            .header("x-api-key", &self.api_key)
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("anthropic-version", "2023-06-01")
            .header("Content-Type", "application/json")
            .json(&request)
            .send()
            .await?;

        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            bail!(rate_limit_msg(status, &body));
        }

        let data: AnthropicResponse = serde_json::from_str(&body).map_err(|e| {
            anyhow::anyhow!(
                "Failed to parse Anthropic response: {e}; body={}",
                truncate_body(&body, 300)
            )
        })?;

        let content = data
            .content
            .into_iter()
            .filter(|b| b.block_type == "text")
            .filter_map(|b| b.text)
            .collect::<Vec<_>>()
            .join("\n");
        Ok(content)
    }
}

fn truncate_body(s: &str, max: usize) -> String {
    let t: String = s.chars().take(max).collect();
    if s.chars().count() > max {
        format!("{t}…")
    } else {
        t
    }
}

/// Produce a user-friendly message for HTTP 429 (rate limited).
fn rate_limit_msg(status: reqwest::StatusCode, body: &str) -> String {
    if status.as_u16() == 429 {
        let hint = extract_retry_after(body);
        if let Some(s) = hint {
            format!(
                "LLM 频率限制（429 Too Many Requests）— 建议等待 {}s 再试。原始: {}",
                s,
                truncate_body(body, 200)
            )
        } else {
            format!(
                "LLM 频率限制（429 Too Many Requests）— 稍等几秒再按热键。原始: {}",
                truncate_body(body, 200)
            )
        }
    } else {
        format!("LLM API error {}: {}", status.as_u16(), truncate_body(body, 400))
    }
}

/// Try to extract retry-after hint from a 429 error body or common header patterns.
fn extract_retry_after(body: &str) -> Option<String> {
    // OpenAI-style: "Please retry after X seconds"
    for pattern in &["retry after", "Retry after", "Please retry after"] {
        if let Some(pos) = body.find(pattern) {
            let after = &body[pos + pattern.len()..];
            // Grab the next number
            let num: String = after.chars().skip_while(|c| !c.is_ascii_digit()).take_while(|c| c.is_ascii_digit()).collect();
            if !num.is_empty() {
                return Some(num);
            }
        }
    }
    // Try JSON: {"error": {"message": "Rate limit exceeded. Please wait 30 seconds."}}
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(body) {
        if let Some(msg) = v.get("error").and_then(|e| e.get("message").or(Some(e))).and_then(|m| m.as_str()) {
            for pattern in &["retry after", "wait", "seconds"] {
                if msg.contains(pattern) {
                    let num: String = msg.chars().skip_while(|c| !c.is_ascii_digit()).take_while(|c| c.is_ascii_digit()).collect();
                    if !num.is_empty() {
                        return Some(num);
                    }
                }
            }
        }
    }
    None
}
