//! Shared local-first + optional LLM pipeline.

use super::{run_llm_analysis, Recommendation};
use crate::game::state::GameState;
use crate::llm::LlmClient;
use crate::ui;

pub fn renumber(recs: &mut [Recommendation], start: u32) {
    for (i, r) in recs.iter_mut().enumerate() {
        r.rank = start + i as u32;
    }
}

/// Print local results immediately, optionally call LLM, merge results.
pub async fn local_then_llm<F>(
    state: &GameState,
    llm: &LlmClient,
    mut local: Vec<Recommendation>,
    banner: &str,
    build_user: F,
    call_llm: bool,
) -> Vec<Recommendation>
where
    F: FnOnce(&str) -> String,
{
    renumber(&mut local, 1);
    println!("\n—— {banner} ——");
    ui::overlay::show_recommendations(&local);

    if !call_llm || !llm.should_call_remote() {
        if call_llm && !llm.should_call_remote() {
            println!("（未调用 LLM：disabled 或无 key）\n");
        }
        return local;
    }

    println!("……LLM 补充中（失败不影响上方本地建议）……");
    let llm_recs = run_llm_analysis(state, llm, build_user).await;
    merge_llm(local, llm_recs)
}

pub fn merge_llm(mut local: Vec<Recommendation>, llm_recs: Vec<Recommendation>) -> Vec<Recommendation> {
    let failed = llm_recs.first().map(is_llm_failure).unwrap_or(false);
    let base = local.len() as u32;
    if failed {
        if let Some(err) = llm_recs.first() {
            local.push(Recommendation {
                rank: base + 1,
                title: "LLM 跳过".into(),
                description: super::truncate_text(&err.description, 220),
            });
        }
        return local;
    }
    for (i, mut r) in llm_recs.into_iter().enumerate() {
        r.rank = base + i as u32 + 1;
        if !r.title.starts_with("LLM") {
            r.title = format!("LLM · {}", r.title);
        }
        local.push(r);
    }
    local
}

fn is_llm_failure(r: &Recommendation) -> bool {
    r.title.contains("失败")
        || r.title.contains("配置")
        || r.title.contains("序列化")
        || r.description.contains("error sending")
        || r.description.contains("无法连接")
        || r.description.contains("已禁用")
}
