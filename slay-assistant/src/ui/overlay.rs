//! Phase 3: always-on-top egui overlay + console panel.

use crate::analysis::Recommendation;
use eframe::egui;
use parking_lot::Mutex;
use std::sync::Arc;
use std::time::{Duration, Instant};

#[derive(Clone)]
pub struct OverlayHandle {
    inner: Arc<Mutex<OverlayState>>,
}

struct OverlayState {
    summary: String,
    recs: Vec<Recommendation>,
    /// Force show after each trigger
    pulse: u64,
    auto_hide_ms: u64,
    hide_at: Option<Instant>,
    enabled: bool,
    /// Set true once eframe event loop is running
    running: bool,
    /// Last fatal error from overlay thread
    last_error: Option<String>,
    click_through: bool,
}

impl OverlayHandle {
    pub fn spawn(auto_hide_ms: u64, enabled: bool, click_through: bool) -> Self {
        let inner = Arc::new(Mutex::new(OverlayState {
            summary: "等待热键…".into(),
            recs: vec![],
            pulse: 0,
            auto_hide_ms,
            hide_at: None,
            enabled,
            running: false,
            last_error: None,
            click_through,
        }));

        if enabled {
            let ui_state = inner.clone();
            let result = std::thread::Builder::new()
                .name("slay-overlay".into())
                .spawn(move || {
                    // Catch panics so main process keeps running
                    let state_for_err = ui_state.clone();
                    let click_through = ui_state.lock().click_through;
                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        run_eframe(ui_state.clone(), click_through)
                    }));
                    match result {
                        Ok(Ok(())) => {
                            log::info!("Overlay UI exited normally");
                            ui_state.lock().running = false;
                        }
                        Ok(Err(e)) => {
                            let msg = format!("eframe error: {e}");
                            log::error!("Overlay UI: {msg}");
                            let mut g = state_for_err.lock();
                            g.running = false;
                            g.last_error = Some(msg);
                        }
                        Err(panic) => {
                            let msg = if let Some(s) = panic.downcast_ref::<&str>() {
                                (*s).to_string()
                            } else if let Some(s) = panic.downcast_ref::<String>() {
                                s.clone()
                            } else {
                                "unknown panic in overlay thread".into()
                            };
                            log::error!("Overlay panic: {msg}");
                            let mut g = state_for_err.lock();
                            g.running = false;
                            g.last_error = Some(msg);
                        }
                    }
                });
            if let Err(e) = result {
                inner.lock().last_error = Some(format!("failed to spawn overlay thread: {e}"));
            }
        }

        Self { inner }
    }

    pub fn publish(&self, summary: String, recs: Vec<Recommendation>) {
        let mut g = self.inner.lock();
        g.summary = summary;
        g.recs = recs;
        g.pulse = g.pulse.wrapping_add(1);
        if g.auto_hide_ms > 0 {
            g.hide_at = Some(Instant::now() + Duration::from_millis(g.auto_hide_ms));
        } else {
            g.hide_at = None;
        }
    }

    pub fn enabled(&self) -> bool {
        self.inner.lock().enabled
    }

    /// True if the egui loop has marked itself running.
    pub fn is_running(&self) -> bool {
        self.inner.lock().running
    }

    pub fn last_error(&self) -> Option<String> {
        self.inner.lock().last_error.clone()
    }

    /// Wait briefly for overlay to start (or fail).
    pub fn wait_ready(&self, timeout: Duration) -> bool {
        if !self.enabled() {
            return false;
        }
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            let g = self.inner.lock();
            if g.running {
                return true;
            }
            if g.last_error.is_some() {
                return false;
            }
            drop(g);
            std::thread::sleep(Duration::from_millis(50));
        }
        self.inner.lock().running
    }
}

