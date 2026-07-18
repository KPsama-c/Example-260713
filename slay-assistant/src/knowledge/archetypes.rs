//! Load embedded archetype database and score cards / emit tips.

use crate::analysis::Recommendation;
use crate::game::state::{Card, GameState};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::OnceLock;

const RAW: &str = include_str!("../../data/archetypes.json");

#[derive(Debug, Clone, Deserialize)]
pub struct Db {
    pub principles: Principles,
    pub characters: HashMap<String, CharacterDb>,
    #[serde(default)]
    pub generic_card_bias: GenericBias,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Principles {
    pub pick: Vec<String>,
    pub mulligan_keep: Vec<String>,
    pub mulligan_dump: Vec<String>,
    pub play_order: Vec<String>,
}

// Archetype.id used in tests / future UI


#[derive(Debug, Clone, Deserialize, Default)]
pub struct GenericBias {
    #[serde(default)]
    pub always_good: Vec<String>,
    #[serde(default)]
    pub usually_skip_late: Vec<String>,
    #[serde(default)]
    pub energy: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CharacterDb {
    pub aliases: Vec<String>,
    pub summary: String,
    pub archetypes: Vec<Archetype>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Archetype {
    #[allow(dead_code)]
    pub id: String,
    pub name: String,
    pub tier: String,
    pub core_cards: Vec<String>,
    #[serde(default)]
    pub core_ids: Vec<String>,
    #[serde(default)]
    pub key_relics: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    pub pick_priority: String,
    pub keep: String,
    pub dump: String,
    pub play: String,
}

#[derive(Debug, Clone)]
pub struct ArchetypeKit {
    pub character_key: String,
    pub character_summary: String,
    pub primary: Archetype,
    pub secondary: Option<Archetype>,
    pub match_score: i32,
}

fn db() -> &'static Db {
    static DB: OnceLock<Db> = OnceLock::new();
    DB.get_or_init(|| {
        serde_json::from_str(RAW).unwrap_or_else(|e| {
            log::error!("Failed to parse archetypes.json: {e}");
            Db {
                principles: Principles {
                    pick: vec![],
                    mulligan_keep: vec![],
                    mulligan_dump: vec![],
                    play_order: vec![],
                },
                characters: HashMap::new(),
                generic_card_bias: GenericBias::default(),
            }
        })
    })
}

/// Map API character string → database key (ironclad/silent/…)
pub fn detect_character(name: Option<&str>) -> Option<&'static str> {
    let name = name?;
    let n = name.trim();
    if n.is_empty() {
        return None;
    }
    let db = db();
    for (key, ch) in &db.characters {
        for a in &ch.aliases {
            if a.eq_ignore_ascii_case(n) || n.contains(a.as_str()) || a.contains(n) {
                return intern_char_key(key);
            }
        }
        if n.to_ascii_uppercase().contains(&key.to_ascii_uppercase()) {
            return intern_char_key(key);
        }
    }
    let lower = n.to_ascii_lowercase();
    if lower.contains("iron") || n.contains("铁甲") {
        return Some("ironclad");
    }
    if lower.contains("silent") || n.contains("静默") || n.contains("猎手") {
        return Some("silent");
    }
    if lower.contains("defect") || n.contains("缺陷") || n.contains("机器") {
        return Some("defect");
    }
    if lower.contains("regent") || n.contains("摄政") {
        return Some("regent");
    }
    if lower.contains("necro") || n.contains("死灵") || n.contains("缚灵") {
        return Some("necrobinder");
    }
    None
}

fn intern_char_key(key: &str) -> Option<&'static str> {
    match key {
        "ironclad" => Some("ironclad"),
        "silent" => Some("silent"),
        "defect" => Some("defect"),
        "regent" => Some("regent"),
        "necrobinder" => Some("necrobinder"),
        _ => None,
    }
}

#[derive(Debug, Deserialize)]
struct AliasFile {
    cards: Vec<AliasEntry>,
}

#[derive(Debug, Deserialize)]
struct AliasEntry {
    id: String,
    #[serde(default)]
    en: Vec<String>,
    #[serde(default)]
    cn: Vec<String>,
}

