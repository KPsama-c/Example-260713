//! Phase 4: local combat heuristics (no network, target ≤100ms).

use super::Recommendation;
use crate::game::state::{Card, CombatState, Enemy, GameState};

/// Full local combat package: kill line, block need, energy ranking, play order hint.
pub fn analyze_combat_local(state: &GameState) -> Vec<Recommendation> {
    let Some(combat) = state.combat_state.as_ref() else {
        return vec![Recommendation {
            rank: 1,
            title: "📋 本地分析".into(),
            description: "等待进入战斗…".into(),
        }];
    };

    let mut out = Vec::new();
    let mut rank = 1u32;

    if let Some(r) = kill_line_rec(combat) {
        out.push(with_rank(r, rank));
        rank += 1;
    }
    if let Some(r) = block_rec(combat, state) {
        out.push(with_rank(r, rank));
        rank += 1;
    }
    if let Some(r) = energy_rank_rec(combat) {
        out.push(with_rank(r, rank));
        rank += 1;
    }
    if let Some(r) = play_order_rec(combat) {
        out.push(with_rank(r, rank));
    }

    if out.is_empty() {
        out.push(Recommendation {
            rank: 1,
            title: "📋 战况速览".into(),
            description: snapshot_line(combat),
        });
    }
    out
}

fn with_rank(mut r: Recommendation, rank: u32) -> Recommendation {
    r.rank = rank;
    r
}

/// Incoming attack damage this turn (sum over enemies).
pub fn incoming_damage(combat: &CombatState) -> i32 {
    combat
        .enemies
        .iter()
        .filter_map(|e| {
            let intent = e.intent.as_ref()?;
            let t = intent.intent_type.as_deref().unwrap_or("").to_lowercase();
            // Treat as attack if type looks like attack or damage is present.
            let is_attack = t.contains("attack")
                || t.contains("攻击")
                || t.contains("strike")
                || intent.damage.is_some_and(|d| d > 0);
            if !is_attack {
                return None;
            }
            let dmg = intent.damage.unwrap_or(0);
            let hits = intent.hits.unwrap_or(1).max(1) as i32;
            Some(dmg * hits)
        })
        .sum()
}

/// Estimated single-target damage from a card (uses damage field if present).
fn card_damage(card: &Card) -> i32 {
    card.damage.unwrap_or(0).max(0)
}

fn card_block(card: &Card) -> i32 {
    card.block.unwrap_or(0).max(0)
}

fn is_playable(card: &Card, energy: i32) -> bool {
    // cost < 0 often means X-cost; treat as playable if energy > 0
    if card.cost < 0 {
        return energy > 0;
    }
    card.cost <= energy
}

/// Whether a card's damage hits all enemies (AoE).
fn is_aoe(card: &Card) -> bool {
    let desc = card.description.as_deref().unwrap_or("").to_ascii_lowercase();
    let name = card.name.to_ascii_lowercase();
    desc.contains("all enemy")
        || desc.contains("全部敌人")
        || desc.contains("所有敌人")
        || desc.contains("全体")
        || name.contains("cleave")
        || name.contains("whirlwind")
        || name.contains("旋风")
        || name.contains("横扫")
        || name.contains("顺劈")
}

/// Total single-target attack damage in hand (AoE cards count once per enemy).
fn total_hand_damage(combat: &CombatState) -> i32 {
    let enemy_count = living_enemies(combat).len().max(1) as i32;
    combat
        .hand
        .iter()
        .filter(|c| is_playable(c, combat.energy))
        .map(|c| {
            let dmg = card_damage(c);
            if is_aoe(c) && enemy_count > 1 {
                dmg * enemy_count
            } else {
                dmg
            }
        })
        .sum()
}

/// Best single playable attack card damage.
fn best_attack_damage(combat: &CombatState) -> Option<(&Card, i32)> {
    combat
        .hand
        .iter()
        .filter(|c| is_playable(c, combat.energy))
        .map(|c| (c, card_damage(c)))
        .filter(|(_, d)| *d > 0)
        .max_by_key(|(_, d)| *d)
}

fn living_enemies(combat: &CombatState) -> Vec<&Enemy> {
    combat
        .enemies
        .iter()
        .filter(|e| e.current_hp.unwrap_or(1) > 0)
        .collect()
}

