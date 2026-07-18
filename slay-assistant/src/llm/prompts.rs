/// System prompt prefix for STS2 analysis.
pub const SYSTEM_PREFIX: &str = r#"你是一位《Slay the Spire 2》专家级玩家和策略分析师。
你的任务是根据当前游戏状态，提供最优决策建议。

## 输出格式
请严格按照以下格式输出（不要输出其他内容）：

1. **推荐操作** (优先级最高)
   - 理由（一句话）

2. **备选方案** (优先级次之)
   - 理由（一句话）

3. **不推荐** (应该避免)
   - 理由（一句话）

## 分析原则
- 优先考虑当前生命值安全（低于30%时保守）vs 输出最大化（血线安全时激进）
- 考虑牌组协同效应：当前牌组的主要获胜策略是什么？选择卡牌时优先补强该策略
- 评估圣物协同：已有圣物能否放大某些卡牌的效果
- 地图路线：精英战风险高但收益大，篝火可升级关键卡牌，商店可购买核心圣物
- 能量效率：每点能量能造成多少伤害/格挡
"#;

/// Combat analysis prompt template.
pub fn combat_prompt(state_json: &str) -> String {
    format!(
        r#"当前在战斗中。以下是游戏状态：

{state_json}

请分析手牌，给出本回合最优出牌顺序。
- 敌人意图：是否在攻击？伤害多少？需要多少格挡？
- 斩杀线：当前手牌是否能击杀敌人？
- 是否使用药水？
- 出牌顺序是否重要（如：先加力量再攻击）？
"#
    )
}

/// Map pathing prompt template.
pub fn map_prompt(state_json: &str) -> String {
    format!(
        r#"当前在地图选择路线。以下是游戏状态：

{state_json}

请用中文给出可执行的选路建议（必须点明 next_options 的 index 编号）：
1. **推荐操作**：选 index=? 的节点；本层类型；为什么；下一段通向什么
2. **备选方案**：另一个 index
3. **不推荐**：最差 index 及原因
原则：血量<45%优先篝火/保命；血量健康可冲精英；金币充足优先通向商店；前期可多打普通战拿牌。
"#
    )
}

/// Card reward prompt template.
pub fn reward_prompt(state_json: &str) -> String {
    format!(
        r#"当前在选择卡牌奖励（三选一或跳过）。以下是游戏状态：

{state_json}

请用中文给出可执行的选牌建议：
1. **推荐操作**：选第几张（从左到右 1/2/3）或 Skip；卡名；一句话理由
2. **备选方案**
3. **不推荐**：最弱那张或「不要 skip」
原则：稀有/关键能力/易伤虚弱/力量/AOE/0费优先；基础打击防御低优先；牌组已很厚时更敢 Skip；结合已有遗物协同。
"#
    )
}

/// Shop prompt template.
pub fn shop_prompt(state_json: &str) -> String {
    format!(
        r#"当前在商店。以下是游戏状态：

{state_json}

请分析商店物品，给出购买/移除建议。
- 哪些物品性价比最高？
- 是否需要移除基础卡牌（打击/防御）？
- 药水是否值得买？
- 考虑保留多少金币用于后续楼层
"#
    )
}

/// Event choice prompt template.
pub fn event_prompt(state_json: &str) -> String {
    format!(
        r#"当前在事件中。以下是游戏状态：

{state_json}

请分析事件选项，给出建议。
- 每个选项的风险和收益是什么？
- 当前生命值/金币能否承受损失？
- 是否值得赌高风险选项？
"#
    )
}