fn alias_index() -> &'static Vec<(String, Vec<String>)> {
    // each entry: (canonical id upper, all surface forms lower/cn)
    static IDX: OnceLock<Vec<(String, Vec<String>)>> = OnceLock::new();
    IDX.get_or_init(|| {
        const RAW: &str = include_str!("../../data/card_aliases.json");
        let file: AliasFile = serde_json::from_str(RAW).unwrap_or(AliasFile { cards: vec![] });
        file.cards
            .into_iter()
            .map(|e| {
                let mut forms = Vec::new();
                forms.push(e.id.to_ascii_lowercase());
                forms.push(e.id.to_ascii_uppercase());
                for x in e.en {
                    forms.push(x.to_ascii_lowercase());
                    let compact: String = x
                        .to_ascii_lowercase()
                        .chars()
                        .filter(|c| c.is_alphanumeric())
                        .collect();
                    forms.push(compact);
                }
                for x in e.cn {
                    forms.push(x);
                }
                (e.id.to_ascii_uppercase(), forms)
            })
            .collect()
    })
}

/// Expand a name/id into comparable surface forms (EN/CN/id).
fn card_aliases(name_or_id: &str) -> Vec<String> {
    let n = name_or_id.to_ascii_lowercase();
    let mut v = vec![n.clone(), name_or_id.to_string()];
    let compact: String = n.chars().filter(|c| c.is_alphanumeric()).collect();
    if compact != n {
        v.push(compact.clone());
    }
    // pull all forms that share a token with this card
    for (id, forms) in alias_index() {
        let hit = forms.iter().any(|f| {
            f == &n
                || f == name_or_id
                || f == &compact
                || (!f.is_empty() && (n.contains(f) || f.contains(&n) || name_or_id.contains(f)))
        }) || id.eq_ignore_ascii_case(name_or_id);
        if hit {
            v.extend(forms.iter().cloned());
            v.push(id.to_ascii_lowercase());
        }
    }
    v.sort();
    v.dedup();
    v
}

fn card_match_score(card: &Card, arch: &Archetype) -> i32 {
    let aliases = card_aliases(&card.name);
    let id_aliases = card_aliases(&card.id);
    let cid = card.id.to_ascii_uppercase();
    let mut s = 0i32;

    for core in &arch.core_cards {
        let core_forms = card_aliases(core);
        let mut hit = false;
        for a in aliases.iter().chain(id_aliases.iter()) {
            for c in &core_forms {
                if a == c || a.contains(c.as_str()) || c.contains(a.as_str()) {
                    s += 28;
                    hit = true;
                    break;
                }
            }
            if hit {
                break;
            }
        }
        if hit {
            break;
        }
    }
    for id in &arch.core_ids {
        if cid == *id || cid.contains(id) || id.contains(cid.as_str()) {
            s += 30;
            break;
        }
    }
    // tag soft match via description / name
    let desc = card
        .description
        .as_deref()
        .unwrap_or("")
        .to_ascii_lowercase();
    let cname = card.name.to_ascii_lowercase();
    for tag in &arch.tags {
        let t = tag.to_ascii_lowercase();
        if desc.contains(&t) || cname.contains(&t) || card.name.contains(tag.as_str()) {
            s += 4;
        }
    }
    s
}

// ── EN → CN translation layer ──

/// Build a reverse lookup: lowercase English name/id → primary Chinese name.
fn en_to_cn_index() -> &'static HashMap<String, String> {
    static IDX: OnceLock<HashMap<String, String>> = OnceLock::new();
    IDX.get_or_init(|| {
        const RAW: &str = include_str!("../../data/card_aliases.json");
        let file: AliasFile = serde_json::from_str(RAW).unwrap_or(AliasFile { cards: vec![] });
        let mut map = HashMap::new();
        for entry in &file.cards {
            let primary_cn = entry.cn.first().cloned().unwrap_or_default();
            if primary_cn.is_empty() {
                continue;
            }
            for en in &entry.en {
                map.insert(en.to_ascii_lowercase(), primary_cn.clone());
            }
            map.insert(entry.id.to_ascii_lowercase(), primary_cn.clone());
        }
        map
    })
}