fn kill_line_rec(combat: &CombatState) -> Option<Recommendation> {
    let enemies = living_enemies(combat);
    if enemies.is_empty() {
        return None;
    }

    // Effective HP ≈ current_hp + block
    let mut killable: Vec<String> = Vec::new();
    let hand_dmg = total_hand_damage(combat);
    let best = best_attack_damage(combat);

    for e in &enemies {
        let hp = e.current_hp.unwrap_or(0);
        let eff = hp + e.block.max(0);
        if hand_dmg >= eff && hand_dmg > 0 {
            killable.push(format!(
                "{}  HP{}+盾{}={} ← 手牌伤{} → 斩杀",
                e.name, hp, e.block.max(0), eff, hand_dmg
            ));
        } else if let Some((card, dmg)) = best {
            if dmg >= eff {
                killable.push(format!(
                    "{}  用「{}」斩杀（伤{} ≥ HP{}）",
                    e.name, card.name, dmg, eff
                ));
            }
        }
    }

    if !killable.is_empty() {
        return Some(Recommendation {
            rank: 1,
            title: "⚔️ 可斩杀".into(),
            description: killable.join("\n"),
        });
    }

    // Not lethal — report gap on lowest HP enemy
    let lowest = enemies.iter().min_by_key(|e| {
        e.current_hp.unwrap_or(i32::MAX) + e.block.max(0)
    })?;
    let hp = lowest.current_hp.unwrap_or(0);
    let blk = lowest.block.max(0);
    let eff = hp + blk;
    let gap = (eff - hand_dmg).max(0);
    Some(Recommendation {
        rank: 1,
        title: "⚔️ 斩杀线".into(),
        description: format!(
            "手牌伤 {} vs {}\nHP{} + 盾{} = {} → 还差 {}",
            hand_dmg, lowest.name, hp, blk, eff, gap
        ),
    })
}

fn block_rec(combat: &CombatState, state: &GameState) -> Option<Recommendation> {
    let incoming = incoming_damage(combat);
    if incoming <= 0 {
        return Some(Recommendation {
            rank: 1,
            title: "🛡️ 格挡评估".into(),
            description: "敌人本回合无攻击意图 → 放心输出".into(),
        });
    }

    let have = combat.block.max(0);
    let need = (incoming - have).max(0);
    let hand_block: i32 = combat
        .hand
        .iter()
        .filter(|c| is_playable(c, combat.energy))
        .map(card_block)
        .sum();

    let hp = state.current_hp.unwrap_or(0);
    let after = (hp - need.max(0)).max(0);
    let max_hp = state.max_hp.unwrap_or(hp.max(1)).max(1);
    let pct_after = after * 100 / max_hp;

    let status = if need == 0 {
        "✓ 已有格挡足够".to_string()
    } else if hand_block >= need {
        format!("✓ 手牌格挡≈{} ≥ 净伤{} → 能防住", hand_block, need)
    } else {
        format!("✗ 手牌格挡≈{} < 净伤{} → 差≈{}", hand_block, need, need - hand_block)
    };

    let danger = if need > 0 && pct_after < 30 {
        format!("\n⚠ 受击后 HP {}/{}（{}%）→ 危险！", after, max_hp, pct_after)
    } else if need > 0 {
        format!("\n受击后 HP ≈ {}/{}（{}%）", after, max_hp, pct_after)
    } else {
        String::new()
    };

    Some(Recommendation {
        rank: 1,
        title: "🛡️ 格挡评估".into(),
        description: format!(
            "承伤 {} → 格挡 {} → 净伤 {}\n{}{}",
            incoming, have, need, status, danger
        ),
    })
}

fn energy_rank_rec(combat: &CombatState) -> Option<Recommendation> {
    if combat.hand.is_empty() {
        return None;
    }

    let mut scored: Vec<(String, f32, i32, i32)> = combat
        .hand
        .iter()
        .filter(|c| is_playable(c, combat.energy) || c.cost <= combat.max_energy)
        .map(|c| {
            let cost = if c.cost <= 0 { 1 } else { c.cost };
            let value = card_damage(c) + card_block(c);
            let ratio = value as f32 / cost as f32;
            (c.name.clone(), ratio, value, c.cost)
        })
        .filter(|(_, _, v, _)| *v > 0)
        .collect();

    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    if scored.is_empty() {
        return Some(Recommendation {
            rank: 1,
            title: "⚡ 能量效率".into(),
            description: format!(
                "能量 {}/{}  |  手牌暂无数值（Mod 未提供 damage/block）",
                combat.energy, combat.max_energy
            ),
        });
    }

    let top: Vec<String> = scored
        .iter()
        .take(4)
        .map(|(name, _ratio, value, cost)| {
            format!("{}({}费·值{})", name, cost, value)
        })
        .collect();

    Some(Recommendation {
        rank: 1,
        title: "⚡ 能量效率".into(),
        description: format!(
            "能量 {}/{}  →  {}",
            combat.energy,
            combat.max_energy,
            top.join("  >  ")
        ),
    })
}

