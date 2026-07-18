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
    ) -> Result<Self> {
        // Fail faster on dead networks; overall timeout still from config.
        let connect = Duration::from_secs(8.min(timeout_secs.max(5)));
        let http = reqwest::Client::builder()
            .connect_timeout(connect)
            .timeout(Duration::from_secs(timeout_secs.max(5)))
            .pool_max_idle_per_host(2)
            .build()?;
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

    /// Send a chat completion request.
    pub async fn chat(&self, system_prompt: &str, user_message: &str) -> Result<String> {
        if !self.enabled {
            bail!("LLM 已禁用（config llm.enabled=false 或 SLAY_SKIP_LLM=1）");
        }
        if !self.api_key_configured() {
            bail!("LLM api_key 为空 — 请编辑 config.toml 填入密钥");
        }

        match self.api_style {
            ApiStyle::Openai => self.chat_openai(system_prompt, user_message).await,
            ApiStyle::Anthropic => self.chat_anthropic(system_prompt, user_message).await,
        }
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
            bail!(
                "LLM API error {status}: {}",
                truncate_body(&body, 400)
            );
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
            bail!(
                "LLM API error {status}: {}",
                truncate_body(&body, 400)
            );
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
