//! Convert STS2 MCP v0.4 JSON (`/api/v1/singleplayer`) into internal `GameState`.

use super::state::*;
use anyhow::{bail, Result};
use serde_json::Value;

/// Parse raw API body: either already-normalized GameState, or STS2MCP envelope.
pub fn parse_game_state_json(text: &str) -> Result<GameState> {
    let value: Value = serde_json::from_str(text)?;

    // Error objects
    if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
        bail!("API error: {err}");
    }

    // STS2MCP: has state_type + player/run
    if value.get("state_type").is_some() || value.get("player").is_some() {
        return Ok(from_sts2mcp(&value));
    }

    // Legacy / alternate shape
    match serde_json::from_value::<GameState>(value.clone()) {
        Ok(s) => Ok(s),
        Err(e) => {
            // Last resort: try adapter on unknown shape if player-like fields exist
            if value.get("screen_type").is_some() {
                bail!("Failed to parse GameState: {e}");
            }
            Ok(from_sts2mcp(&value))
        }
    }
}

fn from_sts2mcp(v: &Value) -> GameState {
    let state_type = v
        .get("state_type")
        .and_then(|x| x.as_str())
        .unwrap_or("unknown");
    let screen_type = map_state_type(state_type);

    let player = v.get("player").cloned().unwrap_or(Value::Null);
    let run = v.get("run").cloned().unwrap_or(Value::Null);

    let character = player
        .get("character")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let current_hp = int_field(&player, &["hp", "current_hp"]);
    let max_hp = int_field(&player, &["max_hp"]);
    let gold = int_field(&player, &["gold"]);
    let act = int_field(&run, &["act"]).map(|n| n as u8);
    let floor = int_field(&run, &["floor"]).map(|n| n as u8);
    let ascension_level = int_field(&run, &["ascension", "ascension_level"]).map(|n| n as u8);

    let relics = parse_relics(player.get("relics"));
    let potions = parse_potions(player.get("potions"));
    let deck = parse_cards(
        player
            .get("deck")
            .or_else(|| v.get("deck"))
            .or_else(|| player.get("cards")),
    );

    let map_state = v.get("map").map(parse_map);
    let combat_state = v
        .get("combat")
        .or_else(|| v.get("battle"))
        .or_else(|| v.get("combat_state"))
        .or_else(|| v.get("player_combat"))
        .map(parse_combat);
    let shop_state = v.get("shop").map(parse_shop);
    let event_state = v.get("event").map(parse_event);
    let reward_state = v
        .get("rewards")
        .or_else(|| v.get("reward"))
        .or_else(|| v.get("card_reward"))
        .map(parse_reward);
    let rest_state = v
        .get("rest")
        .or_else(|| v.get("rest_site"))
        .map(parse_rest);

    GameState {
        screen_type,
        seed: v
            .get("seed")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        character,
        act,
        floor,
        ascension_level,
        current_hp,
        max_hp,
        gold,
        deck,
        relics,
        potions,
        combat_state,
        map_state,
        shop_state,
        event_state,
        reward_state,
        rest_state,
    }
}

fn map_state_type(s: &str) -> ScreenType {
    match s.to_ascii_lowercase().as_str() {
        "map" => ScreenType::Map,
        "combat" | "battle" => ScreenType::Combat,
        "shop" | "merchant" | "fake_merchant" => ScreenType::Shop,
        "event" => ScreenType::Event,
        "reward" | "rewards" | "card_reward" => ScreenType::Reward,
        "rest" | "rest_site" | "restsite" => ScreenType::Rest,
        "boss_reward" | "bossreward" => ScreenType::BossReward,
        "game_over" | "gameover" => ScreenType::GameOver,
        "none" | "" => ScreenType::None,
        _ => ScreenType::Unknown,
    }
}

fn int_field(v: &Value, keys: &[&str]) -> Option<i32> {
    for k in keys {
        if let Some(n) = v.get(*k) {
            if let Some(i) = n.as_i64() {
                return Some(i as i32);
            }
            if let Some(u) = n.as_u64() {
                return Some(u as i32);
            }
            if let Some(f) = n.as_f64() {
                return Some(f as i32);
            }
        }
    }
    None
}

