use serde::{Deserialize, Serialize};

/// Top-level game state returned by the STS2 API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GameState {
    /// Which screen the player is currently on
    pub screen_type: ScreenType,

    /// Run seed
    #[serde(default)]
    pub seed: Option<String>,

    /// Character class
    #[serde(default)]
    pub character: Option<String>,

    /// Current act (1-4)
    #[serde(default)]
    pub act: Option<u8>,

    /// Current floor within the act
    #[serde(default)]
    pub floor: Option<u8>,

    /// Ascension level
    #[serde(default)]
    pub ascension_level: Option<u8>,

    /// Current HP / Max HP
    #[serde(default)]
    pub current_hp: Option<i32>,
    #[serde(default)]
    pub max_hp: Option<i32>,

    /// Gold
    #[serde(default)]
    pub gold: Option<i32>,

    /// Current deck
    #[serde(default)]
    pub deck: Vec<Card>,

    /// Relics
    #[serde(default)]
    pub relics: Vec<Relic>,

    /// Potions
    #[serde(default)]
    pub potions: Vec<Potion>,

    // Screen-specific state (only one is populated based on screen_type)
    #[serde(default)]
    pub combat_state: Option<CombatState>,
    #[serde(default)]
    pub map_state: Option<MapState>,
    #[serde(default)]
    pub shop_state: Option<ShopState>,
    #[serde(default)]
    pub event_state: Option<EventState>,
    #[serde(default)]
    pub reward_state: Option<RewardState>,
    #[serde(default)]
    pub rest_state: Option<RestState>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ScreenType {
    #[serde(alias = "MAP")]
    Map,
    #[serde(alias = "COMBAT")]
    Combat,
    #[serde(alias = "SHOP")]
    Shop,
    #[serde(alias = "EVENT")]
    Event,
    #[serde(alias = "REWARD")]
    Reward,
    #[serde(alias = "REST")]
    Rest,
    #[serde(alias = "BOSS_REWARD")]
    BossReward,
    #[serde(alias = "GAME_OVER")]
    GameOver,
    #[serde(alias = "NONE")]
    None,
    #[serde(other)]
    Unknown,
}

// ── Card ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Card {
    pub id: String,
    pub name: String,

    #[serde(default)]
    pub card_type: String, // Attack, Skill, Power, Status, Curse

    #[serde(default)]
    pub cost: i32,

    #[serde(default)]
    pub upgraded: bool,

    /// Current damage value (may be modified by strength, etc.)
    #[serde(default)]
    pub damage: Option<i32>,

    /// Current block value
    #[serde(default)]
    pub block: Option<i32>,

    /// Magic number (varies by card)
    #[serde(default)]
    pub magic_number: Option<i32>,

    /// Card description text
    #[serde(default)]
    pub description: Option<String>,

    #[serde(default)]
    pub rarity: Option<String>,
}

// ── Relic ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Relic {
    pub id: String,
    pub name: String,

    #[serde(default)]
    pub description: Option<String>,

    /// Counter value (e.g., Happy Flower, Pen Nib)
    #[serde(default)]
    pub counter: Option<i32>,
}

// ── Potion ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Potion {
    pub id: String,
    pub name: String,

    #[serde(default)]
    pub description: Option<String>,
}