/// Terminal box (always available).
pub fn show_recommendations(recommendations: &[Recommendation]) {
    const W: usize = 52;
    let bar = "═".repeat(W);
    println!();
    println!("╔{bar}╗");
    println!("║{:^w$}║", "Slay the Spire 2 助手", w = W);
    println!("╠{bar}╣");
    if recommendations.is_empty() {
        println!("║ {:<w$}║", "(无推荐结果)", w = W - 1);
    }
    for (i, rec) in recommendations.iter().enumerate() {
        if i > 0 {
            println!("║{:-<w$}║", "", w = W);
        }
        let head = format!("#{}. {}", rec.rank, rec.title);
        for line in wrap_lines(&head, W - 2) {
            println!("║ {:<w$}║", line, w = W - 1);
        }
        if !rec.description.is_empty() {
            for line in wrap_lines(&rec.description, W - 4) {
                println!("║   {:<w$}║", line, w = W - 3);
            }
        }
    }
    println!("╚{bar}╝");
    println!();
}

fn wrap_lines(text: &str, width: usize) -> Vec<String> {
    let width = width.max(8);
    let mut out = Vec::new();
    for para in text.split('\n') {
        let para = para.trim();
        if para.is_empty() {
            continue;
        }
        let mut line = String::new();
        for ch in para.chars() {
            line.push(ch);
            if line.chars().count() >= width {
                out.push(std::mem::take(&mut line));
            }
        }
        if !line.is_empty() {
            out.push(line);
        }
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

fn run_eframe(state: Arc<Mutex<OverlayState>>, click_through: bool) -> Result<(), eframe::Error> {
    let mut options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([420.0, 560.0])
            .with_min_inner_size([300.0, 200.0])
            .with_always_on_top()
            .with_title("Slay Assistant"),
        ..Default::default()
    };

    // Windows: overlay runs on a background thread; allow EventLoop off main thread.
    #[cfg(target_os = "windows")]
    {
        use winit::platform::windows::EventLoopBuilderExtWindows;
        options.event_loop_builder = Some(Box::new(|builder| {
            builder.with_any_thread(true);
        }));
    }

    // Mark running as soon as we enter eframe setup (window is about to show).
    state.lock().running = true;

    eframe::run_native(
        "Slay Assistant",
        options,
        Box::new(move |cc| {
            install_cjk_fonts(&cc.egui_ctx);
            Ok(Box::new(OverlayApp {
                state,
                last_pulse: 0,
                click_through,
                click_through_applied: false,
            }))
        }),
    )
}

/// Windows: let mouse clicks pass through the overlay window (raw FFI, no windows crate conflict).
#[cfg(target_os = "windows")]
fn apply_click_through(window_title: &str) {
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "user32")]
    extern "system" {
        fn FindWindowW(lp_class: *const u16, lp_window: *const u16) -> isize;
        fn GetWindowLongW(hwnd: isize, index: i32) -> i32;
        fn SetWindowLongW(hwnd: isize, index: i32, new_long: i32) -> i32;
    }
    const GWL_EXSTYLE: i32 = -20;
    const WS_EX_LAYERED: i32 = 0x0008_0000;
    const WS_EX_TRANSPARENT: i32 = 0x0000_0020;

    let title: Vec<u16> = std::ffi::OsStr::new(window_title)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let hwnd = FindWindowW(std::ptr::null(), title.as_ptr());
        if hwnd == 0 {
            log::warn!("click-through: window '{window_title}' not found yet");
            return;
        }
        let ex = GetWindowLongW(hwnd, GWL_EXSTYLE);
        let new_ex = ex | WS_EX_LAYERED | WS_EX_TRANSPARENT;
        SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex);
        log::info!("Overlay click-through enabled (HWND={hwnd})");
    }
}

