use super::heuristics;
use super::pipeline;
use crate::game::state::GameState;
use crate::llm::{prompts, LlmClient};

pub async fn analyze(state: &GameState, llm: &LlmClient) -> Vec<super::Recommendation> {
    let local = heuristics::analyze_event_local(state);
    pipeline::local_then_llm(
        state,
        llm,
        local,
        "本地事件建议（秒出）",
        prompts::event_prompt,
        true,
    )
    .await
}