/// Translate a single card/relic name from English to Chinese.
/// Returns the original string if no translation is found.
pub fn translate_card_name(name: &str) -> String {
    let idx = en_to_cn_index();
    let key = name.trim().to_ascii_lowercase();
    if let Some(cn) = idx.get(&key) {
        return cn.clone();
    }
    // Try without spaces (e.g. "BodySlam" → "body slam")
    let compact: String = key.chars().filter(|c| c.is_alphanumeric()).collect();
    if compact != key {
        if let Some(cn) = idx.get(&compact) {
            return cn.clone();
        }
    }
    // Also try id-style SCREAMING_SNAKE as spaced words
    if key.contains('_') {
        let spaced = key.replace('_', " ");
        if let Some(cn) = idx.get(&spaced) {
            return cn.clone();
        }
    }
    name.to_string()
}

/// UI-facing card label: Chinese alias when known, else API name.
pub fn display_name(card: &Card) -> String {
    let by_id = translate_card_name(&card.id);
    if by_id != card.id && !by_id.is_empty() {
        // Only accept if we actually translated (id often looks English)
        let looks_cjk = by_id.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c));
        if looks_cjk {
            return by_id;
        }
    }
    let by_name = translate_card_name(&card.name);
    if by_name
        .chars()
        .any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
    {
        return by_name;
    }
    if !card.name.is_empty() {
        card.name.clone()
    } else {
        card.id.clone()
    }
}

/// Replace known English card/relic names in a text with their Chinese equivalents.
pub fn translate_text(text: &str) -> String {
    let idx = en_to_cn_index();
    if idx.is_empty() {
        return text.to_string();
    }

    // Collect (en_name, cn_name) pairs sorted by length descending (longer = more specific first)
    let mut pairs: Vec<(&String, &String)> = idx.iter().collect();
    pairs.sort_by_key(|(en, _)| -(en.len() as i32));

    let mut result = text.to_string();
    for (en, cn) in &pairs {
        if en.len() < 4 {
            continue; // skip short fragments that might match accidentally
        }
        let en_lower = en.to_ascii_lowercase();
        // Replace all occurrences (loop in case name appears multiple times)
        loop {
            let lower = result.to_ascii_lowercase();
            let Some(pos) = lower.find(&en_lower) else { break };
            let end = pos + en.len();
            // Check word boundary: don't replace if it's a substring of another word
            let before_ok = pos == 0
                || !result
                    .as_bytes()
                    .get(pos - 1)
                    .map(|b| b.is_ascii_alphabetic())
                    .unwrap_or(true);
            let after_ok = !result
                .as_bytes()
                .get(end)
                .map(|b| b.is_ascii_alphabetic())
                .unwrap_or(true);
            if before_ok && after_ok {
                result.replace_range(pos..end, cn);
            } else {
                break; // safety: avoid infinite loop on partial match
            }
        }
    }
    result
}

/// Score how well current deck/relics/hand match each archetype; return top kits.
pub fn detect_archetypes(state: &GameState) -> Option<ArchetypeKit> {
    let key = detect_character(state.character.as_deref())?;
    let ch = db().characters.get(key)?;
    let mut cards: Vec<&Card> = Vec::new();
    cards.extend(state.deck.iter());
    if let Some(c) = &state.combat_state {
        cards.extend(c.hand.iter());
        cards.extend(c.draw_pile.iter());
        cards.extend(c.discard_pile.iter());
    }
    if let Some(r) = &state.reward_state {
        cards.extend(r.cards.iter());
        for it in &r.items {
            cards.extend(it.cards.iter());
        }
    }
    if let Some(s) = &state.shop_state {
        for si in &s.cards {
            cards.push(&si.item);
        }
    }

    let relic_blob = state
        .relics
        .iter()
        .map(|r| format!("{} {}", r.name, r.id))
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase();

    let mut ranked: Vec<(i32, &Archetype)> = ch
        .archetypes
        .iter()
        .map(|a| {
            let mut score = 0i32;
            // tier bias
            score += match a.tier.as_str() {
                "S" => 3,
                "A" => 2,
                "B" => 1,
                _ => 0,
            };
            for c in &cards {
                let m = card_match_score(c, a);
                if m > 0 {
                    score += m;
                }
            }
            for r in &a.key_relics {
                if relic_blob.contains(&r.to_ascii_lowercase()) {
                    score += 18;
                }
            }
            // tag in relic/card names soft
            for t in &a.tags {
                if relic_blob.contains(&t.to_ascii_lowercase()) {
                    score += 3;
                }
            }
            (score, a)
        })
        .collect();
    ranked.sort_by(|a, b| b.0.cmp(&a.0));

    let (best_s, best) = ranked.first()?;
    let best_score = *best_s;
    // If no card evidence, still return S-tier default for character
    let primary = if best_score >= 8 {
        (*best).clone()
    } else {
        ch.archetypes
            .iter()
            .find(|a| a.tier == "S")
            .unwrap_or(best)
            .clone()
    };
    let secondary = ranked
        .get(1)
        .filter(|(s, _)| *s >= 12 && *s + 10 >= best_score.max(8))
        .map(|(_, a)| (*a).clone());

    Some(ArchetypeKit {
        character_key: key.to_string(),
        character_summary: ch.summary.clone(),
        primary,
        secondary,
        match_score: best_score,
    })
}