fn str_field(v: &Value, keys: &[&str]) -> Option<String> {
    for k in keys {
        if let Some(s) = v.get(*k).and_then(|x| x.as_str()) {
            return Some(s.to_string());
        }
    }
    None
}

fn parse_relics(v: Option<&Value>) -> Vec<Relic> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|item| {
            let id = str_field(item, &["id", "relic_id"]).unwrap_or_else(|| "unknown".into());
            let name = str_field(item, &["name", "relic_name"]).unwrap_or_else(|| id.clone());
            Some(Relic {
                id,
                name,
                description: str_field(item, &["description", "relic_description"]),
                counter: int_field(item, &["counter"]),
            })
        })
        .collect()
}

fn parse_potions(v: Option<&Value>) -> Vec<Potion> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|item| {
            // empty slot may be null
            if item.is_null() {
                return None;
            }
            let id = str_field(item, &["id", "potion_id"]).unwrap_or_else(|| "unknown".into());
            let name = str_field(item, &["name", "potion_name"]).unwrap_or_else(|| id.clone());
            Some(Potion {
                id,
                name,
                description: str_field(item, &["description", "potion_description"]),
            })
        })
        .collect()
}

fn parse_cards(v: Option<&Value>) -> Vec<Card> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter().filter_map(parse_card).collect()
}

fn parse_card(item: &Value) -> Option<Card> {
    if item.is_null() {
        return None;
    }
    // Nested { card: {...}, price } shop style
    let item = item.get("card").unwrap_or(item);
    if item.is_null() {
        return None;
    }
    let id = str_field(item, &["id", "card_id", "key", "uuid"]).unwrap_or_else(|| "unknown".into());
    let name = str_field(item, &["name", "card_name", "title", "display_name"])
        .unwrap_or_else(|| id.clone());
    // type may be nested under "type" as string or object
    let card_type = str_field(item, &["card_type", "type", "kind", "base_type"]).unwrap_or_else(|| {
        item.get("type")
            .and_then(|t| t.get("name").or_else(|| t.get("id")))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string()
    });
    let cost = int_field(
        item,
        &["cost", "card_cost", "card_star_cost", "energy_cost", "star_cost"],
    )
    .unwrap_or(0);
    let upgraded = item
        .get("upgraded")
        .and_then(|x| x.as_bool())
        .or_else(|| item.get("is_upgraded").and_then(|x| x.as_bool()))
        .or_else(|| {
            // upgrade_level > 0
            int_field(item, &["upgrade_level", "upgrades"]).map(|n| n > 0)
        })
        .unwrap_or(false);
    Some(Card {
        id,
        name,
        card_type,
        cost,
        upgraded,
        damage: int_field(item, &["damage", "current_damage", "base_damage", "dmg"]),
        block: int_field(item, &["block", "current_block", "base_block", "blk"]),
        magic_number: int_field(item, &["magic_number", "magic", "misc", "amount"]),
        description: str_field(
            item,
            &["description", "card_description", "text", "raw_description", "desc"],
        ),
        rarity: str_field(item, &["rarity", "card_rarity", "rarity_id"]),
    })
}

fn node_id(col: i64, row: i64) -> String {
    format!("{col},{row}")
}

fn parse_children_coords(v: Option<&Value>) -> Vec<String> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    let mut out = Vec::new();
    for ch in arr {
        // STS2MCP: children are [col, row] pairs
        if let Value::Array(pair) = ch {
            if pair.len() >= 2 {
                let col = pair[0].as_i64().or_else(|| pair[0].as_u64().map(|u| u as i64)).unwrap_or(0);
                let row = pair[1].as_i64().or_else(|| pair[1].as_u64().map(|u| u as i64)).unwrap_or(0);
                out.push(node_id(col, row));
                continue;
            }
        }
        // fallback object
        if let Some(col) = ch.get("col").and_then(|x| x.as_i64()) {
            let row = ch.get("row").and_then(|x| x.as_i64()).unwrap_or(0);
            out.push(node_id(col, row));
        }
    }
    out
}

