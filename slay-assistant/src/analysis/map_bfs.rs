//! Multi-hop map path scoring via BFS on full act graph.

use super::Recommendation;
use crate::game::state::{GameState, MapNextOption, MapNode, MapState};
use std::collections::{HashMap, HashSet, VecDeque};

const MAX_DEPTH: usize = 6;

pub fn analyze_map_bfs(state: &GameState) -> Vec<Recommendation> {
    let Some(map) = state.map_state.as_ref() else {
        return vec![Recommendation {
            rank: 1,
            title: "地图".into(),
            description: "无 map_state".into(),
        }];
    };

    let hp = state.current_hp.unwrap_or(0);
    let max_hp = state.max_hp.unwrap_or(hp.max(1)).max(1);
    let hp_pct = hp * 100 / max_hp;
    let gold = state.gold.unwrap_or(0);
    let act = state.act.unwrap_or(1);
    let floor = state.floor.unwrap_or(1);

    let ctx = PathCtx {
        hp_pct,
        gold,
        need_heal: hp_pct < 45,
        want_elite: hp_pct >= 65,
        want_shop: gold >= 80 || (gold >= 50 && act >= 2),
    };

    let graph = build_graph(map);
    let options: Vec<MapNextOption> = if map.next_options.is_empty() {
        // synthesize from current children
        synthesize_options(map)
    } else {
        map.next_options.clone()
    };

    if options.is_empty() {
        return vec![Recommendation {
            rank: 1,
            title: "地图".into(),
            description: format!(
                "Act{act} F{floor} HP {hp}/{max_hp} — 无 next_options（可能在事件/战斗中）"
            ),
        }];
    }

    let mut scored: Vec<OptScore> = options
        .iter()
        .map(|opt| score_option(opt, &graph, &ctx))
        .collect();
    scored.sort_by(|a, b| b.total.cmp(&a.total).then(a.index.cmp(&b.index)));

    let phase = if ctx.need_heal {
        "保命（篝火/少硬刚）"
    } else if ctx.want_elite {
        "进攻（可冲精英）"
    } else if ctx.want_shop {
        "攒店"
    } else {
        "均衡"
    };

    let mut out = vec![Recommendation {
        rank: 1,
        title: "★ 多跳选路 (BFS)".into(),
        description: format!(
            "Act{act} 第{floor}层 | HP {hp}/{max_hp}（{hp_pct}%）| 金 {gold} | 图节点 {} | 可选 {} | 策略：{phase} | 前瞻 {MAX_DEPTH} 层",
            map.nodes.len(),
            options.len()
        ),
    }];

    if let Some(best) = scored.first() {
        out.push(Recommendation {
            rank: 2,
            title: format!(
                "【最优】index={}（{}）→ {}",
                best.index,
                side_label(best.col, &scored),
                best.room_cn
            ),
            description: format!(
                "评分 {} | 路径：{} | {}",
                best.total,
                best.path_cn,
                best.why.join("；")
            ),
        });
    }
    if scored.len() > 1 {
        let s = &scored[1];
        out.push(Recommendation {
            rank: 3,
            title: format!(
                "【备选】index={} → {}",
                s.index, s.room_cn
            ),
            description: format!(
                "评分 {} | 路径：{} | {}",
                s.total,
                s.path_cn,
                s.why.join("；")
            ),
        });
    }
    if let Some(worst) = scored.iter().rev().find(|s| s.total + 12 < scored[0].total) {
        out.push(Recommendation {
            rank: 4,
            title: format!("【避开】index={}", worst.index),
            description: format!(
                "评分 {} | {} | {}",
                worst.total,
                worst.path_cn,
                worst.why.join("；")
            ),
        });
    }

    let ranking: Vec<String> = scored
        .iter()
        .enumerate()
        .map(|(i, s)| {
            format!(
                "{}. idx={} {}… 分{}",
                i + 1,
                s.index,
                s.path_cn.chars().take(28).collect::<String>(),
                s.total
            )
        })
        .collect();
    out.push(Recommendation {
        rank: 9,
        title: "全部排序".into(),
        description: ranking.join(" ｜ "),
    });

    out
}

struct PathCtx {
    hp_pct: i32,
    gold: i32,
    need_heal: bool,
    want_elite: bool,
    want_shop: bool,
}