/// Extra score for reward/shop card evaluation from archetype knowledge.
pub fn score_card_for_archetypes(card: &Card, state: &GameState) -> (i32, Vec<String>) {
    let mut score = 0i32;
    let mut why = Vec::new();
    let db = db();

    // generic
    let cname = card.name.to_ascii_lowercase();
    for g in &db.generic_card_bias.always_good {
        if cname.contains(&g.to_ascii_lowercase()) {
            score += 12;
            why.push(format!("泛用强卡({g})"));
            break;
        }
    }
    for g in &db.generic_card_bias.energy {
        if cname.contains(&g.to_ascii_lowercase()) {
            score += 8;
            why.push("能量/过牌资源".into());
            break;
        }
    }
    let late = state.act.unwrap_or(1) >= 2 || state.deck.len() >= 22;
    if late {
        for g in &db.generic_card_bias.usually_skip_late {
            if cname.contains(&g.to_ascii_lowercase())
                || card.id.to_ascii_uppercase().contains("STRIKE")
                || card.id.to_ascii_uppercase().contains("DEFEND")
            {
                score -= 10;
                why.push("后期基础牌，低优先".into());
                break;
            }
        }
    }

    if let Some(kit) = detect_archetypes(state) {
        let m = card_match_score(card, &kit.primary);
        if m > 0 {
            score += m;
            why.push(format!("契合【{}】核心", kit.primary.name));
        } else {
            // soft tag
            let desc = card.description.as_deref().unwrap_or("").to_ascii_lowercase();
            let mut tag_hit = false;
            for t in &kit.primary.tags {
                if desc.contains(&t.to_ascii_lowercase()) {
                    score += 6;
                    tag_hit = true;
                }
            }
            if tag_hit {
                why.push(format!("标签贴合{}", kit.primary.name));
            } else if !kit.primary.core_cards.is_empty() {
                score -= 4;
                why.push(format!("与主流派{}关联弱", kit.primary.name));
            }
        }
        if let Some(sec) = &kit.secondary {
            let m2 = card_match_score(card, sec);
            if m2 > 0 {
                score += m2 / 2;
                why.push(format!("也适合支线{}", sec.name));
            }
        }
    }

    why.truncate(3);
    (score, why)
}