/// Greedy play order: powers/buffs first (block+damage both 0 but cost ok → name heuristic),
/// then high damage/energy, leave block if needed.
fn play_order_rec(combat: &CombatState) -> Option<Recommendation> {
    let incoming = incoming_damage(combat);
    let need_block = (incoming - combat.block.max(0)).max(0);

    let mut energy = combat.energy;
    let mut order: Vec<String> = Vec::new();
    let mut remaining: Vec<&Card> = combat.hand.iter().collect();

    // Pass 1: zero-cost playable first
    play_pass(&mut remaining, &mut energy, &mut order, |c, e| {
        c.cost == 0 && is_playable(c, e)
    });

    // Pass 2: if need block, play highest block cards first
    if need_block > 0 {
        play_pass_sorted(
            &mut remaining,
            &mut energy,
            &mut order,
            |c| card_block(c),
            need_block,
        );
    }

    // Pass 3: highest damage among affordable (0 = no early stop)
    play_pass_sorted(
        &mut remaining,
        &mut energy,
        &mut order,
        |c| card_damage(c),
        0,
    );

    // Pass 4: any remaining affordable skills
    play_pass(&mut remaining, &mut energy, &mut order, |c, e| {
        is_playable(c, e)
    });

    if order.is_empty() {
        return Some(Recommendation {
            rank: 1,
            title: "🎯 出牌建议".into(),
            description: format!(
                "能量 {} 打不出手牌 → 结束回合\n手牌：{}",
                combat.energy,
                combat
                    .hand
                    .iter()
        .map(|c| format!("{}({}费)", crate::knowledge::display_name(c), c.cost))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        });
    }

    Some(Recommendation {
        rank: 1,
        title: "🎯 出牌顺序".into(),
        description: format!(
            "{} → 结束（余 {} 能量）",
            order.join(" → "),
            energy
        ),
    })
}

fn play_pass(
    remaining: &mut Vec<&Card>,
    energy: &mut i32,
    order: &mut Vec<String>,
    pred: impl Fn(&Card, i32) -> bool,
) {
    let mut i = 0;
    while i < remaining.len() {
        let c = remaining[i];
        if pred(c, *energy) && is_playable(c, *energy) {
            let cost = c.cost.max(0);
            if cost <= *energy {
                *energy -= cost;
        order.push(crate::knowledge::display_name(c));
                remaining.remove(i);
                continue;
            }
        }
        i += 1;
    }
}

fn play_pass_sorted(
    remaining: &mut Vec<&Card>,
    energy: &mut i32,
    order: &mut Vec<String>,
    score: impl Fn(&Card) -> i32,
    cumulative_stop: i32,
) {
    let mut cumulative = 0i32;
    loop {
        let best_idx = remaining
            .iter()
            .enumerate()
            .filter(|(_, c)| is_playable(c, *energy) && score(c) > 0)
            .max_by_key(|(_, c)| score(c))
            .map(|(i, _)| i);

        let Some(idx) = best_idx else { break };
        let c = remaining[idx];
        let val = score(c);
        if val <= 0 {
            break;
        }
        let cost = c.cost.max(0);
        if cost > *energy {
            break;
        }
        *energy -= cost;
        order.push(c.name.clone());
        remaining.remove(idx);
        cumulative += val;

        // Stop early when cumulative output meets the target (e.g. enough block to cover incoming damage)
        if cumulative_stop > 0 && cumulative >= cumulative_stop {
            break;
        }
    }
}

fn snapshot_line(combat: &CombatState) -> String {
    let hand: Vec<String> = combat
        .hand
        .iter()
        .map(|c| format!("{}({}费)", c.name, c.cost))
        .collect();
    let enemies: Vec<String> = combat
        .enemies
        .iter()
        .map(|e| {
            let hp = e.current_hp.map(|h| h.to_string()).unwrap_or_else(|| "?".into());
            format!("{} HP{}", e.name, hp)
        })
        .collect();
    format!(
        "能量 {}/{} 格挡 {} | 手牌: {} | 敌人: {}",
        combat.energy,
        combat.max_energy,
        combat.block,
        hand.join(", "),
        enemies.join("; ")
    )
}

/// Map pathing: multi-hop BFS on full act graph (see `map_bfs`).
pub fn analyze_map_local(state: &GameState) -> Vec<Recommendation> {
    super::map_bfs::analyze_map_bfs(state)
}

/// Reward screen: gold/card flow + card pick ranking when cards present.
pub fn analyze_rewards_local(state: &GameState) -> Vec<Recommendation> {
    let Some(rew) = state.reward_state.as_ref() else {
        return vec![Recommendation {
            rank: 1,
            title: "奖励".into(),
            description: "无 reward_state".into(),
        }];
    };

    let mut out = Vec::new();
    let deck_n = state.deck.len();
    let act = state.act.unwrap_or(1);
    let relic_names: Vec<&str> = state.relics.iter().map(|r| r.name.as_str()).collect();
    let relic_ids: Vec<&str> = state.relics.iter().map(|r| r.id.as_str()).collect();

    // Step-by-step when on summary reward screen (items)
    if !rew.items.is_empty() && rew.cards.is_empty() {
        let mut steps = Vec::new();
        let mut ordered: Vec<_> = rew.items.iter().collect();
        ordered.sort_by_key(|i| {
            // gold first, then potion, relic, card last (open pick)
            match i.item_type.to_ascii_lowercase().as_str() {
                "gold" => 0,
                "potion" => 1,
                "relic" => 2,
                "card" => 3,
                _ => 4,
            }
        });
        for (step, i) in ordered.iter().enumerate() {
            let label = match i.item_type.to_ascii_lowercase().as_str() {
                "gold" => format!(
                    "领金币 +{}",
                    i.gold_amount.unwrap_or(rew.gold.unwrap_or(0))
                ),
                "card" => "点开【卡牌奖励】进入三选一".into(),
                "potion" => format!(
                    "药水 {}",
                    i.description.as_deref().unwrap_or("领取")
                ),
                "relic" => format!(
                    "遗物 {}",
                    i.description.as_deref().unwrap_or("领取")
                ),
                other => format!("{other} {}", i.description.as_deref().unwrap_or("")),
            };
            steps.push(format!("{}. [index={}] {}", step + 1, i.index, label));
        }
        out.push(Recommendation {
            rank: 1,
            title: "【奖励流程】按顺序点".into(),
            description: steps.join(" → "),
        });
        out.push(Recommendation {
            rank: 2,
            title: "选牌提醒".into(),
            description: "点开卡牌后再按热键，可对三选一逐张评分；现在 API 未展开卡面时只能提示流程。".into(),
        });
        if rew.can_skip {
            out.push(Recommendation {
                rank: 3,
                title: "结束".into(),
                description: "领完可 proceed；卡不好再 skip 跳过加牌。".into(),
            });
        }
        return out;
    }

    // Card pick UI: rank cards
    if !rew.cards.is_empty() {
        let mut ranked: Vec<(i32, usize, String, String)> = rew
            .cards
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let (score, reasons) = score_reward_card(c, state, &relic_ids, &relic_names, deck_n, act);
                let title = format!(
                    "{}{}",
                    crate::knowledge::display_name(c),
                    if c.upgraded { "+" } else { "" }
                );
                (score, i, title, reasons.join("；"))
            })
            .collect();
        ranked.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));

        out.push(Recommendation {
            rank: 1,
            title: "🃏 选牌原则".into(),
            description: format!(
                "牌组约 {deck_n} 张 | Act{act} | 遗物：{}。优先：稀有/强力输出或关键防御 > 一般攻击 > 劣质打击/防御；后期牌多更敢 skip。",
                if relic_names.is_empty() {
                    "无".into()
                } else {
                    relic_names.join("、")
                }
            ),
        });

        if let Some((score, idx, name, why)) = ranked.first() {
            out.push(Recommendation {
                rank: 2,
                title: format!("【最优】选第 {} 张：{name}", idx + 1),
                description: format!("{why}（分 {score}）"),
            });
        }
        if ranked.len() > 1 {
            let (score, idx, name, why) = &ranked[1];
            out.push(Recommendation {
                rank: 3,
                title: format!("【备选】第 {} 张：{name}", idx + 1),
                description: format!("{why}（分 {score}）"),
            });
        }
        if let Some((score, idx, name, why)) = ranked.iter().last() {
            if ranked.len() >= 2 && *score < ranked[0].0.saturating_sub(8) {
                out.push(Recommendation {
                    rank: 4,
                    title: format!("【最弱】第 {} 张：{name}", idx + 1),
                    description: format!("更建议不选这张：{why}（分 {score}）"),
                });
            }
        }

        // Skip advice
        let best = ranked.first().map(|r| r.0).unwrap_or(0);
        let skip = if best < 8 && deck_n >= 25 {
            "牌组已偏厚且候选偏弱 → 【建议 Skip】"
        } else if best < 5 {
            "候选整体一般 → 可考虑 Skip"
        } else if best >= 18 {
            "有明显强卡 → 不建议 Skip"
        } else {
            "可拿最优张；没有特别想要的再 Skip"
        };
        out.push(Recommendation {
            rank: 5,
            title: "Skip？".into(),
            description: skip.into(),
        });

        let all: Vec<String> = ranked
            .iter()
            .map(|(s, i, n, _)| format!("{}.{}(分{})", i + 1, n, s))
            .collect();
        out.push(Recommendation {
            rank: 6,
            title: "三选一排序".into(),
            description: all.join(" ｜ "),
        });
        return out;
    }

    out.push(Recommendation {
        rank: 1,
        title: "奖励".into(),
        description: "无奖励条目".into(),
    });
    out
}

