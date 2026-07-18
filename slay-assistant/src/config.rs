use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ApiStyle {
    #[default]
    Openai,
    Anthropic,
}

impl ApiStyle {
    pub fn as_str(self) -> &'static str {
        match self {
            ApiStyle::Openai => "openai",
            ApiStyle::Anthropic => "anthropic",
        }
    }

    fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "openai" | "oai" => Some(ApiStyle::Openai),
            "anthropic" | "claude" => Some(ApiStyle::Anthropic),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Path this config was loaded from (not serialized).
    #[serde(skip)]
    pub config_path: Option<PathBuf>,

    /// Base URL of the STS2 game state API
    #[serde(default = "default_game_api_url")]
    pub game_api_url: String,

    /// LLM API configuration
    pub llm: LlmConfig,

    /// Hotkey combination
    #[serde(default = "default_hotkey")]
    pub hotkey: String,

    /// Auto-hide overlay after N milliseconds (0 = don't auto-hide)
    #[serde(default = "default_auto_hide_ms")]
    pub auto_hide_ms: u64,

    /// Show always-on-top egui window (Phase 3)
    #[serde(default = "default_overlay_enabled")]
    pub overlay_enabled: bool,
}

fn default_overlay_enabled() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmConfig {
    /// Request/response dialect: "openai" | "anthropic"
    #[serde(default)]
    pub api_style: ApiStyle,

    /// API base URL (no trailing slash required)
    #[serde(default = "default_llm_url")]
    pub base_url: String,

    /// API key (can also set env SLAY_LLM_API_KEY)
    #[serde(default)]
    pub api_key: String,

    /// Model name
    #[serde(default = "default_llm_model")]
    pub model: String,

    /// Max output tokens
    #[serde(default = "default_max_tokens")]
    pub max_tokens: u32,

    /// HTTP timeout in seconds
    #[serde(default = "default_timeout_secs")]
    pub timeout_secs: u64,

    /// Call remote LLM after local heuristics (false = 纯本地，秒出)
    #[serde(default = "default_llm_enabled")]
    pub enabled: bool,
}

fn default_llm_enabled() -> bool {
    true
}

fn default_game_api_url() -> String {
    "http://localhost:15526".into()
}

fn default_hotkey() -> String {
    "Ctrl+Shift+A".into()
}

fn default_auto_hide_ms() -> u64 {
    5000
}

fn default_llm_url() -> String {
    // Align with NarraFork custom provider "lv10"
    "https://ai.lv10.ren".into()
}

fn default_llm_model() -> String {
    // Default: Grok 4.5 (override in config.toml if your proxy uses another id)
    "grok-4.5".into()
}

fn default_max_tokens() -> u32 {
    2000
}

fn default_timeout_secs() -> u64 {
    // Prefer fail-fast when proxy is flaky; override in config.toml
    25
}

impl Config {
    /// Load config, apply env overrides, create default file if missing.
    pub fn load() -> Result<Self> {
        let path = Self::resolve_path()?;
        let mut config = if path.exists() {
            let content = std::fs::read_to_string(&path)
                .with_context(|| format!("Failed to read config at {}", path.display()))?;
            let mut c: Config =
                toml::from_str(&content).with_context(|| "Failed to parse config file")?;
            c.config_path = Some(path.clone());
            log::info!("Using config: {}", path.display());
            c
        } else {
            let mut c = Config::default_with_key();
            c.config_path = Some(path.clone());
            let content = toml::to_string_pretty(&c)?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&path, &content)?;
            log::info!("Created default config at {}", path.display());
            log::warn!("Edit api_key in that file, or set env SLAY_LLM_API_KEY");
            c
        };

        config.apply_env_overrides();

        if config.llm.api_key.trim().is_empty() {
            log::warn!(
                "LLM api_key empty — local heuristics only. Set key in {} or SLAY_LLM_API_KEY",
                path.display()
            );
        } else {
            log::info!(
                "LLM ready (style={}, model={})",
                config.llm.api_style.as_str(),
                config.llm.model
            );
        }