fn parse_map(m: &Value) -> MapState {
    let current = m.get("current_position");
    let current_node_id = current.map(|c| {
        node_id(
            c.get("col").and_then(|x| x.as_i64()).unwrap_or(0),
            c.get("row").and_then(|x| x.as_i64()).unwrap_or(0),
        )
    });

    // Full graph from map.nodes
    let mut nodes = Vec::new();
    if let Some(Value::Array(arr)) = m.get("nodes") {
        for n in arr {
            let col = n.get("col").and_then(|x| x.as_i64()).unwrap_or(0);
            let row = n.get("row").and_then(|x| x.as_i64()).unwrap_or(0);
            let typ = n
                .get("type")
                .and_then(|x| x.as_str())
                .unwrap_or("Unknown");
            nodes.push(MapNode {
                id: node_id(col, row),
                symbol: typ.to_string(),
                x: Some(col as f32),
                y: Some(row as f32),
                children: parse_children_coords(n.get("children")),
                parents: vec![],
            });
        }
    }

    // Immediate options
    let mut next_options = Vec::new();
    if let Some(Value::Array(opts)) = m.get("next_options") {
        for o in opts {
            let col = o.get("col").and_then(|x| x.as_i64()).unwrap_or(0);
            let row = o.get("row").and_then(|x| x.as_i64()).unwrap_or(0);
            let typ = o
                .get("type")
                .and_then(|x| x.as_str())
                .unwrap_or("Unknown")
                .to_string();
            let idx = o.get("index").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
            let leads = o
                .get("leads_to")
                .and_then(|x| x.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|n| {
                            node_id(
                                n.get("col").and_then(|x| x.as_i64()).unwrap_or(0),
                                n.get("row").and_then(|x| x.as_i64()).unwrap_or(0),
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            next_options.push(MapNextOption {
                index: idx,
                node_id: node_id(col, row),
                symbol: typ,
                col: col as i32,
                row: row as i32,
                leads_to: leads,
            });
        }
    }

    // If graph empty but we have next_options, synthesize nodes from options
    if nodes.is_empty() {
        for o in &next_options {
            nodes.push(MapNode {
                id: o.node_id.clone(),
                symbol: o.symbol.clone(),
                x: Some(o.col as f32),
                y: Some(o.row as f32),
                children: o.leads_to.clone(),
                parents: vec![],
            });
        }
    }

    // Populate reverse edges (parents) from children references
    {
        // Collect (parent_id, child_id) pairs
        let mut child_parents: std::collections::HashMap<String, Vec<String>> =
            std::collections::HashMap::new();
        for n in &nodes {
            for ch in &n.children {
                child_parents
                    .entry(ch.clone())
                    .or_default()
                    .push(n.id.clone());
            }
        }
        for n in &mut nodes {
            if let Some(p) = child_parents.remove(&n.id) {
                n.parents = p;
            }
        }
    }

    let boss_id = m.get("boss").map(|b| {
        node_id(
            b.get("col").and_then(|x| x.as_i64()).unwrap_or(0),
            b.get("row").and_then(|x| x.as_i64()).unwrap_or(0),
        )
    });

    MapState {
        nodes,
        current_node_id,
        next_options,
        boss_id,
    }
}

fn parse_combat(c: &Value) -> CombatState {
    // Some mods nest under player / combat_state
    let c = c
        .get("player_combat")
        .or_else(|| c.get("player"))
        .filter(|p| p.get("hand").is_some() || p.get("energy").is_some())
        .unwrap_or(c);

    let hand = parse_cards(
        c.get("hand")
            .or_else(|| c.get("cards"))
            .or_else(|| c.get("hand_cards")),
    );
    let draw_pile = parse_cards(
        c.get("draw_pile")
            .or_else(|| c.get("draw"))
            .or_else(|| c.get("draw_pile_cards")),
    );
    let discard_pile = parse_cards(
        c.get("discard_pile")
            .or_else(|| c.get("discard"))
            .or_else(|| c.get("discard_pile_cards")),
    );
    let exhaust_pile = parse_cards(
        c.get("exhaust_pile")
            .or_else(|| c.get("exhaust"))
            .or_else(|| c.get("exhaust_pile_cards")),
    );

    let enemies = c
        .get("enemies")
        .or_else(|| c.get("monsters"))
        .or_else(|| c.get("enemy_list"))
        .and_then(|x| x.as_array())
        .map(|arr| arr.iter().filter_map(parse_enemy).collect())
        .unwrap_or_default();

    let powers = parse_powers(
        c.get("powers")
            .or_else(|| c.get("status"))
            .or_else(|| c.get("orbs")),
    );

    CombatState {
        turn: int_field(c, &["turn", "round", "turn_number"]).unwrap_or(0) as u32,
        hand,
        draw_pile,
        discard_pile,
        exhaust_pile,
        energy: int_field(c, &["energy", "current_energy"]).unwrap_or(0),
        max_energy: int_field(c, &["max_energy", "energy_max"]).unwrap_or(3),
        block: int_field(c, &["block"]).unwrap_or(0),
        powers,
        enemies,
    }
}

fn parse_enemy(e: &Value) -> Option<Enemy> {
    if e.is_null() {
        return None;
    }
    // skip dead/null slots
    if e.get("is_dead").and_then(|x| x.as_bool()) == Some(true) {
        return None;
    }
    let id = str_field(e, &["id", "enemy_id", "monster_id", "key"]).unwrap_or_else(|| "enemy".into());
    let name = str_field(e, &["name", "enemy_name", "display_name"]).unwrap_or_else(|| id.clone());
    let intent_val = e
        .get("intent")
        .or_else(|| e.get("move"))
        .or_else(|| e.get("next_move"));
    let intent = intent_val.map(|i| {
        // intent may be string
        if let Some(s) = i.as_str() {
            return EnemyIntent {
                intent_type: Some(s.to_string()),
                damage: None,
                hits: None,
                block: None,
            };
        }
        EnemyIntent {
            intent_type: str_field(i, &["intent_type", "type", "move_type", "name", "id"]),
            damage: int_field(i, &["damage", "base_damage", "total_damage", "dmg"]),
            hits: int_field(i, &["hits", "hit_count", "multi"]).map(|n| n as u32),
            block: int_field(i, &["block", "base_block"]),
        }
    });
    Some(Enemy {
        id,
        name,
        current_hp: int_field(e, &["hp", "current_hp", "health"]),
        max_hp: int_field(e, &["max_hp", "max_health"]),
        block: int_field(e, &["block", "current_block"]).unwrap_or(0),
        intent,
        powers: parse_powers(e.get("powers").or_else(|| e.get("status")).or_else(|| e.get("buffs"))),
    })
}

fn parse_powers(v: Option<&Value>) -> Vec<Power> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|p| {
            if p.is_null() {
                return None;
            }
            let id = str_field(p, &["id", "power_id"]).unwrap_or_else(|| "power".into());
            let name = str_field(p, &["name"]).unwrap_or_else(|| id.clone());
            Some(Power {
                id,
                name,
                amount: int_field(p, &["amount", "stacks", "counter"]).unwrap_or(0),
                description: str_field(p, &["description"]),
            })
        })
        .collect()
}

fn parse_shop(s: &Value) -> ShopState {
    ShopState {
        cards: parse_shop_cards(s.get("cards")),
        relics: parse_shop_relics(s.get("relics")),
        potions: parse_shop_potions(s.get("potions")),
        removal_cost: int_field(s, &["removal_cost", "card_removal"]),
    }
}

fn parse_shop_cards(v: Option<&Value>) -> Vec<ShopItem<Card>> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|item| {
            let card = parse_card(item.get("card").unwrap_or(item))?;
            let price = int_field(item, &["price", "cost", "gold"]).unwrap_or(0);
            Some(ShopItem { item: card, price })
        })
        .collect()
}