#[cfg(not(target_os = "windows"))]
fn apply_click_through(_window_title: &str) {}
/// egui default fonts have almost no CJK glyphs → Chinese shows as □.
/// Prefer Windows system fonts (YaHei / SimHei / SimSun).
fn install_cjk_fonts(ctx: &egui::Context) {
    // Prefer single-font TTF/OTF — TTC collections sometimes fail in ab_glyph.
    let candidates = [
        r"C:\Windows\Fonts\simhei.ttf", // 黑体 (TTF, 兼容最好)
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\msyh.ttc",   // 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simsun.ttc", // 宋体
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\NotoSansSC-Regular.otf",
        r"C:\Windows\Fonts\SourceHanSansSC-Regular.otf",
    ];

    let mut font_bytes: Option<Vec<u8>> = None;
    let mut used = String::new();
    for path in candidates {
        match std::fs::read(path) {
            Ok(data) if !data.is_empty() => {
                font_bytes = Some(data);
                used = path.to_string();
                break;
            }
            _ => continue,
        }
    }

    let Some(data) = font_bytes else {
        log::warn!(
            "No CJK font found under C:\\Windows\\Fonts — overlay Chinese may show as tofu (□)"
        );
        return;
    };

    log::info!("Overlay CJK font: {used}");

    let mut fonts = egui::FontDefinitions::default();
    fonts.font_data.insert(
        "cjk".to_owned(),
        std::sync::Arc::new(egui::FontData::from_owned(data)),
    );

    // Put CJK first so Han characters resolve; Latin still falls through default fonts.
    fonts
        .families
        .entry(egui::FontFamily::Proportional)
        .or_default()
        .insert(0, "cjk".to_owned());
    fonts
        .families
        .entry(egui::FontFamily::Monospace)
        .or_default()
        .insert(0, "cjk".to_owned());

    ctx.set_fonts(fonts);
}

// ── Overlay styling helpers ──

/// Extract key stats from the summary line and render as colored badges.
fn render_summary_badges(ui: &mut egui::Ui, summary: &str) {
    // Parse: "Screen: Combat | HP: 68/80 | Gold: 250 | Deck: 15 cards | Relics: 5"
    let mut hp = "";
    let mut gold = "";
    let mut screen = "";

    for segment in summary.split('|') {
        let s = segment.trim();
        if s.starts_with("HP:") {
            hp = s.strip_prefix("HP:").unwrap_or("").trim();
        } else if s.starts_with("Gold:") {
            gold = s.strip_prefix("Gold:").unwrap_or("").trim();
        } else if s.starts_with("Screen:") {
            screen = s.strip_prefix("Screen:").unwrap_or("").trim();
        }
    }

    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing.x = 4.0;
        if !screen.is_empty() {
            badge(ui, screen, egui::Color32::from_rgb(160, 200, 240));
        }
        if !hp.is_empty() {
            let color = if let Some((cur, max)) = hp.split_once('/') {
                let cur: i32 = cur.trim().parse().unwrap_or(0);
                let max: i32 = max.trim().parse().unwrap_or(1);
                if cur * 100 / max.max(1) < 30 {
                    egui::Color32::from_rgb(255, 100, 100)
                } else if cur * 100 / max.max(1) < 60 {
                    egui::Color32::from_rgb(255, 180, 50)
                } else {
                    egui::Color32::from_rgb(100, 220, 100)
                }
            } else {
                egui::Color32::WHITE
            };
            badge(ui, &format!("❤ {hp}"), color);
        }
        if !gold.is_empty() {
            badge(ui, &format!("🪙 {gold}"), egui::Color32::from_rgb(255, 210, 80));
        }
    });
}

fn badge(ui: &mut egui::Ui, text: &str, color: egui::Color32) {
    egui::Frame::new()
        .fill(egui::Color32::from_rgba_premultiplied(
            color.r() / 4,
            color.g() / 4,
            color.b() / 4,
            180,
        ))
        .corner_radius(4)
        .inner_margin(egui::Margin::symmetric(4, 2))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).color(color).size(11.0));
        });
}

