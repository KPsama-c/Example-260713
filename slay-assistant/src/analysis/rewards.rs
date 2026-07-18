use super::heuristics;
use super::pipeline;
use crate::game::state::GameState;
use crate::llm::{prompts, LlmClient};

pub async fn analyze(state: &GameState, llm: &LlmClient) -> Vec<super::Recommendation> {
    let local = heuristics::analyze_rewards_local(state);
    let has_cards = state
        .reward_state
        .as_ref()
        .map(|r| !r.cards.is_empty())
        .unwrap_or(false);
    // Only spend LLM tokens when card faces are visible
    pipeline::local_then_llm(
        state,
        llm,
        local,
        "本地选牌/奖励（秒出）",
        prompts::reward_prompt,
        has_cards,
    )
    .await
}
