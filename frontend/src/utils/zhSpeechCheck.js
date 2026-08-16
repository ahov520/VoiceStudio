/**
 * Chinese speech pre-flight check (zh-UX pass 2).
 *
 * Scans script text for two things Chinese TTS engines commonly get wrong:
 *
 *  1. Polyphones (多音字) — characters with several readings, e.g. 行 (xíng /
 *     háng) in 行走 vs 银行. Engines guess from context and sometimes guess
 *     wrong; the fix is a pronunciation-dictionary entry (whole-word respell
 *     via a homophone char, `services/pronunciation.apply_lexicon`), which
 *     applies to every synthesis. The table below maps each reading to a
 *     COMMON homophone char (engines read those reliably) plus the frequent
 *     collocations that disambiguate it — a hit inside one of those words
 *     auto-resolves the reading; anything else is surfaced for the user.
 *
 *  2. Number reading — internet-style units ("10w", "3.5k") that engines
 *     read letter-by-letter, and 7+ digit runs (phones/IDs) read as one huge
 *     cardinal. Both get a one-click text rewrite (万/千, digit grouping).
 *
 * Pure functions, no imports — trivially testable, UI-agnostic.
 */

// char -> readings: { pinyin, respelling (homophone char), words: frequent
// collocations containing the char with THIS reading }.
// Curated for narration/dubbing frequency; homophones are common chars so the
// engine itself never stumbles on the replacement.
export const POLYPHONES = {
  长: [
    {
      pinyin: 'cháng',
      respelling: '常',
      words: ['长久', '漫长', '长途', '长城', '悠长', '特长', '很长', '较长', '长度', '长空'],
    },
    {
      pinyin: 'zhǎng',
      respelling: '掌',
      words: [
        '长大',
        '成长',
        '增长',
        '长辈',
        '校长',
        '市长',
        '家长',
        '首长',
        '班长',
        '部长',
        '长老',
      ],
    },
  ],
  行: [
    {
      pinyin: 'xíng',
      respelling: '形',
      words: ['行走', '行进', '行动', '进行', '行驶', '行人', '旅行', '执行', '流行', '行为'],
    },
    {
      pinyin: 'háng',
      respelling: '航',
      words: ['银行', '行业', '行家', '内行', '外行', '行列', '同行'],
    },
  ],
  重: [
    {
      pinyin: 'chóng',
      respelling: '崇',
      words: ['重庆', '重复', '重游', '重逢', '重修', '重建', '重申', '双喜临门'],
    },
    {
      pinyin: 'zhòng',
      respelling: '众',
      words: ['重要', '重量', '重大', '严重', '沉重', '尊重', '重视', '重任', '重伤'],
    },
  ],
  都: [
    { pinyin: 'dōu', respelling: '兜', words: ['都是', '都有', '都行', '全都', '都好'] },
    { pinyin: 'dū', respelling: '督', words: ['首都', '都市', '大都', '建都'] },
  ],
  还: [
    { pinyin: 'hái', respelling: '孩', words: ['还有', '还是', '还没', '还要', '还好', '还在'] },
    { pinyin: 'huán', respelling: '环', words: ['归还', '还原', '还击', '偿还', '返还'] },
  ],
  得: [
    {
      pinyin: 'dé',
      respelling: '德',
      words: ['得到', '取得', '获得', '值得', '得体', '心得', '得不偿失'],
    },
    { pinyin: 'de', respelling: '的', words: ['走得快', '说得好', '跑得动'] },
  ],
  地: [
    {
      pinyin: 'dì',
      respelling: '第',
      words: ['地方', '土地', '地球', '地址', '地区', '天地', '阵地'],
    },
    { pinyin: 'de', respelling: '的', words: ['慢慢地', '轻轻地', '突然地', '认真地'] },
  ],
  数: [
    {
      pinyin: 'shù',
      respelling: '树',
      words: ['数字', '数量', '数据', '大多数', '少数', '无数', '岁数'],
    },
    { pinyin: 'shǔ', respelling: '鼠', words: ['数落', '数一数二', '数不清', '数着'] },
  ],
  种: [
    {
      pinyin: 'zhǒng',
      respelling: '肿',
      words: ['种类', '各种', '种子', '品种', '某种', '多种', '人种'],
    },
    { pinyin: 'zhòng', respelling: '众', words: ['种植', '种地', '种田', '播种'] },
  ],
  传: [
    {
      pinyin: 'chuán',
      respelling: '船',
      words: ['传说', '传统', '传播', '流传', '传授', '相传', '传递'],
    },
    { pinyin: 'zhuàn', respelling: '赚', words: ['传记', '自传', '列传', '水浒传'] },
  ],
  背: [
    { pinyin: 'bèi', respelling: '被', words: ['背景', '背后', '背诵', '背部', '背面', '违背'] },
    { pinyin: 'bēi', respelling: '杯', words: ['背负', '背起', '背带', '背包', '背着'] },
  ],
  朝: [
    { pinyin: 'cháo', respelling: '潮', words: ['朝向', '朝着', '王朝', '朝代', '朝廷'] },
    { pinyin: 'zhāo', respelling: '招', words: ['朝阳', '朝气', '朝夕', '朝不保夕'] },
  ],
  曾: [
    { pinyin: 'céng', respelling: '层', words: ['曾经', '未曾', '何曾'] },
    { pinyin: 'zēng', respelling: '增', words: ['曾孙', '曾祖'] },
  ],
  落: [
    {
      pinyin: 'luò',
      respelling: '洛',
      words: ['落下', '落地', '降落', '落后', '落叶', '角落', '落日'],
    },
    { pinyin: 'lào', respelling: '烙', words: ['落色', '落枕', '落不是'] },
    { pinyin: 'là', respelling: '蜡', words: ['落下', '丢三落四', '落在'] },
  ],
  系: [
    {
      pinyin: 'xì',
      respelling: '细',
      words: ['系统', '关系', '系列', '联系', '系上', '体系', '院系'],
    },
    { pinyin: 'jì', respelling: '记', words: ['系鞋带', '系绳', '系住'] },
  ],
  率: [
    { pinyin: 'lǜ', respelling: '绿', words: ['效率', '概率', '利率', '频率', '税率'] },
    { pinyin: 'shuài', respelling: '帅', words: ['率领', '率先', '直率', '轻率', '草率'] },
  ],
  处: [
    { pinyin: 'chù', respelling: '触', words: ['到处', '好处', '处处', '办事处', '近处', '深处'] },
    {
      pinyin: 'chǔ',
      respelling: '楚',
      words: ['处理', '相处', '处境', '处置', '处决', '设身处地'],
    },
  ],
  调: [
    { pinyin: 'tiáo', respelling: '条', words: ['调整', '调节', '调皮', '协调', '调教'] },
    { pinyin: 'diào', respelling: '吊', words: ['调查', '调动', '调换', '声调', '曲调', '格调'] },
  ],
  弹: [
    { pinyin: 'tán', respelling: '谈', words: ['弹琴', '弹奏', '弹跳', '弹性', '反弹'] },
    { pinyin: 'dàn', respelling: '淡', words: ['子弹', '弹药', '炮弹', '导弹', '弹片'] },
  ],
  答: [
    { pinyin: 'dá', respelling: '达', words: ['回答', '答案', '答复', '解答', '答应'] },
    { pinyin: 'dā', respelling: '搭', words: ['答应', '答理', '答腔'] },
  ],
  教: [
    { pinyin: 'jiào', respelling: '叫', words: ['教育', '教师', '教室', '教材', '教训', '请教'] },
    { pinyin: 'jiāo', respelling: '交', words: ['教书', '教课', '教给', '教我'] },
  ],
  结: [
    {
      pinyin: 'jié',
      respelling: '洁',
      words: ['结果', '结束', '结合', '总结', '结婚', '结构', '团结'],
    },
    { pinyin: 'jiē', respelling: '街', words: ['结实', '结巴'] },
  ],
  劲: [
    { pinyin: 'jìn', respelling: '近', words: ['使劲', '用力', '劲头', '费劲', '松劲', '闯劲'] },
    { pinyin: 'jìng', respelling: '净', words: ['劲敌', '劲旅', '强劲', '刚劲'] },
  ],
  觉: [
    { pinyin: 'jué', respelling: '绝', words: ['觉得', '觉悟', '感觉', '自觉', '直觉'] },
    { pinyin: 'jiào', respelling: '叫', words: ['睡觉', '午觉', '一觉'] },
  ],
  露: [
    { pinyin: 'lù', respelling: '路', words: ['露水', '暴露', '流露', '披露', '露面', '揭露'] },
    { pinyin: 'lòu', respelling: '漏', words: ['露马脚', '露馅', '露脸'] },
  ],
  强: [
    { pinyin: 'qiáng', respelling: '墙', words: ['强大', '强烈', '坚强', '增强', '强盗', '强悍'] },
    { pinyin: 'qiǎng', respelling: '抢', words: ['勉强', '强迫', '强求', '强颜'] },
    { pinyin: 'jiàng', respelling: '匠', words: ['倔强'] },
  ],
  曲: [
    { pinyin: 'qū', respelling: '驱', words: ['弯曲', '曲折', '曲线', '曲径'] },
    { pinyin: 'qǔ', respelling: '取', words: ['歌曲', '曲子', '乐曲', '戏曲', '作曲', '曲目'] },
  ],
  扇: [
    { pinyin: 'shàn', respelling: '善', words: ['扇子', '电扇', '一扇门', '扇形'] },
    { pinyin: 'shān', respelling: '山', words: ['扇风', '扇动'] },
  ],
  似: [
    { pinyin: 'sì', respelling: '四', words: ['相似', '类似', '似乎', '近似', '酷似'] },
    { pinyin: 'shì', respelling: '事', words: ['似的'] },
  ],
  宿: [
    { pinyin: 'sù', respelling: '素', words: ['宿舍', '住宿', '宿命', '归宿'] },
    { pinyin: 'xiù', respelling: '秀', words: ['星宿', '二十八宿'] },
  ],
  提: [
    { pinyin: 'tí', respelling: '题', words: ['提供', '提出', '提醒', '提高', '提前', '提议'] },
    { pinyin: 'dī', respelling: '低', words: ['提防'] },
  ],
  吐: [
    { pinyin: 'tǔ', respelling: '土', words: ['吐出', '吐露', '吐字', '谈吐'] },
    { pinyin: 'tù', respelling: '兔', words: ['呕吐', '吐血'] },
  ],
  兴: [
    { pinyin: 'xīng', respelling: '星', words: ['兴奋', '兴起', '兴建', '振兴', '兴隆'] },
    { pinyin: 'xìng', respelling: '姓', words: ['高兴', '兴趣', '兴致', '兴高采烈'] },
  ],
  相: [
    { pinyin: 'xiāng', respelling: '箱', words: ['相信', '相同', '相互', '相当', '相似', '相处'] },
    { pinyin: 'xiàng', respelling: '向', words: ['相片', '相机', '相貌', '丞相', '真相大白'] },
  ],
  压: [
    { pinyin: 'yā', respelling: '鸭', words: ['压力', '压迫', '压制', '压低', '气压'] },
    { pinyin: 'yà', respelling: '亚', words: ['压根'] },
  ],
  咽: [
    { pinyin: 'yān', respelling: '烟', words: ['咽喉', '咽炎'] },
    { pinyin: 'yàn', respelling: '宴', words: ['咽下', '吞咽', '细嚼慢咽', '狼吞虎咽'] },
    { pinyin: 'yè', respelling: '页', words: ['呜咽', '哽咽'] },
  ],
  要: [
    { pinyin: 'yào', respelling: '药', words: ['需要', '重要', '想要', '只要', '要么'] },
    { pinyin: 'yāo', respelling: '腰', words: ['要求'] },
  ],
  应: [
    { pinyin: 'yīng', respelling: '鹰', words: ['应该', '应当', '应有'] },
    { pinyin: 'yìng', respelling: '硬', words: ['答应', '反应', '应用', '应付', '回应', '应声'] },
  ],
  载: [
    { pinyin: 'zǎi', respelling: '宰', words: ['记载', '刊载', '转载', '一年半载'] },
    { pinyin: 'zài', respelling: '在', words: ['载重', '承载', '装载', '载客', '超载'] },
  ],
  折: [
    { pinyin: 'zhé', respelling: '哲', words: ['折磨', '转折', '折服', '折扣', '曲折'] },
    { pinyin: 'shé', respelling: '蛇', words: ['折本', '绳子折了'] },
    { pinyin: 'zhē', respelling: '遮', words: ['折腾'] },
  ],
  挣: [
    { pinyin: 'zhèng', respelling: '正', words: ['挣钱', '挣脱'] },
    { pinyin: 'zhēng', respelling: '争', words: ['挣扎', '挣开'] },
  ],
  中: [
    { pinyin: 'zhōng', respelling: '钟', words: ['中国', '中间', '中心', '中午', '其中', '中介'] },
    { pinyin: 'zhòng', respelling: '众', words: ['中意', '中奖', '看中', '中肯', '中暑'] },
  ],
  参: [
    { pinyin: 'cān', respelling: '餐', words: ['参加', '参考', '参与', '参观', '参见'] },
    { pinyin: 'shēn', respelling: '申', words: ['人参', '海参', '党参'] },
  ],
  差: [
    { pinyin: 'chà', respelling: '岔', words: ['差不多', '差劲', '相差', '差错', '差距'] },
    { pinyin: 'chā', respelling: '插', words: ['差别', '差异', '反差', '落差', '温差'] },
    { pinyin: 'chāi', respelling: '拆', words: ['出差', '差遣', '差事', '差旅'] },
  ],
  好: [
    { pinyin: 'hǎo', respelling: '郝', words: ['好的', '好看', '好人', '好处', '正好', '只好'] },
    { pinyin: 'hào', respelling: '号', words: ['好恶', '好奇', '爱好', '喜好'] },
  ],
  铺: [
    { pinyin: 'pū', respelling: '扑', words: ['铺开', '铺路', '铺满', '铺设', '平铺'] },
    { pinyin: 'pù', respelling: '瀑', words: ['店铺', '铺子', '当铺', '床铺'] },
  ],
  干: [
    { pinyin: 'gān', respelling: '甘', words: ['干净', '干燥', '干脆', '干预'] },
    { pinyin: 'gàn', respelling: '赣', words: ['干部', '干事', '骨干', '主干', '干活'] },
  ],
};

