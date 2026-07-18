mod analysis;
mod config;
mod game;
mod llm;
mod ui;

use anyhow::Result;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Instant;
use tokio::sync::mpsc;

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let config = config::Config::load()?;
    let use_remote = config.llm.enabled && !config.llm.api_key.trim().is_empty();

    let overlay = ui::overlay::OverlayHandle::spawn(config.auto_hide_ms, config.overlay_enabled);

    println!();
    println!("╔══════════════════════════════════════════════════╗");
    println!("║   Slay Assistant v0.3 · 悬浮窗 + BFS 选路       ║");
    println!("╠══════════════════════════════════════════════════╣");
    if let Some(p) = &config.config_path {
        println!("║ 配置: {}", short_path(p));
    }
    println!("║ 游戏: {}", config.game_api_url);
    println!("║ 热键: {}  |  退出: Ctrl+C", config.hotkey);
    println!(
        "║ LLM:  {} | {} | {}",
        config.llm.api_style.as_str(),
        config.llm.model,
        short_url(&config.llm.base_url)
    );
    println!(
        "║ 悬浮窗: {}  |  自动隐藏: {}ms",
        if config.overlay_enabled { "开" } else { "关" },
        config.auto_hide_ms
    );
    if !config.llm.enabled {
        println!("║ 模式: 纯本地");
    } else if config.llm.api_key.trim().is_empty() {
        println!("║ 模式: 纯本地（无 key）");
    } else {
        println!("║ 模式: 本地 + LLM（timeout={}s）", config.llm.timeout_secs);
    }
    println!("╚══════════════════════════════════════════════════╝");
    println!();

    let (tx, mut rx) = mpsc::unbounded_channel::<()>();
    let hotkey = config.hotkey.clone();
    thread::spawn(move || {
        if let Err(e) = run_hotkey_loop(&hotkey, tx) {
            log::error!("Hotkey thread error: {e}");
        }
    });

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;

    rt.block_on(async {
        let game_client = game::client::GameClient::new(Some(config.game_api_url.clone()));
        let llm_client = llm::LlmClient::new(
            config.llm.base_url.clone(),
            config.llm.api_key.clone(),
            config.llm.model.clone(),
            config.llm.max_tokens,
            config.llm.api_style,
            config.llm.timeout_secs,
            config.llm.enabled,
        )?;

        if game_client.ping().await {
            println!("✓ 游戏 API 可达: {}", game_client.base_url());
        } else {
            println!(
                "⚠ 游戏 API 暂不可达 ({}) — 请启动 STS2 + STS2_MCP",
                game_client.base_url()
            );
        }
        if overlay.enabled() {
            println!("✓ egui 悬浮窗已启动（置顶）");
        }
        if use_remote {
            println!("✓ 本地秒出 + 可选 LLM");
        } else {
            println!("✓ 纯本地模式");
        }
        println!("等待热键 {} …\n", config.hotkey);

        let busy = Arc::new(AtomicBool::new(false));

        loop {
            tokio::select! {
                _ = rx.recv() => {
                    if busy.swap(true, Ordering::SeqCst) {
                        println!("\n⏳ 分析中，忽略重复热键…\n");
                        continue;
                    }
                    let t0 = Instant::now();
                    process_trigger(&game_client, &llm_client, &overlay).await;
                    log::info!("Trigger done in {:?}", t0.elapsed());
                    busy.store(false, Ordering::SeqCst);
                }
                _ = tokio::signal::ctrl_c() => {
                    println!("退出。");
                    break;
                }
            }
        }

        Ok::<_, anyhow::Error>(())
    })?;

    Ok(())
}

fn short_path(p: &std::path::Path) -> String {
    let s = p.display().to_string();
    if s.chars().count() <= 44 {
        s
    } else {
        let t: String = s.chars().rev().take(41).collect::<String>().chars().rev().collect();
        format!("…{t}")
    }
}

fn short_url(u: &str) -> String {
    if u.chars().count() <= 36 {
        u.to_string()
    } else {
        format!("{}…", u.chars().take(34).collect::<String>())
    }
}

async fn process_trigger(
    game: &game::client::GameClient,
    llm: &llm::LlmClient,
    overlay: &ui::overlay::OverlayHandle,
) {
    let state = match game.get_game_state().await {
        Ok(s) => s,
        Err(e) => {
            log::error!("Failed to get game state: {e}");
            println!("\n❌ 无法获取游戏状态\n   {e}\n");
            overlay.publish(format!("错误: {e}"), vec![]);
            return;
        }
    };

    println!("\n{}", state.summary());
    println!(
        "场景: {:?} | 远程LLM: {} | 图节点: {}",
        state.screen_type,
        if llm.should_call_remote() {
            "开"
        } else {
            "关"
        },
        state
            .map_state
            .as_ref()
            .map(|m| m.nodes.len())
            .unwrap_or(0)
    );

    let recommendations = analysis::analyze(&state, llm).await;
    let summary = state.summary();
    overlay.publish(summary, recommendations.clone());

    let preprinted = matches!(
        state.screen_type,
        game::state::ScreenType::Map
            | game::state::ScreenType::Reward
            | game::state::ScreenType::BossReward
            | game::state::ScreenType::Combat
            | game::state::ScreenType::Shop
            | game::state::ScreenType::Event
            | game::state::ScreenType::Rest
    );

    if recommendations.iter().any(|r| r.title.contains("LLM")) {
        println!("—— 合并结果（本地 + LLM）——");
        ui::overlay::show_recommendations(&recommendations);
    } else if preprinted {
        println!("（本地建议见上；已同步悬浮窗）\n");
    } else {
        ui::overlay::show_recommendations(&recommendations);
    }
}

fn run_hotkey_loop(hotkey: &str, tx: mpsc::UnboundedSender<()>) -> Result<()> {
    use win_hotkeys::HotkeyManager;

    let mut hkm = HotkeyManager::new();
    let (mods, key) = parse_hotkey(hotkey)?;

    hkm.register_hotkey(key, &mods, move || {
        let _ = tx.send(());
    })?;

    log::info!("Hotkey registered: {hotkey}");
    hkm.event_loop();
    Ok(())
}

fn parse_hotkey(s: &str) -> Result<(Vec<win_hotkeys::VKey>, win_hotkeys::VKey)> {
    let parts: Vec<&str> = s.split('+').map(|p| p.trim()).collect();
    if parts.len() < 2 {
        anyhow::bail!("Hotkey must include modifier+key, e.g. Ctrl+Shift+A");
    }
    let key_str = parts.last().unwrap();
    let mut mods = Vec::new();
    for part in &parts[..parts.len() - 1] {
        let mod_name = match part.to_lowercase().as_str() {
            "ctrl" | "control" => "CONTROL",
            "alt" | "menu" => "MENU",
            "shift" => "SHIFT",
            "win" | "windows" | "super" => "LWIN",
            other => anyhow::bail!("Unknown modifier: {other}"),
        };
        mods.push(win_hotkeys::VKey::from_keyname(mod_name)?);
    }
    let trigger = win_hotkeys::VKey::from_keyname(key_str)?;
    Ok((mods, trigger))
}
