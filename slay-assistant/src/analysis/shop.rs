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
    let local = heuristics::analyze_shop_local(state);
    // No shelf data (MCP merchant bug / degraded) → local strategy only, skip LLM hang.
    let call_llm = state.shop_state.is_some();
    pipeline::local_then_llm(
        state,
        llm,
        overlay,
        cancel_gen,
        my_gen,
        local,
        if call_llm {
            "本地商店建议（秒出）"
        } else {
            "商店降级策略（无货架 / 不调 LLM）"
        },
        prompts::shop_prompt,
        call_llm,
    )
    .await
}