/// Score a card reward candidate (higher = better take).
fn score_reward_card(
    c: &Card,
    state: &GameState,
    relic_ids: &[&str],
    _relic_names: &[&str],
    deck_n: usize,
    act: u8,
) -> (i32, Vec<String>) {
    let mut score = 10i32;
    let mut why = Vec::new();
    let name = c.name.to_ascii_lowercase();
    let id = c.id.to_ascii_lowercase();
    let typ = c.card_type.to_ascii_lowercase();
    let desc = c.description.as_deref().unwrap_or("").to_ascii_lowercase();
    let rarity = c.rarity.as_deref().unwrap_or("").to_ascii_lowercase();

    // Rarity
    match rarity.as_str() {
        "rare" | "稀有" => {
            score += 14;
            why.push("稀有卡优先考虑".into());
        }
        "uncommon" | "罕见" | "少见" => {
            score += 7;
            why.push("罕见品质".into());
        }
        "common" | "普通" => {
            score += 2;
        }
        "curse" | "诅咒" => {
            score -= 40;
            why.push("诅咒，通常别拿".into());
        }
        _ => {}
    }

    // Type baseline
    match typ.as_str() {
        "attack" | "攻击" => {
            score += 3;
            why.push("攻击牌：补输出".into());
        }
        "skill" | "技能" => {
            score += 2;
        }
        "power" | "能力" => {
            score += 6;
            why.push("能力牌：长期收益".into());
        }
        "curse" | "status" | "状态" => {
            score -= 25;
            why.push("状态/诅咒负面".into());
        }
        _ => {}
    }

    // Upgrade
    if c.upgraded {
        score += 4;
        why.push("已升级".into());
    }

    // Damage / block numbers if present
    if let Some(d) = c.damage {
        if d >= 12 {
            score += 5;
            why.push(format!("伤害高({d})"));
        } else if d >= 8 {
            score += 2;
        }
    }
    if let Some(b) = c.block {
        if b >= 10 {
            score += 4;
            why.push(format!("格挡厚({b})"));
        }
    }

    // Cost efficiency
    if c.cost == 0 {
        score += 5;
        why.push("0 费灵活".into());
    } else if c.cost == 1 {
        score += 2;
    } else if c.cost >= 3 {
        score -= 1;
        why.push("费用偏高，看强度".into());
    }

    // Strike / Defend dilution (basic)
    let is_basic = name.contains("strike")
        || name.contains("defend")
        || name.contains("打击")
        || name.contains("防御")
        || id.contains("strike")
        || id.contains("defend");
    if is_basic {
        score -= 12;
        why.push("基础打击/防御，膨胀牌组，低优先".into());
    }

    // Keyword / text signals
    let keywords = [
        ("力量", 6, "加力量，铁甲很吃"),
        ("strength", 6, "力量向"),
        ("格挡", 2, "防御向"),
        ("block", 2, "防御向"),
        ("抽", 4, "带抽牌，提节奏"),
        ("draw", 4, "抽牌"),
        ("消耗", 3, "消耗：薄牌/爆发"),
        ("exhaust", 3, "消耗"),
        ("全部敌人", 5, "AOE 清杂"),
        ("all enemy", 5, "AOE"),
        ("所有敌人", 5, "AOE"),
        ("虚弱", 3, "施加虚弱"),
        ("易伤", 4, "施加易伤"),
        ("vulnerable", 4, "易伤"),
        ("weak", 3, "虚弱"),
        ("无实体", 5, "无实体输出/防"),
        ("intangible", 5, "无实体"),
    ];
    for (k, pts, note) in keywords {
        if desc.contains(k) || name.contains(k) {
            score += pts;
            why.push(note.into());
        }
    }

    // Ironclad / burning blood synergy
    let has_burning = relic_ids.iter().any(|r| r.to_ascii_uppercase().contains("BURNING_BLOOD"));
    if has_burning {
        // Prefer ending fights faster / solid attacks
        if typ.contains("attack") || typ.contains("攻击") {
            score += 2;
            why.push("有燃烧之血：利落结束战斗更赚回血".into());
        }
    }

    // Deck size: early take more cards, late be picky
    if deck_n >= 28 {
        score -= 3;
        why.push("牌组已厚，加牌更挑剔".into());
    } else if deck_n <= 15 && act == 1 {
        score += 2;
        why.push("前期牌少，可更积极拿牌".into());
    }

    // Character name hints
    if let Some(ch) = state.character.as_deref() {
        if ch.contains("铁甲") || ch.to_ascii_lowercase().contains("iron") {
            if desc.contains("力量") || desc.contains("strength") || desc.contains("消耗") {
                score += 2;
            }
        }
    }

    // Archetype / meta knowledge (core cards, tags)
    let (arch_s, arch_why) = crate::knowledge::score_card_for_archetypes(c, state);
    score += arch_s;
    why.extend(arch_why);

    // Cap why lines
    why.truncate(5);
    if why.is_empty() {
        why.push("综合中庸".into());
    }
    (score, why)
}

