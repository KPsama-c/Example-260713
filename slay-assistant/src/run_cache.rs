//! Persist cards **owned** during a run so map screens with empty `deck` still have knowledge.
//!
//! v2: only owned deck / combat piles count toward enrich; reward offers and shop shelves
//! are never treated as owned. Counts preserve multiples (e.g. two Strikes).

use crate::game::state::{Card, GameState};
use parking_lot::Mutex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct OwnedEntry {
    card: Card,
    count: u32,
}

#[derive(Debug, Default, Clone, serde::Serialize, serde::Deserialize)]
struct CacheFile {
    /// Schema version; older files without this still deserialize (version=0).
    #[serde(default)]
    version: u32,
    seed: Option<String>,
    character: Option<String>,
    /// key: uppercase id or lowercase name → owned card + count
    #[serde(default)]
    owned: HashMap<String, OwnedEntry>,
    /// Legacy v1 field — migrated on load, not written for new saves.
    #[serde(default, skip_serializing)]
    cards: HashMap<String, Card>,
}

#[derive(Clone)]
pub struct RunCache {
    inner: Arc<Mutex<CacheFile>>,
    path: PathBuf,
    last_write: Arc<Mutex<Instant>>,
    /// Full last-known GameState snapshot for degraded fallback on mod bugs.
    last_snapshot: Arc<Mutex<Option<GameState>>>,
}

impl RunCache {
    pub fn load_default() -> Self {
        let path = default_cache_path();
        let mut data = if path.exists() {
            std::fs::read_to_string(&path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default()
        } else {
            CacheFile::default()
        };
        migrate_v1(&mut data);
        log::info!(
            "Run cache: {} ({} unique / {} total owned)",
            path.display(),
            data.owned.len(),
            data.owned.values().map(|e| e.count as usize).sum::<usize>()
        );
        Self {
            inner: Arc::new(Mutex::new(data)),
            path,
            last_write: Arc::new(Mutex::new(Instant::now())),
            last_snapshot: Arc::new(Mutex::new(None)),
        }
    }

    /// Merge owned cards from this snapshot; reset if seed/character changed.
    /// Does **not** learn reward candidates or shop shelf items as owned.
    pub fn observe(&self, state: &GameState) {
        *self.last_snapshot.lock() = Some(state.clone());

        let mut g = self.inner.lock();
        let seed = state.seed.clone();
        let character = state.character.clone();

        let run_changed = match (&g.seed, &seed) {
            (Some(a), Some(b)) if a != b => true,
            (Some(_), None) => false,
            (None, Some(_)) if !g.owned.is_empty() && g.character != character => true,
            _ => g.character.is_some()
                && character.is_some()
                && g.character != character
                && g.owned.len() > 5,
        };

        if run_changed {
            log::info!(
                "Run cache reset (seed/character change): {:?} -> {:?}",
                g.character,
                character
            );
            g.owned.clear();
        }
        if seed.is_some() {
            g.seed = seed;
        }
        if character.is_some() {
            g.character = character;
        }
        g.version = 2;

        // When API provides full deck, replace owned counts from deck (authoritative).
        if !state.deck.is_empty() {
            g.owned.clear();
            for c in &state.deck {
                upsert_owned(&mut g.owned, c, 1);
            }
        } else if let Some(combat) = &state.combat_state {
            // Combat without top-level deck: merge piles as owned evidence (max count).
            let mut piles: Vec<&Card> = Vec::new();
            piles.extend(combat.hand.iter());
            piles.extend(combat.draw_pile.iter());
            piles.extend(combat.discard_pile.iter());
            piles.extend(combat.exhaust_pile.iter());
            // Count per key in this combat snapshot
            let mut batch: HashMap<String, (Card, u32)> = HashMap::new();
            for c in piles {
                if c.id.is_empty() && c.name.is_empty() {
                    continue;
                }
                let key = cache_key(c);
                let e = batch.entry(key).or_insert_with(|| (c.clone(), 0));
                e.1 += 1;
                if richness(c) > richness(&e.0) {
                    e.0 = c.clone();
                }
            }
            for (key, (card, count)) in batch {
                let entry = g.owned.entry(key).or_insert_with(|| OwnedEntry {
                    card: card.clone(),
                    count: 0,
                });
                if richness(&card) >= richness(&entry.card) {
                    entry.card = card;
                }
                entry.count = entry.count.max(count);
            }
        }
        // rest upgradeable is a subset of deck — only refresh metadata if already owned
        if let Some(rest) = &state.rest_state {
            for c in &rest.upgradeable_cards {
                let key = cache_key(c);
                if let Some(e) = g.owned.get_mut(&key) {
                    if richness(c) > richness(&e.card) {
                        e.card = c.clone();
                    }
                }
            }
        }

        drop(g);
        self.persist();
    }

    /// Fill empty deck from owned cache (with multiplicity).
    pub fn enrich(&self, state: &mut GameState) {
        let g = self.inner.lock();
        if state.deck.is_empty() && !g.owned.is_empty() {
            let mut deck = expand_owned(&g.owned);
            deck.sort_by(|a, b| a.name.cmp(&b.name));
            log::info!(
                "Enriched empty deck from owned cache ({} cards)",
                deck.len()
            );
            state.deck = deck;
        }
    }

    pub fn degraded_state(
        &self,
        screen_type: crate::game::state::ScreenType,
    ) -> Option<GameState> {
        self.last_snapshot.lock().clone().map(|mut s| {
            s.screen_type = screen_type;
            s.shop_state = None;
            s.combat_state = None;
            s.map_state = None;
            s.event_state = None;
            s.reward_state = None;
            s.rest_state = None;
            s
        })
    }

    pub fn minimal_state(
        &self,
        screen_type: crate::game::state::ScreenType,
    ) -> Option<GameState> {
        let g = self.inner.lock();
        if g.owned.is_empty() && g.character.is_none() {
            return None;
        }
        let mut deck = expand_owned(&g.owned);
        deck.sort_by(|a, b| a.name.cmp(&b.name));
        Some(GameState {
            screen_type,
            seed: g.seed.clone(),
            character: g.character.clone(),
            act: None,
            floor: None,
            ascension_level: None,
            current_hp: None,
            max_hp: None,
            gold: None,
            deck,
            relics: vec![],
            potions: vec![],
            combat_state: None,
            map_state: None,
            shop_state: None,
            event_state: None,
            reward_state: None,
            rest_state: None,
        })
    }

    /// Unique owned card types.
    pub fn unique_count(&self) -> usize {
        self.inner.lock().owned.len()
    }

    /// Total owned cards including multiples.
    pub fn total_count(&self) -> usize {
        self.inner
            .lock()
            .owned
            .values()
            .map(|e| e.count as usize)
            .sum()
    }

    /// Back-compat alias for unique types.
    #[allow(dead_code)]
    pub fn len(&self) -> usize {
        self.unique_count()
    }

    fn persist(&self) {
        {
            let mut lw = self.last_write.lock();
            if lw.elapsed().as_secs() < 5 {
                return;
            }
            *lw = Instant::now();
        }
        let g = self.inner.lock();
        if let Ok(s) = serde_json::to_string_pretty(&*g) {
            if let Some(parent) = self.path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&self.path, s);
        }
    }
}