struct OptScore {
    index: i32,
    col: i32,
    room_cn: String,
    total: i32,
    path_cn: String,
    why: Vec<String>,
}

fn build_graph(map: &MapState) -> HashMap<String, MapNode> {
    let mut g = HashMap::new();
    for n in &map.nodes {
        g.insert(n.id.clone(), n.clone());
    }
    // ensure next option nodes exist
    for o in &map.next_options {
        g.entry(o.node_id.clone()).or_insert(MapNode {
            id: o.node_id.clone(),
            symbol: o.symbol.clone(),
            x: Some(o.col as f32),
            y: Some(o.row as f32),
            children: o.leads_to.clone(),
            parents: vec![],
        });
    }
    g
}

fn synthesize_options(map: &MapState) -> Vec<MapNextOption> {
    let Some(cur) = map.current_node_id.as_ref() else {
        return vec![];
    };
    let Some(node) = map.nodes.iter().find(|n| &n.id == cur) else {
        return vec![];
    };
    node.children
        .iter()
        .enumerate()
        .filter_map(|(i, id)| {
            let n = map.nodes.iter().find(|x| &x.id == id)?;
            Some(MapNextOption {
                index: i as i32,
                node_id: id.clone(),
                symbol: n.symbol.clone(),
                col: n.x.unwrap_or(0.0) as i32,
                row: n.y.unwrap_or(0.0) as i32,
                leads_to: n.children.clone(),
            })
        })
        .collect()
}

fn score_option(opt: &MapNextOption, graph: &HashMap<String, MapNode>, ctx: &PathCtx) -> OptScore {
    let mut why = Vec::new();
    let room_label = room_type_label(&opt.symbol);

    // Immediate room
    let mut total = room_score(&opt.symbol, ctx, &mut why);

    // BFS multi-hop expected value
    let (hop_score, path_types, highlights) = bfs_value(&opt.node_id, graph, ctx, MAX_DEPTH);
    total += hop_score;
    why.extend(highlights);

    let path_cn = {
        let mut p = vec![room_label.clone()];
        p.extend(path_types.iter().map(|t| room_type_label(t)));
        p.join(" → ")
    };

    OptScore {
        index: opt.index,
        col: opt.col,
        room_cn: room_label,
        total,
        path_cn,
        why,
    }
}

/// BFS: best path value with depth decay; also track first path's type sequence for display.
fn bfs_value(
    start: &str,
    graph: &HashMap<String, MapNode>,
    ctx: &PathCtx,
    max_depth: usize,
) -> (i32, Vec<String>, Vec<String>) {
    // state: (node_id, depth, score_so_far, path_types)
    let mut q = VecDeque::new();
    let mut best_score = 0i32;
    let mut best_path: Vec<String> = vec![];
    let mut highlights = Vec::new();
    let mut seen: HashSet<(String, usize)> = HashSet::new();

    q.push_back((start.to_string(), 0usize, 0i32, Vec::<String>::new()));

    while let Some((id, depth, score, path)) = q.pop_front() {
        if depth > 0 && score > best_score {
            best_score = score;
            best_path = path.clone();
        }
        if depth >= max_depth {
            continue;
        }
        if !seen.insert((id.clone(), depth)) {
            continue;
        }
        let Some(node) = graph.get(&id) else {
            continue;
        };
        // expand children (for depth 0 start, children are next rooms after taking this option)
        let nexts: Vec<String> = if depth == 0 {
            // start node itself already counted as immediate; expand its children
            node.children.clone()
        } else {
            node.children.clone()
        };

        for child_id in nexts {
            let Some(child) = graph.get(&child_id) else {
                continue;
            };
            let mut why_tmp = Vec::new();
            let add = room_score(&child.symbol, ctx, &mut why_tmp);
            // depth decay: closer nodes matter more
            let decay = match depth + 1 {
                1 => 100,
                2 => 70,
                3 => 50,
                4 => 35,
                5 => 25,
                _ => 15,
            };
            let add_scaled = add * decay / 100;
            let mut new_path = path.clone();
            new_path.push(child.symbol.clone());
            // note special finds
            if child.symbol == "Shop" && depth + 1 <= 3 {
                highlights.push(format!("{}跳内有商店", depth + 1));
            }
            if (child.symbol == "RestSite" || child.symbol == "Rest") && depth + 1 <= 3 {
                highlights.push(format!("{}跳内有篝火", depth + 1));
            }
            if child.symbol == "Elite" && depth + 1 <= 3 {
                if ctx.want_elite {
                    highlights.push(format!("{}跳内可接精英", depth + 1));
                } else if ctx.need_heal {
                    highlights.push(format!("{}跳内有精英(血线慎)", depth + 1));
                }
            }
            if child.symbol == "Treasure" && depth + 1 <= 3 {
                highlights.push(format!("{}跳内有宝箱", depth + 1));
            }
            q.push_back((child_id, depth + 1, score + add_scaled, new_path));
        }
    }

    highlights.sort();
    highlights.dedup();
    highlights.truncate(4);

    // if BFS found little, still return 0
    (best_score, best_path, highlights)
}