fn parse_shop_relics(v: Option<&Value>) -> Vec<ShopItem<Relic>> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|item| {
            let inner = item.get("relic").unwrap_or(item);
            let id = str_field(inner, &["id"]).unwrap_or_else(|| "relic".into());
            let name = str_field(inner, &["name"]).unwrap_or_else(|| id.clone());
            let price = int_field(item, &["price", "cost", "gold"]).unwrap_or(0);
            Some(ShopItem {
                item: Relic {
                    id,
                    name,
                    description: str_field(inner, &["description"]),
                    counter: int_field(inner, &["counter"]),
                },
                price,
            })
        })
        .collect()
}

fn parse_shop_potions(v: Option<&Value>) -> Vec<ShopItem<Potion>> {
    let Some(Value::Array(arr)) = v else {
        return vec![];
    };
    arr.iter()
        .filter_map(|item| {
            let inner = item.get("potion").unwrap_or(item);
            if inner.is_null() {
                return None;
            }
            let id = str_field(inner, &["id"]).unwrap_or_else(|| "potion".into());
            let name = str_field(inner, &["name"]).unwrap_or_else(|| id.clone());
            let price = int_field(item, &["price", "cost", "gold"]).unwrap_or(0);
            Some(ShopItem {
                item: Potion {
                    id,
                    name,
                    description: str_field(inner, &["description"]),
                },
                price,
            })
        })
        .collect()
}

