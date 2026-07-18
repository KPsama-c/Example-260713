use super::heuristics;
use super::pipeline;
use crate::game::state::GameState;
use crate::llm::{prompts, LlmClient};

pub async fn analyze(state: &GameState, llm: &LlmClient) -> Vec<super::Recommendation> {
    let local = heuristics::analyze_map_local(state);
    pipeline::local_then_llm(
        state,
        llm,
        local,
        "本地选路（秒出）",
        prompts::map_prompt,
        true,
    )
    .await
}
