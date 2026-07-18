pub mod combat;
pub mod events;
pub mod heuristics;
pub mod map;
pub mod map_bfs;
pub mod pipeline;
pub mod rewards;
pub mod shop;

use crate::game::state::{GameState, ScreenType};
use crate::llm::{prompts, LlmClient};
use crate::ui::overlay::OverlayHandle;
use std::sync::atomic::AtomicU64;

/// Analyze the current game state and return recommendations.
pub async fn analyze(
    state: &GameState,
    llm: &LlmClient,
    overlay: &OverlayHandle,
    cancel_gen: &AtomicU64,
    my_gen: u64,
) -> Vec<Recommendation> {
    match state.screen_type {
        ScreenType::Combat => combat::analyze(state, llm, overlay, cancel_gen, my_gen).await,
        ScreenType::Map => map::analyze(state, llm, overlay, cancel_gen, my_gen).await,
        ScreenType::Shop => shop::analyze(state, llm, overlay, cancel_gen, my_gen).await,
        ScreenType::Event => events::analyze(state, llm, overlay, cancel_gen, my_gen).await,
        ScreenType::Reward | ScreenType::BossReward => rewards::analyze(state, llm, overlay, cancel_gen, my_gen).await,
        ScreenType::Rest => rest_fallback(state, llm, overlay, cancel_gen, my_gen).await,
        _ => vec![Recommendation {
            rank: 1,
            title: "等待中".into(),
            description: format!("当前场景: {:?} — 暂无专项分析", state.screen_type),
        }],
    }
}

async fn rest_fallback(
    state: &GameState,
    llm: &LlmClient,
    overlay: &OverlayHandle,
    cancel_gen: &AtomicU64,
    my_gen: u64,
) -> Vec<Recommendation> {
    let local = heuristics::analyze_rest_local(state);
    pipeline::local_then_llm(
        state,
        llm,
        overlay,
        cancel_gen,
        my_gen,
        local,
        "本地篝火建议（秒出）",
        |json| {
            format!(
                "当前在篝火/休息点。以下是游戏状态：\n\n{json}\n\n请建议休息回血还是升级哪张卡，必须给出明确选择。"
            )
        },
        true,
    )
    .await
}

/// Shared LLM path: serialize → prompt → chat → parse.
pub async fn run_llm_analysis<F>(
    state: &GameState,
    llm: &LlmClient,
    build_user: F,
) -> Vec<Recommendation>
where
    F: FnOnce(&str) -> String,
{
    if !llm.api_key_configured() {
        return vec![Recommendation {
            rank: 1,
            title: "配置缺失".into(),
            description: "LLM api_key 为空，请编辑 config.toml 后重试".into(),
        }];
    }

    let payload = state_for_llm(state);
    let json = match serde_json::to_string_pretty(&payload) {
        Ok(s) => s,
        Err(e) => {
            return vec![Recommendation {
                rank: 1,
                title: "序列化失败".into(),
                description: e.to_string(),
            }];
        }
    };

    let user = build_user(&json);
    log::info!(
        "Calling LLM ({}) for screen {:?}…",
        llm.api_style().as_str(),
        state.screen_type
    );

    match llm.chat(prompts::SYSTEM_PREFIX, &user).await {
        Ok(text) => {
            let mut recs = parse_recommendations(&text);
            if recs.is_empty() {
                recs.push(Recommendation {
                    rank: 1,
                    title: "LLM 回复".into(),
                    description: truncate_text(&text, 1200),
                });
            }
            recs
        }
        Err(e) => {
            log::error!("LLM failed: {e}");
            vec![Recommendation {
                rank: 1,
                title: "LLM 调用失败".into(),
                description: e.to_string(),
            }]
        }
    }
}

/// Slim combat payload; other screens send a still-compact snapshot.
fn state_for_llm(state: &GameState) -> serde_json::Value {
    match state.screen_type {
        ScreenType::Combat => slim_combat(state),
        _ => slim_general(state),
    }
}