fn parse_event(e: &Value) -> EventState {
    let choices = e
        .get("choices")
        .or_else(|| e.get("options"))
        .and_then(|x| x.as_array())
        .map(|arr| {
            arr.iter()
                .enumerate()
                .map(|(i, c)| EventChoice {
                    id: str_field(c, &["id", "index"]).unwrap_or_else(|| i.to_string()),
                    text: str_field(c, &["text", "name", "description"])
                        .unwrap_or_else(|| format!("选项{i}")),
                    available: c
                        .get("available")
                        .and_then(|x| x.as_bool())
                        .unwrap_or(true),
                    cost: str_field(c, &["cost"]),
                })
                .collect()
        })
        .unwrap_or_default();

    EventState {
        event_name: str_field(e, &["event_name", "name", "id"]),
        text: str_field(e, &["text", "description"]),
        choices,
    }
}

fn parse_reward(r: &Value) -> RewardState {
    // STS2 MCP: { items: [{type, description, gold_amount, index}], can_proceed }
    let mut items = Vec::new();
    let mut gold = int_field(r, &["gold", "gold_amount"]);
    let mut cards = parse_cards(
        r.get("cards")
            .or_else(|| r.get("card_reward"))
            .or_else(|| r.get("card_options")),
    );

    if let Some(Value::Array(arr)) = r.get("items") {
        for it in arr {
            let item_type = str_field(it, &["type", "item_type", "reward_type"])
                .unwrap_or_else(|| "unknown".into());
            let index = int_field(it, &["index", "slot"]).unwrap_or(0);
            let description = str_field(it, &["description", "text", "label"]);
            let gold_amount = int_field(it, &["gold_amount", "gold", "amount"]);
            if item_type.eq_ignore_ascii_case("gold") && gold.is_none() {
                gold = gold_amount;
            }
            let nested = parse_cards(
                it.get("cards")
                    .or_else(|| it.get("options"))
                    .or_else(|| it.get("choices"))
                    .or_else(|| it.get("card_options"))
                    .or_else(|| it.get("card_choices")),
            );
            if item_type.eq_ignore_ascii_case("card") && !nested.is_empty() {
                cards = nested.clone();
            }
            items.push(RewardItem {
                index,
                item_type,
                description,
                gold_amount,
                cards: nested,
            });
        }
    }
    // Top-level alternate shapes for open card pick
    if cards.is_empty() {
        cards = parse_cards(
            r.get("card_choices")
                .or_else(|| r.get("card_options"))
                .or_else(|| r.get("reward_cards")),
        );
    }

    let can_skip = r
        .get("can_skip")
        .or_else(|| r.get("can_proceed"))
        .and_then(|x| x.as_bool())
        .unwrap_or(true);

    RewardState {
        cards,
        gold,
        can_skip,
        items,
    }
}