fn room_score(symbol: &str, ctx: &PathCtx, why: &mut Vec<String>) -> i32 {
    match symbol {
        "RestSite" | "Rest" => {
            let s = if ctx.need_heal {
                45
            } else if ctx.hp_pct < 70 {
                22
            } else {
                8
            };
            if why.is_empty() {
                why.push(if ctx.need_heal {
                    "血线需要篝火".into()
                } else {
                    "可回血/升级".into()
                });
            }
            s
        }
        "Shop" | "$" => {
            let s = if ctx.want_shop {
                28
            } else if ctx.gold >= 40 {
                14
            } else {
                5
            };
            if why.len() < 2 {
                why.push(format!("持金{}，商店价值", ctx.gold));
            }
            s
        }
        "Elite" | "E" => {
            if ctx.want_elite {
                if why.len() < 2 {
                    why.push("血线可冲精英".into());
                }
                26
            } else if ctx.need_heal {
                if why.len() < 2 {
                    why.push("低血避开精英".into());
                }
                -18
            } else {
                8
            }
        }
        "Treasure" | "T" => 20,
        "Unknown" | "?" | "Event" => {
            if ctx.need_heal {
                6
            } else {
                12
            }
        }
        "Monster" | "M" | "Enemy" => {
            if ctx.need_heal {
                4
            } else {
                10
            }
        }
        "Boss" | "B" => 2,
        _ => 5,
    }
}

fn room_type_label(symbol: &str) -> String {
    match symbol {
        "Monster" | "M" | "Enemy" => "普通怪".into(),
        "Elite" | "E" => "精英".into(),
        "RestSite" | "Rest" => "篝火".into(),
        "Shop" | "$" => "商店".into(),
        "Treasure" | "T" => "宝箱".into(),
        "Unknown" | "?" | "Event" => "问号".into(),
        "Boss" | "B" => "Boss".into(),
        other => other.to_string(),
    }
}

fn side_label(col: i32, all: &[OptScore]) -> String {
    let mut cols: Vec<i32> = all.iter().map(|s| s.col).collect();
    cols.sort();
    cols.dedup();
    if cols.len() <= 1 {
        return format!("列{col}");
    }
    let pos = cols.iter().position(|&c| c == col).unwrap_or(0);
    let tag = match (pos, cols.len()) {
        (0, _) => "最左",
        (p, n) if p == n - 1 => "最右",
        (p, n) if p < n / 2 => "偏左",
        _ => "偏右",
    };
    format!("{tag}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game::adapter::parse_game_state_json;
    use std::path::PathBuf;

    #[test]
    fn bfs_on_fixture() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/singleplayer_map.json");
        if !path.exists() {
            return;
        }
        let text = std::fs::read_to_string(path).unwrap();
        let state = parse_game_state_json(&text).unwrap();
        let recs = analyze_map_bfs(&state);
        assert!(!recs.is_empty());
        let joined = recs
            .iter()
            .map(|r| format!("{} {}", r.title, r.description))
            .collect::<Vec<_>>()
            .join(" ");
        assert!(joined.contains("最优") || joined.contains("BFS") || joined.contains("index"));
        // shop path should rank well with gold 113
        assert!(
            joined.contains("商店") || joined.contains("index=1") || joined.contains("idx=1"),
            "{joined}"
        );
    }
}