fn slim_general(state: &GameState) -> serde_json::Value {
    // Cap deck / map size so hotkey LLM stays under token budget.
    let deck: Vec<_> = state.deck.iter().take(40).collect();
    let deck_truncated = state.deck.len().saturating_sub(deck.len());
    let map = state.map_state.as_ref().map(|m| {
        let nodes: Vec<_> = m.nodes.iter().take(80).collect();
        serde_json::json!({
            "current_node_id": m.current_node_id,
            "boss_id": m.boss_id,
            "next_options": m.next_options,
            "nodes": nodes,
            "nodes_truncated": m.nodes.len().saturating_sub(nodes.len()),
        })
    });
    serde_json::json!({
        "screen_type": state.screen_type,
        "character": state.character,
        "act": state.act,
        "floor": state.floor,
        "ascension_level": state.ascension_level,
        "current_hp": state.current_hp,
        "max_hp": state.max_hp,
        "gold": state.gold,
        "deck": deck,
        "deck_truncated": deck_truncated,
        "deck_size": state.deck.len(),
        "relics": state.relics,
        "potions": state.potions,
        "map_state": map,
        "shop_state": state.shop_state,
        "event_state": state.event_state,
        "reward_state": state.reward_state,
        "rest_state": state.rest_state,
        // Full combat only needed on combat screen (use slim_combat).
        "combat_state": serde_json::Value::Null,
    })
}

fn slim_combat(state: &GameState) -> serde_json::Value {
    let combat = state.combat_state.as_ref();
    serde_json::json!({
        "screen_type": "combat",
        "character": state.character,
        "act": state.act,
        "floor": state.floor,
        "current_hp": state.current_hp,
        "max_hp": state.max_hp,
        "gold": state.gold,
        "relics": state.relics.iter().map(|r| {
            serde_json::json!({ "name": r.name, "counter": r.counter })
        }).collect::<Vec<_>>(),
        "potions": state.potions.iter().map(|p| p.name.clone()).collect::<Vec<_>>(),
        "deck_size": state.deck.len(),
        "combat": combat.map(|c| {
            serde_json::json!({
                "turn": c.turn,
                "energy": c.energy,
                "max_energy": c.max_energy,
                "block": c.block,
                "powers": c.powers,
                "hand": c.hand,
                "draw_pile_count": c.draw_pile.len(),
                "discard_pile_count": c.discard_pile.len(),
                "exhaust_pile_count": c.exhaust_pile.len(),
                "enemies": c.enemies,
            })
        }),
    })
}

/// Parse LLM markdown-ish three-section output into recommendations.
pub fn parse_recommendations(text: &str) -> Vec<Recommendation> {
    let text = text.trim();
    if text.is_empty() {
        return Vec::new();
    }

    // Split by numbered sections or known headings.
    let markers: &[(u32, &[&str])] = &[
        (1, &["推荐操作", "推荐", "**推荐", "1.", "1、", "1 "]),
        (2, &["备选方案", "备选", "**备选", "2.", "2、", "2 "]),
        (3, &["不推荐", "**不推荐", "3.", "3、", "3 "]),
    ];

    let mut sections: Vec<(u32, String)> = Vec::new();
    let mut current_rank: Option<u32> = None;
    let mut current_buf = String::new();

    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if current_rank.is_some() {
                current_buf.push('\n');
            }
            continue;
        }

        let mut matched_rank = None;
        for (rank, keys) in markers {
            for key in *keys {
                if line_starts_section(trimmed, key) {
                    matched_rank = Some(*rank);
                    break;
                }
            }
            if matched_rank.is_some() {
                break;
            }
        }

        if let Some(rank) = matched_rank {
            if let Some(prev) = current_rank {
                let body = current_buf.trim().to_string();
                if !body.is_empty() {
                    sections.push((prev, body));
                }
            }
            current_rank = Some(rank);
            // Keep remainder of the heading line as content start.
            let after = strip_section_prefix(trimmed);
            current_buf = if after.is_empty() {
                String::new()
            } else {
                after.to_string()
            };
        } else if current_rank.is_some() {
            if !current_buf.is_empty() {
                current_buf.push('\n');
            }
            current_buf.push_str(trimmed);
        }
    }

    if let Some(prev) = current_rank {
        let body = current_buf.trim().to_string();
        if !body.is_empty() {
            sections.push((prev, body));
        }
    }

    if sections.is_empty() {
        return vec![Recommendation {
            rank: 1,
            title: "分析结果".into(),
            description: truncate_text(text, 1200),
        }];
    }

    sections
        .into_iter()
        .map(|(rank, body)| {
            let (title, description) = split_title_body(&body, rank);
            Recommendation {
                rank,
                title,
                description: truncate_text(&description, 800),
            }
        })
        .collect()
}

fn line_starts_section(line: &str, key: &str) -> bool {
    let l = line.trim_start_matches(['*', '#', '-', ' ']);
    l.starts_with(key)
        || l.to_lowercase().starts_with(&key.to_lowercase())
}

