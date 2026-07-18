mod analysis;
mod config;
mod game;
mod knowledge;
mod llm;
mod run_cache;
mod ui;

use anyhow::Result;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Instant;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let config = config::Config::load()?;
    let use_remote = config.llm.enabled && !config.llm.api_key.trim().is_empty();

    let overlay = ui::overlay::OverlayHandle::spawn(
        config.auto_hide_ms,
        config.overlay_enabled,
        config.overlay_click_through,
    );
    let run_cache = run_cache::RunCache::load_default();
    let overlay_ok = if config.overlay_enabled {
        overlay.wait_ready(std::time::Duration::from_millis(800))
    } else {
        false
    };

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
        "║ 悬浮窗: {}  |  穿透: {}  |  隐藏: {}ms",
        if config.overlay_enabled { "开" } else { "关" },
        if config.overlay_click_through {
            "开"
        } else {
            "关"
        },
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

    // Multi-thread so analysis tasks can run while the loop still recv() hotkeys.
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .worker_threads(2)
        .build()?;

    rt.block_on(async {
        let game_client = Arc::new(game::client::GameClient::new(Some(
            config.game_api_url.clone(),
        )));
        let proxy = config.resolve_llm_proxy();
        if let Some(ref p) = proxy {
            println!("✓ LLM 代理: {p}");
        }
        let llm_client = Arc::new(llm::LlmClient::new(
            config.llm.base_url.clone(),
            config.llm.api_key.clone(),
            config.llm.model.clone(),
            config.llm.max_tokens,
            config.llm.api_style,
            config.llm.timeout_secs,
            config.llm.enabled,
            proxy,
        )?);

        if game_client.ping().await {
            println!("✓ 游戏 API 可达: {}", game_client.base_url());
        } else {
            println!(
                "⚠ 游戏 API 暂不可达 ({}) — 请启动 STS2 + STS2_MCP",
                game_client.base_url()
            );
        }
        if !config.overlay_enabled {
            println!("○ 悬浮窗关闭（overlay_enabled=false，仅终端）");
        } else if overlay_ok || overlay.is_running() {
            println!("✓ egui 悬浮窗已就绪（置顶窗口「Slay Assistant」）");
        } else if let Some(err) = overlay.last_error() {
            println!("✗ 悬浮窗启动失败 — 请看上方 panic/日志");
            println!("  详情: {}", err.chars().take(120).collect::<String>());
            println!("  终端推荐仍可用；可设 overlay_enabled=false 关闭悬浮窗");
        } else {
            println!("⚠ 悬浮窗尚未确认就绪（可能被系统拦截或启动较慢）");
            println!("  请看任务栏是否有「Slay Assistant」窗口");
        }
        if use_remote {
            println!("✓ 本地秒出 + 可选 LLM（热键可打断）");
        } else {
            println!("✓ 纯本地模式");
        }
        println!("等待热键 {} …\n", config.hotkey);

        let cancel_gen = Arc::new(AtomicU64::new(0));
        let mut in_flight: Option<JoinHandle<()>> = None;

        loop {
            tokio::select! {
                biased;

                _ = rx.recv() => {
                    // Bump generation so in-flight LLM / publish checks bail out.
                    let my_gen = cancel_gen.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(h) = in_flight.take() {
                        h.abort();
                        println!("\n⚡ 打断旧分析，重新获取…\n");
                    }

                    let game = Arc::clone(&game_client);
                    let llm = Arc::clone(&llm_client);
                    let overlay_h = overlay.clone();
                    let cache = run_cache.clone();
                    let gen = Arc::clone(&cancel_gen);
                    let t0 = Instant::now();

                    in_flight = Some(tokio::spawn(async move {
                        process_trigger(
                            game.as_ref(),
                            llm.as_ref(),
                            &overlay_h,
                            &cache,
                            gen.as_ref(),
                            my_gen,
                        )
                        .await;
                        log::info!("Trigger done in {:?}", t0.elapsed());
                    }));
                }

                // Reap completed analysis so we don't leak join handles.
                _ = async {
                    match in_flight.as_mut() {
                        Some(h) => {
                            let _ = h.await;
                        }
                        None => std::future::pending::<()>().await,
                    }
                } => {
                    in_flight = None;
                }

                _ = tokio::signal::ctrl_c() => {
                    if let Some(h) = in_flight.take() {
                        h.abort();
                    }
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

fn screen_label(st: &game::state::ScreenType) -> &'static str {
    use game::state::ScreenType::*;
    match st {
        Shop => "商店",
        Combat => "战斗",
        Map => "地图",
        Event => "事件",
        Reward | BossReward => "奖励",
        Rest => "篝火",
        GameOver => "终局",
        _ => "未知",
    }
}

async fn process_trigger(
    game: &game::client::GameClient,
    llm: &llm::LlmClient,
    overlay: &ui::overlay::OverlayHandle,
    run_cache: &run_cache::RunCache,
    cancel_gen: &AtomicU64,
    my_gen: u64,
) {
    if cancel_gen.load(Ordering::SeqCst) != my_gen {
        return;
    }

    let (mut state, degraded) = match game.get_game_state().await {
        Ok(s) => (s, false),
        Err(e) => {
            let err_msg = e.to_string();
            let is_missing = err_msg.contains("MissingMethodException")
                || err_msg.contains("Method not found");
            let screen = is_missing
                .then(|| game::client::detect_screen_from_missing_method(&err_msg))
                .flatten();

            let fallback = screen.and_then(|sc| {
                run_cache
                    .degraded_state(sc)
                    .or_else(|| run_cache.minimal_state(sc))
            });

            match fallback {
                Some(s) => {
                    let has_snapshot = s.current_hp.is_some();
                    log::warn!(
                        "Degraded {:?} state from {} (mod API mismatch)",
                        s.screen_type,
                        if has_snapshot { "snapshot" } else { "card cache" }
                    );
                    println!(
                        "\n⚠ {}场景兼容降级：使用{}数据（STS2_MCP API 不匹配）",
                        screen_label(&s.screen_type),
                        if has_snapshot { "缓存快照" } else { "牌库缓存" }
                    );
                    (s, true)
                }
                None => {
                    if cancel_gen.load(Ordering::SeqCst) != my_gen {
                        return;
                    }
                    log::error!("Failed to get game state: {e}");
                    println!("\n❌ 无法获取游戏状态（无缓存可降级）\n   {e}\n");
                    overlay.publish(format!("错误: {e}"), vec![]);
                    return;
                }
            }
        }
    };

    if cancel_gen.load(Ordering::SeqCst) != my_gen {
        return;
    }

    // Learn owned cards only; fill empty deck for archetype scoring
    if !degraded {
        run_cache.observe(&state);
    }
    let deck_before = state.deck.len();
    run_cache.enrich(&mut state);
    if deck_before == 0 && !state.deck.is_empty() {
        println!(
            "📦 牌库缓存补全: API deck=0 → 使用本局拥有的 {} 张牌",
            state.deck.len()
        );
    }

    println!("\n{}", state.summary());
    println!(
        "场景: {:?} | 远程LLM: {} | 图节点: {} | 拥有牌: {} 种/{} 张",
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
            .unwrap_or(0),
        run_cache.unique_count(),
        run_cache.total_count()
    );

    let recommendations = analysis::analyze(&state, llm, overlay, cancel_gen, my_gen).await;

    if cancel_gen.load(Ordering::SeqCst) != my_gen {
        log::info!("Skip final publish — cancelled (gen {my_gen})");
        return;
    }

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