const CJK_CHAR = /[\u3400-\u9fff]/;

/** Contiguous CJK runs of `text` as [{text, start}] (words live inside runs). */
function cjkRuns(text) {
  const runs = [];
  let i = 0;
  while (i < text.length) {
    if (CJK_CHAR.test(text[i])) {
      const start = i;
      while (i < text.length && CJK_CHAR.test(text[i])) i++;
      runs.push({ text: text.slice(start, i), start });
    } else {
      i++;
    }
  }
  return runs;
}

/**
 * Scan for polyphone occurrences.
 *
 * Returns rows (one per distinct word): {
 *   char, word,            // word containing the polyphone char
 *   options: [{pinyin, respelling}],
 *   detected: pinyin|null, // auto-resolved from the collocation table
 *   count: occurrences,
 * }
 * A word only lands here if it contains a table polyphone char; when no
 * collocation matches, `detected` is null and the user picks a reading.
 */
export function scanPolyphones(text) {
  if (!text || !CJK_CHAR.test(text)) return [];
  const rows = new Map();
  for (const run of cjkRuns(text)) {
    for (const [char, readings] of Object.entries(POLYPHONES)) {
      let idx = run.text.indexOf(char);
      while (idx !== -1) {
        // Which readings' collocations cover this occurrence? Exactly one
        // match resolves it; zero or several leave it to the user.
        const matches = new Map(); // pinyin -> matched collocation word
        for (const r of readings) {
          for (const w of r.words) {
            let at = run.text.indexOf(w);
            while (at !== -1) {
              if (idx >= at && idx < at + w.length) {
                matches.set(r.pinyin, w);
                break;
              }
              at = run.text.indexOf(w, at + 1);
            }
            if (matches.size) break;
          }
        }
        const detected = matches.size === 1 ? [...matches.keys()][0] : null;
        // Dictionary term: the matched collocation when the reading
        // auto-resolved (tight, reusable); otherwise a ±2-char context window
        // the user confirms a reading against.
        const lo = Math.max(0, idx - 2);
        const hi = Math.min(run.text.length, idx + 3);
        const word = detected ? matches.get(detected) : run.text.slice(lo, hi);
        const key = `${char}|${word}|${detected ?? '?'}`;
        const row = rows.get(key) || { char, word, count: 0, detected, options: readings };
        row.count++;
        rows.set(key, row);
        idx = run.text.indexOf(char, idx + 1);
      }
    }
  }
  return [...rows.values()];
}