fn migrate_v1(data: &mut CacheFile) {
    if data.version >= 2 {
        data.cards.clear();
        return;
    }
    if data.owned.is_empty() && !data.cards.is_empty() {
        for (k, c) in data.cards.drain() {
            data.owned.insert(
                k,
                OwnedEntry {
                    card: c,
                    count: 1,
                },
            );
        }
        log::info!("Migrated run cache v1 → v2 ({} unique, count=1 each)", data.owned.len());
    }
    data.version = 2;
    data.cards.clear();
}

fn upsert_owned(map: &mut HashMap<String, OwnedEntry>, c: &Card, add: u32) {
    if c.id.is_empty() && c.name.is_empty() {
        return;
    }
    let key = cache_key(c);
    let entry = map.entry(key).or_insert_with(|| OwnedEntry {
        card: c.clone(),
        count: 0,
    });
    if richness(c) >= richness(&entry.card) {
        entry.card = c.clone();
    }
    entry.count = entry.count.saturating_add(add);
}

fn expand_owned(owned: &HashMap<String, OwnedEntry>) -> Vec<Card> {
    let mut deck = Vec::new();
    for e in owned.values() {
        let n = e.count.max(1);
        for _ in 0..n {
            deck.push(e.card.clone());
        }
    }
    deck
}

fn cache_key(c: &Card) -> String {
    if !c.id.is_empty() && c.id != "unknown" {
        c.id.to_ascii_uppercase()
    } else {
        c.name.to_ascii_lowercase()
    }
}