// ── Combat ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct CombatState {
    pub turn: u32,

    /// Cards currently in hand
    #[serde(default)]
    pub hand: Vec<Card>,

    /// Cards in draw pile
    #[serde(default)]
    pub draw_pile: Vec<Card>,

    /// Cards in discard pile
    #[serde(default)]
    pub discard_pile: Vec<Card>,

    /// Cards exhausted this combat
    #[serde(default)]
    pub exhaust_pile: Vec<Card>,

    /// Current energy
    #[serde(default)]
    pub energy: i32,

    /// Max energy this turn
    #[serde(default)]
    pub max_energy: i32,

    /// Player's block this turn
    #[serde(default)]
    pub block: i32,

    /// Active powers on the player
    #[serde(default)]
    pub powers: Vec<Power>,

    /// Enemies in the current fight
    #[serde(default)]
    pub enemies: Vec<Enemy>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Power {
    pub id: String,
    pub name: String,

    #[serde(default)]
    pub amount: i32,

    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Enemy {
    pub id: String,
    pub name: String,

    #[serde(default)]
    pub current_hp: Option<i32>,

    #[serde(default)]
    pub max_hp: Option<i32>,

    #[serde(default)]
    pub block: i32,

    /// The enemy's intent for this turn
    #[serde(default)]
    pub intent: Option<EnemyIntent>,

    /// Active powers on this enemy
    #[serde(default)]
    pub powers: Vec<Power>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct EnemyIntent {
    /// Type: Attack, Defend, Buff, Debuff, etc.
    #[serde(default)]
    pub intent_type: Option<String>,

    /// Damage value (if attacking)
    #[serde(default)]
    pub damage: Option<i32>,

    /// Number of hits
    #[serde(default)]
    pub hits: Option<u32>,

    /// Block to gain (if defending)
    #[serde(default)]
    pub block: Option<i32>,
}

// ── Map ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub struct MapState {
    /// Full act graph. Node id format: `"col,row"`.
    #[serde(default)]
    pub nodes: Vec<MapNode>,

    /// Current position id (`"col,row"`)
    #[serde(default)]
    pub current_node_id: Option<String>,

    /// Immediate choices from current position (subset of nodes / next hops)
    #[serde(default)]
    pub next_options: Vec<MapNextOption>,

    /// Boss node id if known
    #[serde(default)]
    pub boss_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MapNextOption {
    /// Game API index (0 = leftmost typically)
    pub index: i32,
    /// Node id `"col,row"`
    pub node_id: String,
    pub symbol: String,
    #[serde(default)]
    pub col: i32,
    #[serde(default)]
    pub row: i32,
    /// One-hop leads (ids)
    #[serde(default)]
    pub leads_to: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MapNode {
    /// `"col,row"`
    pub id: String,

    /// Monster / Elite / RestSite / Shop / Treasure / Unknown / Boss …
    pub symbol: String,

    /// col
    #[serde(default)]
    pub x: Option<f32>,
    /// row
    #[serde(default)]
    pub y: Option<f32>,

    /// Child node ids (`"col,row"`)
    #[serde(default)]
    pub children: Vec<String>,

    #[serde(default)]
    pub parents: Vec<String>,
}

// ── Shop ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ShopState {
    /// Cards for sale
    #[serde(default)]
    pub cards: Vec<ShopItem<Card>>,

    /// Relics for sale
    #[serde(default)]
    pub relics: Vec<ShopItem<Relic>>,

    /// Potions for sale
    #[serde(default)]
    pub potions: Vec<ShopItem<Potion>>,

    /// Cost to remove a card
    #[serde(default)]
    pub removal_cost: Option<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ShopItem<T> {
    #[serde(flatten)]
    pub item: T,
    pub price: i32,
}

// ── Event ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct EventState {
    /// Event name or description
    #[serde(default)]
    pub event_name: Option<String>,

    /// Event text
    #[serde(default)]
    pub text: Option<String>,

    /// Available choices
    #[serde(default)]
    pub choices: Vec<EventChoice>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct EventChoice {
    pub id: String,

    /// Choice label/description
    pub text: String,

    /// Whether this choice is available (e.g., requires gold, specific relic, etc.)
    #[serde(default = "default_true")]
    pub available: bool,

    /// Required cost (gold, HP, etc.)
    #[serde(default)]
    pub cost: Option<String>,
}

fn default_true() -> bool {
    true
}

// ── Reward ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct RewardState {
    /// Card choices (pick 1) — when the card-pick UI is open
    #[serde(default)]
    pub cards: Vec<Card>,

    /// Gold reward amount (from gold item)
    #[serde(default)]
    pub gold: Option<i32>,

    /// Whether we can skip / proceed
    #[serde(default = "default_true")]
    pub can_skip: bool,

    /// STS2 MCP reward screen items (gold / card / potion / relic …)
    #[serde(default)]
    pub items: Vec<RewardItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct RewardItem {
    pub index: i32,
    /// gold | card | potion | relic | …
    #[serde(rename = "type")]
    pub item_type: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub gold_amount: Option<i32>,
    /// When card-pick is expanded, nested choices may live here
    #[serde(default)]
    pub cards: Vec<Card>,
}

// ── Rest ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct RestState {
    /// Options: "rest" (heal 30%), "smith" (upgrade a card), etc.
    #[serde(default)]
    pub options: Vec<RestOption>,

    /// Cards available to upgrade
    #[serde(default)]
    pub upgradeable_cards: Vec<Card>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct RestOption {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
}

impl GameState {
    /// Quick summary for display/analysis
    pub fn summary(&self) -> String {
        format!(
            "Screen: {:?} | HP: {}/{} | Gold: {} | Deck: {} cards | Relics: {}",
            self.screen_type,
            self.current_hp.unwrap_or(0),
            self.max_hp.unwrap_or(0),
            self.gold.unwrap_or(0),
            self.deck.len(),
            self.relics.len()
        )
    }
}