/// Emit high-level archetype tips as recommendations for any screen.
pub fn tips_for_state(state: &GameState) -> Vec<Recommendation> {
    let mut out = Vec::new();
    let db = db();

    let Some(kit) = detect_archetypes(state) else {
        out.push(Recommendation {
            rank: 1,
            title: "流派库".into(),
            description: format!(
                "未识别角色（{:?}）。通用：{}。",
                state.character,
                db.principles.pick.first().cloned().unwrap_or_default()
            ),
        });
        return out;
    };

    let conf = if kit.match_score >= 40 {
        "高"
    } else if kit.match_score >= 15 {
        "中"
    } else {
        "低（按 S 级默认）"
    };

    out.push(Recommendation {
        rank: 1,
        title: format!("★ 流派 · {}", kit.primary.name),
        description: format!(
            "角色知识：{} | 匹配度{}（分{}）| Tier {} | {}",
            kit.character_summary,
            conf,
            kit.match_score,
            kit.primary.tier,
            translate_text(&kit.primary.pick_priority)
        ),
    });
    out.push(Recommendation {
        rank: 2,
        title: "核心牌".into(),
        description: format!(
            "优先拿：{}",
            kit.primary.core_cards.iter().map(|c| translate_card_name(c)).collect::<Vec<_>>().join("、")
        ),
    });
    out.push(Recommendation {
        rank: 3,
        title: "过牌/留牌".into(),
        description: format!(
            "留：{} ｜ 扔：{}",
            translate_text(&kit.primary.keep),
            translate_text(&kit.primary.dump)
        ),
    });
    out.push(Recommendation {
        rank: 4,
        title: "出牌思路".into(),
        description: translate_text(&kit.primary.play),
    });
    if let Some(sec) = &kit.secondary {
        out.push(Recommendation {
            rank: 5,
            title: format!("支线 · {}", sec.name),
            description: format!("可双修：{}", translate_text(&sec.pick_priority)),
        });
    }
    if !kit.primary.key_relics.is_empty() {
        out.push(Recommendation {
            rank: 6,
            title: "关键遗物".into(),
            description: kit.primary.key_relics.iter().map(|r| translate_card_name(r)).collect::<Vec<_>>().join("、"),
        });
    }
    // surface mulligan / play principles from DB
    if let Some(k) = db.principles.mulligan_keep.first() {
        out.push(Recommendation {
            rank: 7,
            title: "通用留牌".into(),
            description: format!(
                "{}；扔：{}",
                k,
                db.principles
                    .mulligan_dump
                    .first()
                    .cloned()
                    .unwrap_or_default()
            ),
        });
    }
    if let Some(p) = db.principles.play_order.first() {
        out.push(Recommendation {
            rank: 8,
            title: "通用出牌序".into(),
            description: format!(
                "{}{}",
                p,
                db.principles
                    .play_order
                    .get(1)
                    .map(|x| format!("；{x}"))
                    .unwrap_or_default()
            ),
        });
    }
    if let Some(p) = db.principles.pick.first() {
        out.push(Recommendation {
            rank: 9,
            title: "总原则".into(),
            description: p.clone(),
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game::state::ScreenType;

    #[test]
    fn db_loads_and_detects_ironclad() {
        let db = db();
        assert!(db.characters.contains_key("ironclad"));
        assert!(db.characters.contains_key("silent"));
        assert_eq!(detect_character(Some("铁甲战士")), Some("ironclad"));
        assert_eq!(detect_character(Some("IRONCLAD")), Some("ironclad"));
    }

    #[test]
    fn display_name_prefers_chinese_alias() {
        let c = Card {
            id: "HEAVY_BLADE".into(),
            name: "Heavy Blade".into(),
            card_type: "Attack".into(),
            cost: 2,
            upgraded: false,
            damage: Some(14),
            block: None,
            magic_number: None,
            description: None,
            rarity: Some("Uncommon".into()),
        };
        let label = display_name(&c);
        assert!(
            label.contains("重") || label.contains("刃") || label == "Heavy Blade",
            "label={label}"
        );
        // known entry should resolve to CN
        assert_ne!(translate_card_name("Heavy Blade"), "Heavy Blade");
    }

    #[test]
    fn tips_strength_with_inflame() {
        let state = GameState {
            screen_type: ScreenType::Reward,
            seed: None,
            character: Some("铁甲战士".into()),
            act: Some(1),
            floor: Some(3),
            ascension_level: None,
            current_hp: Some(70),
            max_hp: Some(80),
            gold: Some(50),
            deck: vec![Card {
                id: "INFLAME".into(),
                name: "Inflame".into(),
                card_type: "Power".into(),
                cost: 1,
                upgraded: false,
                damage: None,
                block: None,
                magic_number: None,
                description: Some("Gain Strength".into()),
                rarity: Some("Uncommon".into()),
            }],
            relics: vec![],
            potions: vec![],
            combat_state: None,
            map_state: None,
            shop_state: None,
            event_state: None,
            reward_state: None,
            rest_state: None,
        };
        let kit = detect_archetypes(&state).expect("kit");
        assert!(
            kit.primary.id.contains("strength")
                || kit.primary.core_cards.iter().any(|c| c.contains("Inflame")),
            "primary={:?}",
            kit.primary.id
        );
        let tips = tips_for_state(&state);
        assert!(!tips.is_empty());
        let (s, why) = score_card_for_archetypes(
            &Card {
                id: "HEAVY_BLADE".into(),
                name: "Heavy Blade".into(),
                card_type: "Attack".into(),
                cost: 2,
                upgraded: false,
                damage: Some(14),
                block: None,
                magic_number: None,
                description: None,
                rarity: Some("Uncommon".into()),
            },
            &state,
        );
        assert!(s > 10, "score={s} why={why:?}");
    }
}