fn richness(c: &Card) -> i32 {
    let mut s = 0;
    if c.description.as_ref().map(|d| !d.is_empty()).unwrap_or(false) {
        s += 3;
    }
    if c.damage.is_some() {
        s += 1;
    }
    if c.block.is_some() {
        s += 1;
    }
    if c.rarity.is_some() {
        s += 1;
    }
    if !c.card_type.is_empty() {
        s += 1;
    }
    s
}

fn default_cache_path() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.join("Cargo.toml").exists() {
        return cwd.join("run_card_cache.json");
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            return dir.join("run_card_cache.json");
        }
    }
    cwd.join("run_card_cache.json")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game::state::{Card, GameState, RewardState, ScreenType, ShopItem, ShopState};

    fn card(id: &str, name: &str) -> Card {
        Card {
            id: id.into(),
            name: name.into(),
            cost: 1,
            upgraded: false,
            card_type: "Attack".into(),
            description: Some("d".into()),
            damage: Some(6),
            block: None,
            magic_number: None,
            rarity: Some("common".into()),
        }
    }

    fn base_state() -> GameState {
        GameState {
            screen_type: ScreenType::Map,
            seed: Some("s1".into()),
            character: Some("Ironclad".into()),
            act: Some(1),
            floor: Some(3),
            ascension_level: None,
            current_hp: Some(70),
            max_hp: Some(80),
            gold: Some(100),
            deck: vec![],
            relics: vec![],
            potions: vec![],
            combat_state: None,
            map_state: None,
            shop_state: None,
            event_state: None,
            reward_state: None,
            rest_state: None,
        }
    }

    #[test]
    fn reward_offers_not_owned() {
        let cache = RunCache {
            inner: Arc::new(Mutex::new(CacheFile {
                version: 2,
                ..Default::default()
            })),
            path: PathBuf::from("nul"),
            last_write: Arc::new(Mutex::new(Instant::now())),
            last_snapshot: Arc::new(Mutex::new(None)),
        };
        let mut st = base_state();
        st.deck = vec![card("STRIKE", "Strike"), card("STRIKE", "Strike")];
        cache.observe(&st);
        assert_eq!(cache.unique_count(), 1);
        assert_eq!(cache.total_count(), 2);

        st.deck.clear();
        st.reward_state = Some(RewardState {
            gold: None,
            cards: vec![card("BASH", "Bash")],
            can_skip: true,
            items: vec![],
        });
        // No deck → observe does not add reward cards
        cache.observe(&st);
        assert_eq!(cache.unique_count(), 1);
        assert!(!cache
            .inner
            .lock()
            .owned
            .contains_key("BASH"));
    }

    #[test]
    fn shop_shelf_not_owned() {
        let cache = RunCache {
            inner: Arc::new(Mutex::new(CacheFile {
                version: 2,
                ..Default::default()
            })),
            path: PathBuf::from("nul"),
            last_write: Arc::new(Mutex::new(Instant::now())),
            last_snapshot: Arc::new(Mutex::new(None)),
        };
        let mut st = base_state();
        st.deck = vec![card("DEFEND", "Defend")];
        cache.observe(&st);
        st.shop_state = Some(ShopState {
            cards: vec![ShopItem {
                item: card("RARITY", "Demon Form"),
                price: 150,
            }],
            relics: vec![],
            potions: vec![],
            removal_cost: Some(75),
        });
        // deck still present → replace from deck only
        cache.observe(&st);
        assert_eq!(cache.unique_count(), 1);
        assert!(cache.inner.lock().owned.contains_key("DEFEND"));
    }

    #[test]
    fn enrich_expands_counts() {
        let cache = RunCache {
            inner: Arc::new(Mutex::new(CacheFile {
                version: 2,
                ..Default::default()
            })),
            path: PathBuf::from("nul"),
            last_write: Arc::new(Mutex::new(Instant::now())),
            last_snapshot: Arc::new(Mutex::new(None)),
        };
        let mut st = base_state();
        st.deck = vec![
            card("STRIKE", "Strike"),
            card("STRIKE", "Strike"),
            card("DEFEND", "Defend"),
        ];
        cache.observe(&st);
        let mut empty = base_state();
        empty.deck.clear();
        cache.enrich(&mut empty);
        assert_eq!(empty.deck.len(), 3);
    }
}