fn parse_rest(r: &Value) -> RestState {
    let options = r
        .get("options")
        .and_then(|x| x.as_array())
        .map(|arr| {
            arr.iter()
                .enumerate()
                .map(|(i, o)| RestOption {
                    id: str_field(o, &["id"]).unwrap_or_else(|| i.to_string()),
                    name: str_field(o, &["name", "text"]).unwrap_or_else(|| format!("选项{i}")),
                    description: str_field(o, &["description"]),
                })
                .collect()
        })
        .unwrap_or_default();
    RestState {
        options,
        upgradeable_cards: parse_cards(r.get("upgradeable_cards").or_else(|| r.get("cards"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn parse_fixture_map() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/singleplayer_map.json");
        if !path.exists() {
            eprintln!("skip: fixture missing");
            return;
        }
        let text = std::fs::read_to_string(&path).unwrap();
        let state = parse_game_state_json(&text).unwrap();
        assert_eq!(state.screen_type, ScreenType::Map);
        assert_eq!(state.character.as_deref(), Some("铁甲战士"));
        assert_eq!(state.current_hp, Some(79));
        assert_eq!(state.max_hp, Some(80));
        assert_eq!(state.gold, Some(113));
        assert_eq!(state.act, Some(1));
        assert!(!state.relics.is_empty());
        let map = state.map_state.expect("map");
        assert!(!map.nodes.is_empty(), "full graph nodes");
        assert!(!map.next_options.is_empty(), "next_options");
        assert!(map.current_node_id.is_some());
        // children should be "col,row" ids
        assert!(map.nodes.iter().any(|n| !n.children.is_empty()));
    }

    #[test]
    fn parse_rewards_items() {
        let text = r#"{
            "state_type": "rewards",
            "rewards": {
                "items": [
                    {"index": 0, "type": "gold", "description": "14金币", "gold_amount": 14},
                    {"index": 1, "type": "card", "description": "将一张牌添加到你的牌组。"}
                ],
                "can_proceed": true
            },
            "run": {"act": 1, "floor": 1, "ascension": 0},
            "player": {
                "character": "铁甲战士",
                "hp": 79, "max_hp": 80, "block": 0, "gold": 99,
                "relics": [], "potions": []
            }
        }"#;
        let state = parse_game_state_json(text).unwrap();
        assert_eq!(state.screen_type, ScreenType::Reward);
        let rew = state.reward_state.expect("reward");
        assert_eq!(rew.gold, Some(14));
        assert_eq!(rew.items.len(), 2);
        assert!(rew.can_skip);
    }

    #[test]
    fn parse_empty_body_errors() {
        assert!(parse_game_state_json("").is_err());
        assert!(parse_game_state_json("{}").is_ok()); // Unknown screen but valid JSON
    }

    #[test]
    fn parse_error_envelope() {
        let text = r#"{"error": "Game not started yet"}"#;
        let err = parse_game_state_json(text).unwrap_err();
        assert!(err.to_string().contains("Game not started"));
    }

    #[test]
    fn parse_combat_nested_under_player_combat() {
        let text = r#"{
            "state_type": "combat",
            "combat": {
                "player_combat": {
                    "turn": 2,
                    "energy": 3,
                    "max_energy": 3,
                    "block": 5,
                    "hand": [
                        {"id": "strike", "name": "打击", "card_type": "Attack", "cost": 1, "damage": 6}
                    ],
                    "draw_pile": [],
                    "discard_pile": [],
                    "exhaust_pile": [],
                    "powers": [],
                    "enemies": [
                        {"id": "e1", "name": "敌人A", "hp": 20, "max_hp": 30, "block": 0, "intent": {"intent_type": "Attack", "damage": 8, "hits": 1}}
                    ]
                }
            },
            "run": {"act": 1, "floor": 3, "ascension": 0},
            "player": {"character": "Ironclad", "hp": 70, "max_hp": 80, "gold": 50, "relics": [], "potions": []}
        }"#;
        let state = parse_game_state_json(text).unwrap();
        assert_eq!(state.screen_type, ScreenType::Combat);
        let c = state.combat_state.expect("combat");
        assert_eq!(c.turn, 2);
        assert_eq!(c.block, 5);
        assert_eq!(c.hand.len(), 1);
        assert_eq!(c.enemies.len(), 1);
    }

    #[test]
    fn parse_shop_with_nested_cards() {
        let text = r#"{
            "state_type": "shop",
            "shop": {
                "cards": [
                    {"card": {"id": "heavy_blade", "name": "重刃", "card_type": "Attack", "cost": 2, "damage": 14}, "price": 120}
                ],
                "relics": [],
                "potions": [],
                "removal_cost": 75
            },
            "run": {"act": 1, "floor": 4, "ascension": 0},
            "player": {"character": "铁甲战士", "hp": 70, "max_hp": 80, "gold": 200, "relics": [], "potions": []}
        }"#;
        let state = parse_game_state_json(text).unwrap();
        assert_eq!(state.screen_type, ScreenType::Shop);
        let shop = state.shop_state.expect("shop");
        assert_eq!(shop.cards.len(), 1);
        assert_eq!(shop.cards[0].price, 120);
        assert_eq!(shop.cards[0].item.name, "重刃");
        assert_eq!(shop.removal_cost, Some(75));
    }

    #[test]
    fn parse_state_type_aliases() {
        for (input, expected) in &[
            ("merchant", ScreenType::Shop),
            ("fake_merchant", ScreenType::Shop),
            ("battle", ScreenType::Combat),
            ("bossreward", ScreenType::BossReward),
            ("restsite", ScreenType::Rest),
            ("gameover", ScreenType::GameOver),
        ] {
            let text = format!(
                r#"{{"state_type": "{input}", "run": {{"act": 1, "floor": 1}}, "player": {{"character": "x", "hp": 10, "max_hp": 10, "gold": 0, "relics": [], "potions": []}}}}"#
            );
            let state = parse_game_state_json(&text).unwrap();
            assert_eq!(
                state.screen_type, *expected,
                "state_type={input} → expected {expected:?}"
            );
        }
    }

    #[test]
    fn parse_map_parents_populated() {
        let text = r#"{
            "state_type": "map",
            "map": {
                "nodes": [
                    {"col": 0, "row": 0, "type": "Monster", "children": [[1, 0], [1, 1]]},
                    {"col": 1, "row": 0, "type": "Shop", "children": [[2, 0]]},
                    {"col": 1, "row": 1, "type": "Elite", "children": [[2, 0]]},
                    {"col": 2, "row": 0, "type": "RestSite", "children": []}
                ],
                "current_position": {"col": 0, "row": 0}
            },
            "run": {"act": 1, "floor": 1},
            "player": {"character": "Ironclad", "hp": 80, "max_hp": 80, "gold": 99, "relics": [], "potions": []}
        }"#;
        let state = parse_game_state_json(text).unwrap();
        let map = state.map_state.expect("map");
        assert_eq!(map.nodes.len(), 4);

        // Shop (1,0) should have parent (0,0)
        let shop = map.nodes.iter().find(|n| n.id == "1,0").expect("shop node");
        assert!(shop.parents.contains(&"0,0".to_string()), "shop parents: {:?}", shop.parents);

        // RestSite (2,0) should have parents (1,0) and (1,1)
        let rest = map.nodes.iter().find(|n| n.id == "2,0").expect("rest node");
        assert_eq!(rest.parents.len(), 2);
    }
}
