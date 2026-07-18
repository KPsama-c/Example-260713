use anyhow::{bail, Result};
use reqwest::Client;
use std::time::Duration;

use super::adapter;
use super::state::GameState;

const DEFAULT_BASE_URL: &str = "http://localhost:15526";
const DEFAULT_TIMEOUT_SECS: u64 = 2;

/// Client for querying game state from the STS2 MCP mod API.
pub struct GameClient {
    http: Client,
    base_url: String,
}

impl GameClient {
    pub fn new(base_url: Option<String>) -> Self {
        Self::with_timeout(base_url, DEFAULT_TIMEOUT_SECS)
    }

    pub fn with_timeout(base_url: Option<String>, timeout_secs: u64) -> Self {
        let http = Client::builder()
            .timeout(Duration::from_secs(timeout_secs.max(1)))
            .connect_timeout(Duration::from_secs(2))
            .pool_max_idle_per_host(2)
            .build()
            .unwrap_or_else(|_| Client::new());
        GameClient {
            http,
            base_url: base_url.unwrap_or_else(|| DEFAULT_BASE_URL.into()),
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Fetch the full game state (STS2_MCP v0.4).
    pub async fn get_game_state(&self) -> Result<GameState> {
        // Only real endpoints — legacy /state /game-state always 404 on v0.4 and
        // would hide the useful singleplayer error if used as last_err.
        let primary = format!(
            "{}/api/v1/singleplayer",
            self.base_url.trim_end_matches('/')
        );
        let multi = format!(
            "{}/api/v1/multiplayer",
            self.base_url.trim_end_matches('/')
        );

        // 1) singleplayer first
        match self.fetch_parse(&primary).await {
            Ok(state) => return Ok(state),
            Err(e_sp) => {
                log::warn!("singleplayer failed: {e_sp}");
                // 2) multiplayer only if SP clearly "wrong mode" / not available
                match self.fetch_parse(&multi).await {
                    Ok(state) => return Ok(state),
                    Err(e_mp) => {
                        log::warn!("multiplayer failed: {e_mp}");
                        let mod_up = self.ping().await;
                        if !mod_up {
                            bail!(
                                "连不上 STS2_MCP（{}）。请确认：1) 游戏已启动 2) mods 里已加载 STS2_MCP 3) 浏览器能打开 http://localhost:15526/",
                                self.base_url
                            );
                        }
                        // STS2_MCP bug: shop/merchant reflection crash on newer game builds
                        let sp = e_sp.to_string();
                        if is_mod_merchant_bug(&sp) {
                            bail!(
                                "商店场景触发 STS2_MCP 已知兼容问题（无法读 MerchantRoom.Inventory）。\n\
                                 · 原因：Mod 调用的 get_Inventory() 在当前游戏版本中不存在（MissingMethodException）\n\
                                 · 处理：先【离开商店】回到地图/战斗后再按热键\n\
                                 · 根治：更新 STS2_MCP 到支持当前游戏版本的构建（商店 Inventory API 变更）\n\
                                 · 原始错误: {}",
                                truncate_err(&sp, 220)
                            );
                        }
                        if sp.contains("MissingMethodException") || sp.contains("Method not found") {
                            bail!(
                                "STS2_MCP 与当前游戏版本 API 不匹配（MissingMethodException）。\n\
                                 · 处理：换场景重试；或升级/更换兼容的 STS2_MCP 版本\n\
                                 · 详情: {}",
                                truncate_err(&sp, 240)
                            );
                        }
                        bail!(
                            "Mod 在线，但读不到战局。\n\
                             · 单人: {}\n\
                             · 多人: {}\n\
                             请先【进入一局单人游戏】（地图/战斗中），不要停在主菜单。\n\
                             自检: 浏览器打开 {} 应返回 JSON。",
                            truncate_err(&sp, 180),
                            truncate_err(&e_mp.to_string(), 120),
                            primary
                        );
                    }
                }
            }
        }
    }

    async fn fetch_parse(&self, url: &str) -> Result<GameState> {
        let resp = self
            .http
            .get(url)
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("连接失败 {url}: {e}"))?;

        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();

        if status.as_u16() == 409 {
            // e.g. multiplayer endpoint while in singleplayer
            let msg = extract_api_error(&text).unwrap_or_else(|| text.chars().take(200).collect());
            bail!("HTTP 409: {msg}");
        }

        if !status.is_success() {
            let msg = extract_api_error(&text).unwrap_or_else(|| {
                if text.is_empty() {
                    status.to_string()
                } else {
                    text.chars().take(200).collect()
                }
            });
            bail!("HTTP {status}: {msg}");
        }

        // Success body may still be an error envelope
        if let Some(msg) = extract_api_error(&text) {
            if !text.contains("state_type") && !text.contains("screen_type") {
                bail!("API: {msg}");
            }
        }

        adapter::parse_game_state_json(&text).map_err(|e| {
            let snip: String = text.chars().take(240).collect();
            anyhow::anyhow!("解析失败: {e} | body: {snip}")
        })
    }

    /// Quick check: mod HTTP is up (root hello is enough).
    pub async fn ping(&self) -> bool {
        let url = format!("{}/", self.base_url.trim_end_matches('/'));
        match self.http.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => true,
            _ => false,
        }
    }
}

fn extract_api_error(text: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    v.get("error")
        .and_then(|e| {
            if let Some(s) = e.as_str() {
                Some(s.to_string())
            } else {
                e.get("message")
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string())
            }
        })
        .or_else(|| {
            v.get("message")
                .and_then(|m| m.as_str())
                .map(|s| s.to_string())
        })
}

pub(crate) fn is_mod_merchant_bug(msg: &str) -> bool {
    let m = msg.to_ascii_lowercase();
    (m.contains("merchant") || m.contains("inventory"))
        && (m.contains("get_inventory")
            || m.contains("missingmethod")
            || m.contains("method not found"))
}

/// Try to detect the game screen from a MissingMethodException error body.
/// Returns None if the scene can't be determined.
pub(crate) fn detect_screen_from_missing_method(msg: &str) -> Option<crate::game::state::ScreenType> {
    use crate::game::state::ScreenType;
    let m = msg.to_ascii_lowercase();
    if m.contains("merchantroom") || m.contains("merchantinventory") || m.contains("merchant") {
        return Some(ScreenType::Shop);
    }
    // Combat-related: CombatManager, IsPlayPhase, IsPlayFeature, etc.
    if m.contains("combat") {
        return Some(ScreenType::Combat);
    }
    // Extend with more patterns as new mod bugs appear
    None
}

fn truncate_err(s: &str, max: usize) -> String {
    let n = s.chars().count();
    if n <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}

impl Default for GameClient {
    fn default() -> Self {
        Self::new(None)
    }
}