/// (icon, accent_color) for a recommendation card.
fn card_style(rec: &crate::analysis::Recommendation) -> (&'static str, egui::Color32) {
    let t = &rec.title;
    let d = &rec.description;

    // Danger detection
    let danger = d.contains("危险") || d.contains("不够") || d.contains("无法") || d.contains("失败");
    let success = t.contains("斩杀") && !d.contains("还差");

    if success || t.contains("可击杀") {
        ("🗡️", egui::Color32::from_rgb(100, 230, 100))
    } else if danger || rec.rank >= 3 {
        ("⚠️", egui::Color32::from_rgb(255, 110, 90))
    } else if t.contains("格挡") {
        ("🛡️", egui::Color32::from_rgb(130, 200, 255))
    } else if t.contains("能量") {
        ("⚡", egui::Color32::from_rgb(230, 200, 80))
    } else if t.contains("出牌") || t.contains("顺序") {
        ("🎯", egui::Color32::from_rgb(200, 160, 255))
    } else if t.contains("商店") {
        ("🛒", egui::Color32::from_rgb(255, 210, 80))
    } else if t.contains("路线") || t.contains("选路") || t.contains("地图") {
        ("🗺️", egui::Color32::from_rgb(130, 220, 180))
    } else if t.contains("奖励") || t.contains("选牌") {
        ("🃏", egui::Color32::from_rgb(220, 180, 100))
    } else if t.contains("知识") {
        ("📚", egui::Color32::from_rgb(180, 200, 220))
    } else if t.contains("LLM") {
        ("🤖", egui::Color32::from_rgb(180, 140, 255))
    } else if t.contains("篝火") || t.contains("休息") {
        ("🔥", egui::Color32::from_rgb(255, 160, 80))
    } else if t.contains("事件") {
        ("📜", egui::Color32::from_rgb(200, 180, 150))
    } else if t.contains("等待") {
        ("⏳", egui::Color32::from_rgb(160, 160, 160))
    } else {
        ("📋", egui::Color32::from_rgb(180, 200, 220))
    }
}

fn color_bg(accent: egui::Color32) -> egui::Color32 {
    egui::Color32::from_rgba_premultiplied(
        accent.r() / 6,
        accent.g() / 6,
        accent.b() / 6,
        200,
    )
}

// ── eframe App ──

struct OverlayApp {
    state: Arc<Mutex<OverlayState>>,
    last_pulse: u64,
    click_through: bool,
    click_through_applied: bool,
}

impl eframe::App for OverlayApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Apply once after window exists
        if self.click_through && !self.click_through_applied {
            apply_click_through("Slay Assistant");
            self.click_through_applied = true;
        }

        let (summary, recs, pulse, hide_at) = {
            let g = self.state.lock();
            (
                g.summary.clone(),
                g.recs.clone(),
                g.pulse,
                g.hide_at,
            )
        };

        if pulse != self.last_pulse {
            self.last_pulse = pulse;
            // Don't steal focus when click-through (playing game underneath)
            if !self.click_through {
                ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            }
            ctx.request_repaint();
        }

        // auto-hide dimming: still show window but can show "hidden" banner
        let expired = hide_at.map(|t| Instant::now() >= t).unwrap_or(false);

        egui::CentralPanel::default().show(ctx, |ui| {
            // ── Header ──
            ui.heading("Slay Assistant");

            // Summary badges
            if !summary.is_empty() {
                render_summary_badges(ui, &summary);
            }
            ui.separator();

            if expired && recs.is_empty() {
                ui.label("按 Ctrl+Shift+A 获取分析…");
            } else if expired {
                ui.colored_label(egui::Color32::GRAY, "⏰ 自动隐藏 — 再按热键刷新");
            }

            // ── Recommendations ──
            egui::ScrollArea::vertical().show(ui, |ui| {
                for rec in &recs {
                    let (icon, accent) = card_style(rec);
                    let bg = color_bg(accent);

                    egui::Frame::new()
                        .fill(bg)
                        .corner_radius(6)
                        .inner_margin(egui::Margin::same(8))
                        .show(ui, |ui| {
                            ui.vertical(|ui| {
                                ui.horizontal(|ui| {
                                    ui.label(egui::RichText::new(icon).size(16.0));
                                    ui.label(
                                        egui::RichText::new(&rec.title)
                                            .strong()
                                            .color(accent)
                                            .size(13.0),
                                    );
                                });
                                if !rec.description.is_empty() {
                                    ui.add_space(3.0);
                                    ui.label(
                                        egui::RichText::new(&rec.description)
                                            .size(11.0)
                                            .color(egui::Color32::from_gray(200)),
                                    );
                                }
                            });
                        });
                    ui.add_space(3.0);
                }
            });

            // ── Footer ──
            ui.separator();
            ui.small(if self.click_through {
                "Ctrl+Shift+A 刷新 · 鼠标穿透中 · 悬浮窗"
            } else {
                "Ctrl+Shift+A 刷新 · 窗口可点击"
            });
        });

        // repaint periodically for auto-hide countdown
        ctx.request_repaint_after(Duration::from_millis(500));
    }
}