/// When MCP cannot expose the shelf (MissingMethod / degraded), give pre-shop strategy.
fn shop_degraded_local(state: &GameState, gold: i32, hp_pct: i32) -> Vec<Recommendation> {
    let deck_n = state.deck.len();
    let act = state.act.unwrap_or(1);
    let keep = if act <= 1 { 50 } else { 80 };

    let mut out = vec![
        Recommendation {
            rank: 1,
            title: "⚠ 商店货架不可读".into(),
            description: "STS2_MCP 商店 Inventory API 与当前游戏版本不匹配（MissingMethod / get_Inventory）。无法列出在售卡。处理：离开商店回地图后再按热键；根治需更新 Mod（GitHub PR #117 / Issue #114）。".into(),
        },
        Recommendation {
            rank: 2,
            title: "进店前策略（基于缓存牌组）".into(),
            description: format!(
                "持金 {gold} | HP {hp_pct}% | 牌组约 {deck_n} 张 | Act{act}。优先：删基础打击/防御 → 关键遗物 → 补流派核心；血线低优先药水/别硬买高费。"
            ),
        },
        Recommendation {
            rank: 3,
            title: "删牌优先".into(),
            description: if deck_n >= 18 {
                format!("牌组偏厚（{deck_n}）— 有删牌服务时优先删打击/防御；留约 {keep} 金应急。")
            } else if deck_n >= 12 {
                format!("牌组中等（{deck_n}）— 有明显废牌再删；预算离店约 {keep} 金。")
            } else {
                format!("牌组仍薄（{deck_n}）— 可先买核心牌，删牌非必须；留约 {keep} 金。")
            },
        },
        Recommendation {
            rank: 4,
            title: "预算".into(),
            description: format!(
                "建议离店至少留约 {keep} 金；HP {hp_pct}% 偏低时优先自保而非豪购。"
            ),
        },
    ];

    // Top remove candidates from owned deck names
    let mut basics: Vec<String> = state
        .deck
        .iter()
        .filter(|c| {
            let n = c.name.to_ascii_lowercase();
            let id = c.id.to_ascii_lowercase();
            n.contains("strike")
                || n.contains("defend")
                || n.contains("打击")
                || n.contains("防御")
                || id.contains("strike")
                || id.contains("defend")
        })
        .map(|c| crate::knowledge::display_name(c))
        .collect();
    basics.sort();
    basics.dedup();
    if !basics.is_empty() {
        out.push(Recommendation {
            rank: 5,
            title: "可考虑删除".into(),
            description: format!(
                "{}（共 {} 种基础牌痕迹）",
                basics.into_iter().take(6).collect::<Vec<_>>().join("、"),
                state
                    .deck
                    .iter()
                    .filter(|c| {
                        let n = c.name.to_ascii_lowercase();
                        n.contains("strike")
                            || n.contains("defend")
                            || n.contains("打击")
                            || n.contains("防御")
                    })
                    .count()
            ),
        });
    }

    if let Some(kit) = crate::knowledge::detect_archetypes(state) {
        out.push(Recommendation {
            rank: 6,
            title: format!("流派：{}", kit.primary.name),
            description: format!(
                "{} — 商店优先找：{}",
                kit.primary.pick_priority.chars().take(80).collect::<String>(),
                kit.primary
                    .core_cards
                    .iter()
                    .take(4)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(" / ")
            ),
        });
    }

    out
}

