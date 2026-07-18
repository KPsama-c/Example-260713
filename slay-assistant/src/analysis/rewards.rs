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
    let mut local = heuristics::analyze_rewards_local(state);
    // Prepend archetype pick/keep tips
    let mut tips = crate::knowledge::tips_for_state(state);
    for (i, t) in tips.iter_mut().enumerate() {
        t.rank = (i as u32) + 1;
        t.title = format!("知识·{}", t.title);
    }
    // renumber reward-local after tips
    let base = tips.len() as u32;
    for (i, r) in local.iter_mut().enumerate() {
        r.rank = base + i as u32 + 1;
    }
    tips.append(&mut local);

    let has_cards = state
        .reward_state
        .as_ref()
        .map(|r| !r.cards.is_empty())
        .unwrap_or(false);
    pipeline::local_then_llm(
        state, llm, overlay, cancel_gen, my_gen,
        tips,
        "流派知识 + 本地选牌（秒出）",
        prompts::reward_prompt,
        has_cards,
    )
    .await
}
