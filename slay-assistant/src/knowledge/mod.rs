//! Character archetype knowledge (meta builds, pick/keep/play tips).

pub mod archetypes;

pub use archetypes::{
    detect_archetypes, display_name, score_card_for_archetypes, tips_for_state,
    translate_card_name, translate_text,
};