/// Shop: buy / remove / skip with gold budget.
pub fn analyze_shop_local(state: &GameState) -> Vec<Recommendation> {
    let gold = state.gold.unwrap_or(0);
    let hp_pct = {
        let hp = state.current_hp.unwrap_or(0);
        let max = state.max_hp.unwrap_or(hp.max(1)).max(1);
        hp * 100 / max
    };
    let Some(shop) = state.shop_state.as_ref() else {
        return shop_degraded_local(state, gold, hp_pct);
    };

    let mut out = vec![Recommendation {
        rank: 1,
        title: "🛒 商店总览".into(),
        description: format!(
            "持金 {gold} | HP {}% | 卡 {} 件 / 遗物 {} / 药水 {} | 删牌费 {:?}",
            hp_pct,
            shop.cards.len(),
            shop.relics.len(),
            shop.potions.len(),
            shop.removal_cost
        ),
    }];

    // Rank cards by price efficiency + reward card scorer reuse
    let mut card_opts: Vec<(i32, String)> = shop
        .cards
        .iter()
        .map(|si| {
            let relic_ids: Vec<&str> = state.relics.iter().map(|r| r.id.as_str()).collect();
            let relic_names: Vec<&str> = state.relics.iter().map(|r| r.name.as_str()).collect();
            let (base, why) = score_reward_card(
                &si.item,
                state,
                &relic_ids,
                &relic_names,
                state.deck.len(),
                state.act.unwrap_or(1),
            );
            let afford = si.price <= gold;
            let mut s = base - (si.price / 15);
            if !afford {
                s -= 20;
            }
            let name = crate::knowledge::display_name(&si.item);
            let title = format!(
                "{}{} ({}金{})",
                name,
                if si.item.upgraded { "+" } else { "" },
                si.price,
                if afford { "" } else { "·买不起" }
            );
            (s, format!("{title} — {}", why.join("；")))
        })
        .collect();
    card_opts.sort_by(|a, b| b.0.cmp(&a.0));

    if let Some((s, line)) = card_opts.first() {
        out.push(Recommendation {
            rank: 2,
            title: "【卡】优先考虑".into(),
            description: format!("{line}（分 {s}）"),
        });
    }

    // Relics: cheaper first if affordable
    let mut relics: Vec<_> = shop.relics.iter().collect();
    relics.sort_by_key(|r| r.price);
    if let Some(r) = relics.iter().find(|r| r.price <= gold) {
        out.push(Recommendation {
            rank: 3,
            title: "【遗物】可买".into(),
            description: format!(
                "{} — {}金 — {}",
                crate::knowledge::translate_card_name(&r.item.name),
                r.price,
                r.item.description.as_deref().unwrap_or("看描述是否核心")
            ),
        });
    } else if let Some(r) = relics.first() {
        out.push(Recommendation {
            rank: 3,
            title: "【遗物】暂缓".into(),
            description: format!(
                "最便宜 {} 需 {} 金，当前 {} 金不够或性价比一般",
                crate::knowledge::translate_card_name(&r.item.name),
                r.price,
                gold
            ),
        });
    }

    // Removal
    if let Some(cost) = shop.removal_cost {
        let deck = state.deck.len();
        let remove = if cost <= gold && deck >= 18 {
            format!("建议删牌（费{cost}，牌组{deck}张偏厚）— 优先删打击/防御")
        } else if cost <= gold && deck >= 12 {
            format!("可考虑删牌（费{cost}）若有明显废牌")
        } else {
            format!("删牌费{cost}，当前金{gold}/牌{deck} — 暂可不删")
        };
        out.push(Recommendation {
            rank: 4,
            title: "删牌".into(),
            description: remove,
        });
    }

    // Budget leave
    let keep = if state.act.unwrap_or(1) <= 1 { 50 } else { 80 };
    out.push(Recommendation {
        rank: 5,
        title: "预算".into(),
        description: format!(
            "建议离店至少留约 {keep} 金应急；血线低({}%)优先买锅/别硬冲精英。",
            hp_pct
        ),
    });

    out
}

