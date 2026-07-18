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
}

impl OverlayHandle {
    pub fn spawn(auto_hide_ms: u64, enabled: bool) -> Self {
        let inner = Arc::new(Mutex::new(OverlayState {
            summary: "等待热键…".into(),
            recs: vec![],
            pulse: 0,
            auto_hide_ms,
            hide_at: None,
            enabled,
        }));

        if enabled {
            let ui_state = inner.clone();
            std::thread::Builder::new()
                .name("slay-overlay".into())
                .spawn(move || {
                    if let Err(e) = run_eframe(ui_state) {
                        log::error!("Overlay UI exited: {e}");
                    }
                })
                .ok();
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

fn run_eframe(state: Arc<Mutex<OverlayState>>) -> Result<(), eframe::Error> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([420.0, 560.0])
            .with_min_inner_size([300.0, 200.0])
            .with_always_on_top()
            .with_title("Slay Assistant"),
        ..Default::default()
    };

    eframe::run_native(
        "Slay Assistant",
        options,
        Box::new(move |_cc| Ok(Box::new(OverlayApp { state, last_pulse: 0 }))),
    )
}

struct OverlayApp {
    state: Arc<Mutex<OverlayState>>,
    last_pulse: u64,
}

impl eframe::App for OverlayApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
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
            // bring attention
            ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            ctx.request_repaint();
        }

        // auto-hide dimming: still show window but can show "hidden" banner
        let expired = hide_at.map(|t| Instant::now() >= t).unwrap_or(false);

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("Slay Assistant");
            ui.label(egui::RichText::new(&summary).strong());
            ui.separator();

            if expired && recs.is_empty() {
                ui.label("等待热键…");
            } else if expired {
                ui.colored_label(egui::Color32::GRAY, "（自动隐藏计时到 — 再按热键刷新）");
            }

            egui::ScrollArea::vertical().show(ui, |ui| {
                for rec in &recs {
                    ui.group(|ui| {
                        ui.label(
                            egui::RichText::new(format!("#{}. {}", rec.rank, rec.title)).strong(),
                        );
                        if !rec.description.is_empty() {
                            ui.label(&rec.description);
                        }
                    });
                    ui.add_space(4.0);
                }
            });

            ui.separator();
            ui.small("热键刷新 · 置顶窗口 · 与终端同步输出");
        });

        // repaint periodically for auto-hide countdown
        ctx.request_repaint_after(Duration::from_millis(500));
    }
}