/**
 * Suggest number rewrites (kind: 'unit' | 'digits').
 *  - "10w" / "3.5k" → "10万" / "3.5千" (engines read letters aloud otherwise)
 *  - 7+ digit runs → space-grouped (11 digits phone-style 3-4-4) so the
 *    engine reads digit-by-digit instead of one huge cardinal
 */
export function suggestNumberFixes(text) {
  if (!text) return [];
  const fixes = [];
  for (const m of text.matchAll(/(\d+(?:\.\d+)?)([wWkK])(?![A-Za-z0-9])/g)) {
    const unit = m[2].toLowerCase() === 'w' ? '万' : '千';
    fixes.push({
      kind: 'unit',
      raw: m[0],
      fixed: `${m[1]}${unit}`,
      index: m.index,
      length: m[0].length,
    });
  }
  for (const m of text.matchAll(/\d{7,}/g)) {
    const digits = m[0];
    let grouped;
    if (digits.length === 11) {
      grouped = `${digits.slice(0, 3)} ${digits.slice(3, 7)} ${digits.slice(7)}`;
    } else {
      grouped = digits.replace(/(\d{4})(?=\d)/g, '$1 ');
    }
    fixes.push({
      kind: 'digits',
      raw: digits,
      fixed: grouped,
      index: m.index,
      length: digits.length,
    });
  }
  return fixes;
}

/** Replace the exact [index, index+length) span — used to apply a number fix. */
export function applyNumberFix(text, fix) {
  return text.slice(0, fix.index) + fix.fixed + text.slice(fix.index + fix.length);
}

/** Build the dictionary replacement for a word + chosen reading (homophone). */
export function respellWord(word, char, reading) {
  return word.split(char).join(reading.respelling);
}

/** True when the text has any CJK — gates the whole UI affordance. */
export function hasChinese(text) {
  return !!text && CJK_CHAR.test(text);
}
