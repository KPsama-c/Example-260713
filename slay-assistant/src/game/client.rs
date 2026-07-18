use anyhow::Result;
use reqwest::Client;
use std::time::Duration;

use super::adapter;
use super::state::GameState;

const DEFAULT_BASE_URL: &str = "http://localhost:15526";
const DEFAULT_TIMEOUT_SECS: u64 = 3;

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

    /// Fetch the full game state.
    /// Prefer STS2 MCP v0.4 paths; fall back to older aliases.
    pub async fn get_game_state(&self) -> Result<GameState> {
        // STS2_MCP v0.4.0 (confirmed live):
        //   GET /api/v1/singleplayer  → run state
        //   GET /api/v1/multiplayer   → 409 if not in MP
        //   GET /                    → health hello
        let paths = [
            "/api/v1/singleplayer",
            "/api/v1/multiplayer",
            // legacy guesses (other mods / older docs)
            "/api/v1/game-state",
            "/api/v1/game_state",
            "/game-state",
            "/state",
        ];

        let mut last_err = None;
        for path in &paths {
            let url = format!("{}{}", self.base_url, path);
            match self.http.get(&url).send().await {
                Ok(resp) => {
                    let status = resp.status();
                    let text = resp.text().await.unwrap_or_default();
                    if status.is_success() {
                        match adapter::parse_game_state_json(&text) {
                            Ok(state) => {
                                log::info!(
                                    "Game state OK from {url} (screen={:?}, hp={:?}/{:?})",
                                    state.screen_type,
                                    state.current_hp,
                                    state.max_hp
                                );
                                return Ok(state);
                            }
                            Err(e) => {
                                let snip: String = text.chars().take(400).collect();
                                log::warn!("Parse failed for {url}: {e}; snippet={snip}");
                                last_err = Some(anyhow::anyhow!("Parse error from {url}: {e}"));
                            }
                        }
                    } else if status.as_u16() == 409 {
                        // multiplayer endpoint when in singleplayer — skip
                        log::debug!("GET {url} → 409 (wrong mode), try next");
                        last_err = Some(anyhow::anyhow!("GET {url} returned 409: {text}"));
                    } else {
                        log::debug!("GET {url} → {status}");
                        last_err = Some(anyhow::anyhow!("GET {url} returned {status}"));
                    }
                }
                Err(e) => {
                    last_err = Some(anyhow::anyhow!("Connection error for {url}: {e}"));
                }
            }
        }

        Err(last_err.unwrap_or_else(|| {
            anyhow::anyhow!(
                "Could not get game state from {}. Is STS2_MCP loaded and a run active?",
                self.base_url
            )
        }))
    }

    /// Quick check: mod HTTP is up (root hello is enough).
    pub async fn ping(&self) -> bool {
        let url = format!("{}/", self.base_url.trim_end_matches('/'));
        if let Ok(resp) = self.http.get(&url).send().await {
            if resp.status().is_success() {
                return true;
            }
        }
        // Also try singleplayer
        let url = format!("{}/api/v1/singleplayer", self.base_url.trim_end_matches('/'));
        matches!(self.http.get(&url).send().await, Ok(r) if r.status().is_success())
    }
}

impl Default for GameClient {
    fn default() -> Self {
        Self::new(None)
    }
}
