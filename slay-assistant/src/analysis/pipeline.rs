//! Shared local-first + optional LLM pipeline.

use super::{run_llm_analysis, Recommendation};
use crate::game::state::GameState;
use crate::llm::LlmClient;
use crate::ui;
use crate::ui::overlay::OverlayHandle;
use std::sync::atomic::{AtomicU64, Ordering};

pub fn renumber(recs: &mut [Recommendation], start: u32) {
    for (i, r) in recs.iter_mut().enumerate() {
        r.rank = start + i as u32;
    }
}

/// Print local results immediately, optionally call LLM, merge results.
/// Publishes local results to overlay instantly (before slow LLM), then
/// updates with merged results once LLM returns.
/// Checks `cancel_gen` before LLM — if a newer hotkey arrived, skips LLM.
pub async fn local_then_llm<F>(
    state: &GameState,
    llm: &LlmClient,
    overlay: &OverlayHandle,
    cancel_gen: &AtomicU64,
    my_gen: u64,
    mut local: Vec<Recommendation>,
    banner: &str,
    build_user: F,
    call_llm: bool,
) -> Vec<Recommendation>
where
    F: FnOnce(&str) -> String,
{
    renumber(&mut local, 1);

    // Push local results to overlay immediately — don't wait for LLM
    overlay.publish(state.summary(), local.clone());

    println!("\n—— {banner} ——");
    ui::overlay::show_recommendations(&local);

    if !call_llm || !llm.should_call_remote() {
        if call_llm && !llm.should_call_remote() {
            println!("（未调用 LLM：disabled 或无 key）\n");
        }
        return local;
    }

    // Check if a newer hotkey interrupted us — skip LLM if so
    if cancel_gen.load(Ordering::SeqCst) != my_gen {
        println!("（LLM 已取消 — 新热键打断）\n");
        return local;
    }

    println!("……LLM 补充中（失败不影响上方本地建议）……");
    let llm_recs = run_llm_analysis(state, llm, build_user).await;

    // Check again after LLM returns — if cancelled, keep local only
    if cancel_gen.load(Ordering::SeqCst) != my_gen {
        println!("（LLM 结果已过期 — 新热键打断）\n");
        return local;
    }

    let merged = merge_llm(local, llm_recs);

    // Update overlay with merged results
    overlay.publish(state.summary(), merged.clone());

    merged
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

#[cfg(test)]
mod tests {
    use super::super::Recommendation;
    use super::*;

    fn rec(rank: u32, title: &str, desc: &str) -> Recommendation {
        Recommendation {
            rank,
            title: title.into(),
            description: desc.into(),
        }
    }

    #[test]
    fn merge_llm_success_appends_with_prefix() {
        let local = vec![rec(1, "斩杀线", "可击杀"), rec(2, "格挡", "够")];
        let llm = vec![rec(1, "推荐操作", "出痛击"), rec(2, "备选方案", "防御")];
        let merged = merge_llm(local, llm);
        assert_eq!(merged.len(), 4);
        assert_eq!(merged[2].rank, 3);
        assert!(merged[2].title.starts_with("LLM · "));
        assert_eq!(merged[3].rank, 4);
    }

    #[test]
    fn merge_llm_failure_keeps_local() {
        let local = vec![rec(1, "斩杀线", "可击杀")];
        let llm = vec![rec(1, "LLM 调用失败", "timeout")]
        ;
        let merged = merge_llm(local, llm);
        assert_eq!(merged.len(), 2);
        assert!(merged[1].title.contains("跳过"));
    }

    #[test]
    fn merge_llm_empty_llm_returns_local() {
        let local = vec![rec(1, "斩杀线", "可击杀")];
        let llm: Vec<Recommendation> = vec![];
        let merged = merge_llm(local, llm);
        assert_eq!(merged.len(), 1);
    }

    #[test]
    fn renumber_updates_ranks() {
        let mut recs = vec![rec(5, "a", ""), rec(5, "b", "")];
        renumber(&mut recs, 1);
        assert_eq!(recs[0].rank, 1);
        assert_eq!(recs[1].rank, 2);
    }
}