        Ok(config)
    }

    /// Env wins over file (so you can keep config.toml without secrets).
    ///
    /// - `SLAY_LLM_API_KEY`
    /// - `SLAY_LLM_BASE_URL`
    /// - `SLAY_LLM_MODEL` (supports NarraFork style `lv10:gpt-5.6-sol` → strips prefix)
    /// - `SLAY_LLM_API_STYLE` = openai | anthropic
    /// - `SLAY_GAME_API_URL`
    /// - `SLAY_HOTKEY`
    fn apply_env_overrides(&mut self) {
        if let Ok(v) = std::env::var("SLAY_LLM_API_KEY") {
            if !v.trim().is_empty() {
                self.llm.api_key = v;
                log::info!("api_key loaded from SLAY_LLM_API_KEY");
            }
        }
        if let Ok(v) = std::env::var("SLAY_LLM_BASE_URL") {
            if !v.trim().is_empty() {
                self.llm.base_url = v;
            }
        }
        if let Ok(v) = std::env::var("SLAY_LLM_MODEL") {
            if !v.trim().is_empty() {
                self.llm.model = v;
            }
        }
        if let Ok(v) = std::env::var("SLAY_LLM_API_STYLE") {
            if let Some(style) = ApiStyle::parse(&v) {
                self.llm.api_style = style;
            }
        }
        if let Ok(v) = std::env::var("SLAY_GAME_API_URL") {
            if !v.trim().is_empty() {
                self.game_api_url = v;
            }
        }
        if let Ok(v) = std::env::var("SLAY_HOTKEY") {
            if !v.trim().is_empty() {
                self.hotkey = v;
            }
        }
        if let Ok(v) = std::env::var("SLAY_SKIP_LLM") {
            if matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on") {
                self.llm.enabled = false;
                log::info!("LLM disabled via SLAY_SKIP_LLM");
            }
        }
        if let Ok(v) = std::env::var("SLAY_LLM_ENABLED") {
            match v.to_ascii_lowercase().as_str() {
                "0" | "false" | "no" | "off" => self.llm.enabled = false,
                "1" | "true" | "yes" | "on" => self.llm.enabled = true,
                _ => {}
            }
        }

        // NarraFork model ids look like "lv10:gpt-5.6-sol" / "deepseek:deepseek-v4-pro"
        self.llm.model = strip_provider_prefix(&self.llm.model);
    }

    fn resolve_path() -> Result<PathBuf> {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let exe = std::env::current_exe().ok();
        let exe_dir = exe
            .as_ref()
            .and_then(|p| p.parent())
            .map(|p| p.to_path_buf());

        let mut candidates: Vec<PathBuf> = Vec::new();
        candidates.push(cwd.join("config.toml"));
        if let Some(dir) = &exe_dir {
            if let Some(proj) = dir.parent().and_then(|p| p.parent()) {
                if proj.join("Cargo.toml").exists() {
                    candidates.push(proj.join("config.toml"));
                }
            }
            candidates.push(dir.join("config.toml"));
        }

        for c in &candidates {
            if c.exists() {
                return Ok(c.clone());
            }
        }

        // Prefer project root when developing
        if cwd.join("Cargo.toml").exists() {
            return Ok(cwd.join("config.toml"));
        }
        if let Some(dir) = &exe_dir {
            if let Some(proj) = dir.parent().and_then(|p| p.parent()) {
                if proj.join("Cargo.toml").exists() {
                    return Ok(proj.join("config.toml"));
                }
            }
            return Ok(dir.join("config.toml"));
        }
        Ok(cwd.join("config.toml"))
    }

    fn default_with_key() -> Self {
        Config {
            config_path: None,
            game_api_url: default_game_api_url(),
            llm: LlmConfig {
                api_style: ApiStyle::Openai,
                base_url: default_llm_url(),
                api_key: String::new(),
                model: default_llm_model(),
                max_tokens: default_max_tokens(),
                timeout_secs: default_timeout_secs(),
                enabled: true,
            },
            hotkey: default_hotkey(),
            auto_hide_ms: default_auto_hide_ms(),
            overlay_enabled: true,
        }
    }
}

/// `lv10:gpt-5.6-sol` → `gpt-5.6-sol` (API wants bare model id).
fn strip_provider_prefix(model: &str) -> String {
    if let Some((prefix, rest)) = model.split_once(':') {
        // only strip short provider prefixes, not times like "gpt-4:something" rare
        if !prefix.is_empty()
            && prefix.len() <= 32
            && prefix
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
            && !rest.is_empty()
        {
            return rest.to_string();
        }
    }
    model.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_lv10_prefix() {
        assert_eq!(strip_provider_prefix("lv10:gpt-5.6-sol"), "gpt-5.6-sol");
        assert_eq!(
            strip_provider_prefix("deepseek:deepseek-chat"),
            "deepseek-chat"
        );
        assert_eq!(strip_provider_prefix("deepseek-chat"), "deepseek-chat");
    }
}
