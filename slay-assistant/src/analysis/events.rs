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
    let local = heuristics::analyze_event_local(state);
    pipeline::local_then_llm(
        state, llm, overlay, cancel_gen, my_gen,
        local,
        "本地事件建议（秒出）",
        prompts::event_prompt,
        true,
    )
    .await
}