fn strip_section_prefix(line: &str) -> &str {
    let mut s = line.trim_start_matches(['*', '#', ' ']);
    // Drop leading "1." / "2、" etc.
    if let Some(rest) = s.strip_prefix(|c: char| c.is_ascii_digit()) {
        s = rest.trim_start_matches(['.', '、', ')', ' ', '*', ':', '：']);
    }
    for prefix in [
        "推荐操作",
        "推荐",
        "备选方案",
        "备选",
        "不推荐",
        "操作",
        "方案",
    ] {
        if let Some(rest) = s.strip_prefix(prefix) {
            s = rest.trim_start_matches(['*', ' ', ':', '：', '-', ')']);
            break;
        }
    }
    s.trim()
}

fn split_title_body(body: &str, rank: u32) -> (String, String) {
    let default_title = match rank {
        1 => "推荐操作",
        2 => "备选方案",
        3 => "不推荐",
        _ => "建议",
    };

    let body = body.trim();
    // First non-empty line as title if short; rest as description.
    let mut lines = body.lines().map(str::trim).filter(|l| !l.is_empty());
    if let Some(first) = lines.next() {
        let first_clean = first
            .trim_start_matches(['-', '*', '•', ' '])
            .trim()
            .to_string();
        let rest: Vec<&str> = lines.collect();
        if rest.is_empty() {
            // Single block: try "title — reason" or "title: reason"
            if let Some((t, d)) = first_clean.split_once("—") {
                return (t.trim().to_string(), d.trim().to_string());
            }
            if let Some((t, d)) = first_clean.split_once(" - ") {
                return (t.trim().to_string(), d.trim().to_string());
            }
            if first_clean.chars().count() <= 40 {
                return (first_clean, String::new());
            }
            return (default_title.into(), first_clean);
        }
        let title = if first_clean.chars().count() <= 48 {
            first_clean
        } else {
            default_title.into()
        };
        let description = if title == default_title {
            body.to_string()
        } else {
            rest.join("\n")
        };
        return (title, description);
    }
    (default_title.into(), body.to_string())
}

pub fn truncate_text(s: &str, max: usize) -> String {
    let count = s.chars().count();
    if count <= max {
        s.to_string()
    } else {
        let t: String = s.chars().take(max).collect();
        format!("{t}…")
    }
}

#[derive(Debug, Clone)]
pub struct Recommendation {
    pub rank: u32,
    pub title: String,
    pub description: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_three_sections() {
        let text = r#"
1. **推荐操作**
   先出愤怒再重击
   - 理由：先加力量再攻击

2. **备选方案**
   防御后结束回合
   - 理由：敌人伤害高

3. **不推荐**
   乱打牌
   - 理由：浪费能量
"#;
        let recs = parse_recommendations(text);
        assert!(recs.len() >= 2, "got {:?}", recs);
        assert_eq!(recs[0].rank, 1);
    }

    #[test]
    fn parse_unstructured_fallback() {
        let recs = parse_recommendations("直接打精英，血线还够。");
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].rank, 1);
        assert!(recs[0].description.contains("精英"));
    }

    #[test]
    fn parse_empty() {
        assert!(parse_recommendations("").is_empty());
        assert!(parse_recommendations("   \n  ").is_empty());
    }

    #[test]
    fn parse_chinese_number_format() {
        // "1、" and "2、" format (Chinese enumeration)
        let text = "1、出痛击然后防御\n2、先防御再出痛击\n3、乱打牌";
        let recs = parse_recommendations(text);
        assert_eq!(recs.len(), 3);
        assert_eq!(recs[0].rank, 1);
        assert!(recs[0].title.contains("痛击") || recs[0].description.contains("痛击"));
        assert_eq!(recs[1].rank, 2);
        assert_eq!(recs[2].rank, 3);
    }

    #[test]
    fn parse_single_line_fallback() {
        let recs = parse_recommendations("直接打精英，血线还够。");
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].rank, 1);
        assert!(recs[0].description.contains("精英"));
    }

    #[test]
    fn parse_title_with_em_dash() {
        let text = "1. **推荐操作**\n出痛击 — 伤害高且易伤\n2. **备选方案**\n防御 — 敌人下回合高伤";
        let recs = parse_recommendations(text);
        assert!(recs.len() >= 2);
        assert_eq!(recs[0].rank, 1);
    }
}
