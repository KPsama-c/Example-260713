use super::heuristics;
use super::pipeline;
use crate::game::state::GameState;
use crate::llm::{prompts, LlmClient};
use crate::ui::overlay::OverlayHandle;
use std::sync::atomic::AtomicU64;

pub async fn analyze(
    state: &GameState,
    llm: &LlmClient,
    overlay: &OverlayHandle,
    cancel_gen: &AtomicU64,
    my_gen: u64,
) -> Vec<super::Recommendation> {
    let mut local = heuristics::analyze_map_local(state);
    if let Some(kit) = crate::knowledge::detect_archetypes(state) {
        local.insert(
            0,
            super::Recommendation {
                rank: 0,
                title: format!("知识·倾向 {}", kit.primary.name),
                description: format!(
                    "[{}] {} | 选牌优先：{}",
                    kit.character_key,
                    kit.character_summary,
                    crate::knowledge::translate_text(&kit.primary.pick_priority)
                ),
            },
        );
    }
    pipeline::local_then_llm(
        state, llm, overlay, cancel_gen, my_gen,
        local,
        "流派 + BFS 选路（秒出）",
        prompts::map_prompt,
        true,
    )
    .await
}