/// Event choices: prefer free/heal, avoid low-hp gambles.
pub fn analyze_event_local(state: &GameState) -> Vec<Recommendation> {
    let hp = state.current_hp.unwrap_or(0);
    let max_hp = state.max_hp.unwrap_or(hp.max(1)).max(1);
    let hp_pct = hp * 100 / max_hp;
    let gold = state.gold.unwrap_or(0);

    let Some(ev) = state.event_state.as_ref() else {
        return vec![Recommendation {
            rank: 1,
            title: "事件".into(),
            description: "无 event_state".into(),
        }];
    };

    let mut out = vec![Recommendation {
        rank: 1,
        title: "📜 事件".into(),
        description: format!(
            "{} | HP {}/{}（{}%）| 金 {} | 选项 {} 个",
            ev.event_name.as_deref().unwrap_or("未知事件"),
            hp,
            max_hp,
            hp_pct,
            gold,
            ev.choices.len()
        ),
    }];
    if let Some(t) = &ev.text {
        out.push(Recommendation {
            rank: 2,
            title: "文案摘要".into(),
            description: truncate_local(t, 160),
        });
    }

    let mut scored: Vec<(i32, &crate::game::state::EventChoice, String)> = ev
        .choices
        .iter()
        .filter(|c| c.available)
        .map(|c| {
            let text = c.text.to_ascii_lowercase();
            let mut s = 10i32;
            let why: String = if text.contains("heal")
                || text.contains("回血")
                || text.contains("回复")
                || text.contains("生命")
            {
                s += if hp_pct < 60 { 15 } else { 5 };
                "含回血倾向".into()
            } else if text.contains("gold") || text.contains("金币") || text.contains("钱") {
                s += 6;
                "金币向".into()
            } else if text.contains("relic") || text.contains("遗物") || text.contains("圣物") {
                s += 12;
                "遗物向，通常高价值".into()
            } else if text.contains("curse") || text.contains("诅咒") {
                s -= 8;
                "可能诅咒".into()
            } else if text.contains("hp")
                || text.contains("伤害")
                || text.contains("失去")
                || text.contains("掉血")
            {
                s += if hp_pct < 40 { -12 } else { 2 };
                if hp_pct < 40 {
                    "掉血风险，血线危险时回避".into()
                } else {
                    "可能掉血换收益".into()
                }
            } else if text.contains("leave") || text.contains("离开") || text.contains("忽略") {
                s += if hp_pct < 40 { 8 } else { 0 };
                "离开/无视".into()
            } else {
                "中性，看具体描述".into()
            };
            if let Some(cost) = &c.cost {
                if cost.contains("金") || cost.to_ascii_lowercase().contains("gold") {
                    s -= 2;
                }
            }
            (s, c, why)
        })
        .collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0));

    if let Some((s, c, why)) = scored.first() {
        out.push(Recommendation {
            rank: 3,
            title: format!("【倾向】{}", truncate_local(&c.text, 40)),
            description: format!("id={} | {why}（分 {s}）| cost={:?}", c.id, c.cost),
        });
    }
    if scored.len() > 1 {
        let (s, c, why) = &scored[1];
        out.push(Recommendation {
            rank: 4,
            title: format!("【备选】{}", truncate_local(&c.text, 40)),
            description: format!("id={} | {why}（分 {s}）", c.id),
        });
    }
    out.push(Recommendation {
        rank: 5,
        title: "原则".into(),
        description: "血线低勿赌大掉血；遗物/回血优先；看不清收益时选离开或稳妥项。".into(),
    });
    out
}

/// Rest site: heal vs smith.
pub fn analyze_rest_local(state: &GameState) -> Vec<Recommendation> {
    let hp = state.current_hp.unwrap_or(0);
    let max_hp = state.max_hp.unwrap_or(hp.max(1)).max(1);
    let missing = (max_hp - hp).max(0);
    let hp_pct = hp * 100 / max_hp.max(1);
    // STS rest often heals ~30% max hp
    let heal_est = (max_hp * 30 / 100).max(1);

    let mut out = vec![Recommendation {
        rank: 1,
        title: "🔥 篝火".into(),
        description: format!(
            "HP {hp}/{max_hp}（{hp_pct}%），缺 {missing} 血；休息大约回 ~{heal_est}（以游戏为准）"
        ),
    }];

    let prefer_rest = hp_pct < 60 || missing >= heal_est;
    if prefer_rest {
        out.push(Recommendation {
            rank: 2,
            title: "【推荐】休息回血".into(),
            description: "血线未满且缺口明显 — 优先 rest，别为了升级硬扛后面精英/Boss".into(),
        });
        out.push(Recommendation {
            rank: 3,
            title: "【备选】升级".into(),
            description: "仅当血量仍健康或下一层必安全时再 smith；优先升级核心输出/关键防御".into(),
        });
    } else {
        out.push(Recommendation {
            rank: 2,
            title: "【推荐】升级卡牌".into(),
            description: "血线健康 — smith 提升长期强度；优先升级常用输出/能力/关键防御".into(),
        });
        out.push(Recommendation {
            rank: 3,
            title: "【备选】休息".into(),
            description: "若前方有精英/Boss 且你想留容错，仍可 rest".into(),
        });
    }

    if let Some(rest) = state.rest_state.as_ref() {
        if !rest.upgradeable_cards.is_empty() {
            let names: Vec<String> = rest
                .upgradeable_cards
                .iter()
                .take(6)
                .map(|c| c.name.clone())
                .collect();
            out.push(Recommendation {
                rank: 4,
                title: "可升级示例".into(),
                description: format!("候选：{}", names.join("、")),
            });
        }
        if !rest.options.is_empty() {
            let opts: Vec<String> = rest
                .options
                .iter()
                .map(|o| format!("{}({})", o.name, o.id))
                .collect();
            out.push(Recommendation {
                rank: 5,
                title: "选项".into(),
                description: opts.join(" ｜ "),
            });
        }
    }
    out
}

fn truncate_local(s: &str, max: usize) -> String {
    let n = s.chars().count();
    if n <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game::state::{EnemyIntent, ScreenType};

    fn sample_combat() -> GameState {
        GameState {
            screen_type: ScreenType::Combat,
            seed: None,
            character: Some("Ironclad".into()),
            act: Some(1),
            floor: Some(3),
            ascension_level: None,
            current_hp: Some(40),
            max_hp: Some(80),
            gold: Some(50),
            deck: vec![],
            relics: vec![],
            potions: vec![],
            combat_state: Some(CombatState {
                turn: 1,
                hand: vec![
                    Card {
                        id: "strike".into(),
                        name: "打击".into(),
                        card_type: "Attack".into(),
                        cost: 1,
                        upgraded: false,
                        damage: Some(6),
                        block: None,
                        magic_number: None,
                        description: None,
                        rarity: None,
                    },
                    Card {
                        id: "defend".into(),
                        name: "防御".into(),
                        card_type: "Skill".into(),
                        cost: 1,
                        upgraded: false,
                        damage: None,
                        block: Some(5),
                        magic_number: None,
                        description: None,
                        rarity: None,
                    },
                    Card {
                        id: "bash".into(),
                        name: "痛击".into(),
                        card_type: "Attack".into(),
                        cost: 2,
                        upgraded: false,
                        damage: Some(8),
                        block: None,
                        magic_number: None,
                        description: None,
                        rarity: None,
                    },
                ],
                draw_pile: vec![],
                discard_pile: vec![],
                exhaust_pile: vec![],
                energy: 3,
                max_energy: 3,
                block: 0,
                powers: vec![],
                enemies: vec![Enemy {
                    id: "e1".into(),
                    name: "邪教徒".into(),
                    current_hp: Some(10),
                    max_hp: Some(40),
                    block: 0,
                    intent: Some(EnemyIntent {
                        intent_type: Some("Attack".into()),
                        damage: Some(6),
                        hits: Some(1),
                        block: None,
                    }),
                    powers: vec![],
                }],
            }),
            map_state: None,
            shop_state: None,
            event_state: None,
            reward_state: None,
            rest_state: None,
        }
    }

    #[test]
    fn kill_and_block_and_order() {
        let state = sample_combat();
        let recs = analyze_combat_local(&state);
        assert!(!recs.is_empty());
        let text = recs
            .iter()
            .map(|r| format!("{} {}", r.title, r.description))
            .collect::<Vec<_>>()
            .join(" | ");
        assert!(text.contains("斩杀") || text.contains("格挡") || text.contains("出牌"), "{text}");
    }

    #[test]
    fn incoming_damage_multi_hit() {
        let mut state = sample_combat();
        if let Some(c) = state.combat_state.as_mut() {
            c.enemies[0].intent = Some(EnemyIntent {
                intent_type: Some("Attack".into()),
                damage: Some(3),
                hits: Some(3),
                block: None,
            });
            assert_eq!(incoming_damage(c), 9);
        }
    }
}
