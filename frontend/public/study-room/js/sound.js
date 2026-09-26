// ==================== 音频引擎（多层声源 + HRTF 双耳空间化） ====================
//
// 空间化参考专业做法（W3C Web Audio PannerNode / HRTF 双耳渲染）：
// 每个噪声层是独立的单声道声源，经 PannerNode（panningModel:'HRTF'）摆到
// 真实方位（±方位角），浏览器用头相关传递函数渲染成双耳输出——比手工
// 等功率 pan 真实得多；方位可平滑移动（浪花/水滴在声场中漂移）。
//
// 无缝：每层两个不同内容的 10s buffer 交替播放，切换时 300ms 交叉淡化。
// 兼容性：全部用预生成 buffer + 原生节点（PannerNode 无需 secure context、
// 无需外部资源），file:// / http / iframe 都能响。
'use strict';

let audioCtx = null;
let masterGain = null;
let layers = [];          // 当前音色的所有声源层

// 每层独立交叉淡化（错峰 + 随机 jitter）：任何时刻只有一层在过渡，
// 避免"所有层同时切换"造成的周期性落差感
const BUFFER_SECONDS = 20;
const FADE = 0.8;
// 切换间隔必须 < buffer 时长 - FADE（旧声源需留 ≥FADE 余量做淡出），
// 否则旧声源先播完 → 该层完全无声直到切换 → 可闻"陡变断点"
const SWAP_LEAD = 2;      // 额外余量（秒）：切换时旧声源至少还剩 SWAP_LEAD 秒
const SWAP_JITTER = 1;    // 每层 ±1s 随机错峰（秒）

// ── 层配置：每个音色由若干声源层组成，az = 方位角（弧度，0=正前，+右 -左）──
// drift: 声源方位随机漂移（浪花/气泡在声场中缓缓移动）
const LAYER_DEFS = {
    rain: [                      // 暴雨：雨幕 + 近滴答（石面）+ 远噼啪 + 房檐滴水（多层远近/材质）
        { key: 'rainbed', az: -0.7 },
        { key: 'rainbed', az: 0.7 },
        { key: 'dropsNear', drift: true },   // 近处清晰滴答：短促高频，石面/硬面
        { key: 'dropsFar', drift: true },    // 远处噼啪：密集轻柔，中频（空气衰减高频）
        { key: 'dripGutter', az: 0.4 },      // 房檐滴水：慢速、短促中低频"嗒"
    ],
    drizzle: [                   // 细雨：连续沙沙雨幕 + 近清晰滴答 + 远轻柔噼啪（层次）
        { key: 'rainbed', az: -0.7, light: true },
        { key: 'rainbed', az: 0.7, light: true },
        { key: 'dropsNear', drift: true, light: true },   // 近处滴答：清晰可辨
        { key: 'dropsFar', drift: true, light: true },    // 远处噼啪：轻柔密集颗粒底
    ],
    forest: [                    // 森林：以风声为主（左右两层立体风 + 粉噪 + 叶沙点缀）
        { key: 'wind', az: -0.8 },
        { key: 'wind', az: 0.8 },
        { key: 'pink', az: 0 },
        { key: 'leaf', az: 0, drift: true },
    ],
    sea: [                       // 海面：涌动 + 浪花（原"深海"改名）
        { key: 'swell', az: 0 },
        { key: 'foam', drift: true },
    ],
    deep: [                      // 深海（水下）：低频水压底噪 + 气泡咕噜噜
        { key: 'abyss', az: 0 },
        { key: 'bubble', drift: true },                          // 主气泡组：声场中游走
        { key: 'bubble', az: -0.8, quiet: true },                // 小声远层组：固定左远侧，与主组分声道
    ],
};

// 初始化音频上下文
async function initAudio() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
}

// ── 层 buffer 生成（单声道） ──────────────────────────────────────────────

function makeLayerBuffer(ctx, key, def) {
    const rate = ctx.sampleRate;
    const len = Math.floor(rate * BUFFER_SECONDS);
    const buf = ctx.createBuffer(1, len, rate);
    const data = buf.getChannelData(0);

    // 二阶低通系数（-24dB/oct 塑形）
    const lp = (fc) => 1 - Math.exp(-2 * Math.PI * fc / rate);
    const S = (a) => ({ a, s1: 0, s2: 0, lp: function (x) {
        const t = this.s1 + this.a * (x - this.s1);
        this.s2 = this.s2 + this.a * (t - this.s2);
        this.s1 = t;
        return this.s2;
    }});

    // 各层滤波器
    const bedHi = S(lp(3000)), bedLo = S(lp(400));   // 雨幕带通
    const nearHi = S(lp(5200)), nearLo = S(lp(2400)); // 暴雨近滴答（石面/硬面：清脆高频）
    const nearHiL = S(lp(4000)), nearLoL = S(lp(1100)); // 细雨近滴答（宽频：每颗"滴"有实体感）
    const farHi = S(lp(1800)), farLo = S(lp(700));   // 远噼啪（高频被空气衰减：中频）
    const gutLo = S(lp(700));                        // 房檐滴水（中低频闷嗒）
    const windLo = S(lp(800));                        // 风声低通
    const leafLo = S(lp(1500));                       // 叶沙低通
    const swellLo = S(lp(450));                       // 涌动低通
    const foamHi = S(lp(2500)), foamLo = S(lp(1500)); // 浪花带通
    const abyssLo = S(lp(140));                       // 深海低频水压（极低通）

    let brown = 0;
    let k0 = 0, k1 = 0, k2 = 0, k3 = 0, k4 = 0, k5 = 0, k6 = 0;
    // 雨滴脉冲状态：近滴答（稀疏清脆）/ 远噼啪（密集轻柔）/ 房檐滴水（慢速低频）
    let dropNext = 0, dropPhase = 0, dropDur = 0, dropEnv = 0;
    let farNext = 0, farPhase = 0, farDur = 0, farEnv = 0;
    let gutNext = 1, gutPhase = 0, gutDur = 0, gutEnv = 0;
    // 雨幕独立起伏（真实雨声能量波动）
    let rainEnv = 0.8, rainTarget = 0.8, rainNext = 0;
    // 细雨滴答活跃池（专业雨滴声学：短促噪声瞬态、允许偶尔重叠）
    const lightDrops = [];
    // 深海气泡：活跃气泡池（可重叠发声，不用等前一个播完）+ 串触发状态
    // 一串 1~8 个（偏大分布）；串内密集触发（0.08~0.28s）→ 同时 2~4 个气泡叠响
    let bubNext = 1.2, bubRemain = 0;
    const bubbles = [];
    // 幅度包络状态（森林/浪花：随机游走，无固定周期——自然起伏而非规律涨落）
    let env = 0.6, envTarget = 0.6, envNext = 0;
    // 深海涌动 LFO（唯一保留正弦：慢涌 0.035Hz ≈28s 一周期，潮汐感更缓更隐）
    const lfoFreq = key === 'swell' ? 0.035 : 0;

    // 随机游走包络：每 10~22s 才换一次目标幅度（收窄到 0.6~1.0），
    // 逼近更慢（τ≈5.7s）→ 音量几乎恒定，只剩极缓的"呼吸感"，不打扰专注
    const walkEnv = (i) => {
        envNext -= 1 / rate;
        if (envNext <= 0) {
            envTarget = 0.6 + Math.random() * 0.4;
            envNext = 10 + Math.random() * 12;
        }
        env += (envTarget - env) * 0.000004;
        return env;
    };

    for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        brown = brown * 0.995 + w * 0.005;
        k0 = 0.99886 * k0 + w * 0.0555179;
        k1 = 0.99332 * k1 + w * 0.0750759;
        k2 = 0.96900 * k2 + w * 0.1538520;
        k3 = 0.86650 * k3 + w * 0.3104856;
        k4 = 0.55000 * k4 + w * 0.5329522;
        k5 = -0.7616 * k5 - w * 0.0168980;
        const pink = (k0 + k1 + k2 + k3 + k4 + k5 + k6 + w * 0.5362) * 0.11 * 2;
        k6 = w * 0.115926;

        let s = 0;
        if (key === 'rainbed') {
            // 雨幕：极轻的连续暗底（低频沙沙为主），退到背景里——
            // 细雨的主体是"滴答"雨点，雨幕只是让它连续的垫子
            const light = def && def.light;
            // 细雨：带宽大幅调低 + 低频垫也降 → 几乎听不见的底
            const bw = light ? 0.14 : 1.0;
            const loPad = light ? 0.16 : 0.9;
            // 雨幕独立起伏（真实雨声能量波动）
            rainNext -= 1 / rate;
            if (rainNext <= 0) {
                rainTarget = 0.55 + Math.random() * 0.6;
                rainNext = 5 + Math.random() * 5;
            }
            rainEnv += (rainTarget - rainEnv) * 0.00001;
            s = ((bedHi.lp(w) - bedLo.lp(w)) * bw
                + swellLo.lp(brown) * loPad) * rainEnv;
        } else if (key === 'dropsNear') {
            const light = def && def.light;
            if (light) {
                // 细雨滴答（按真实花园细雨录音分析校准，2026-08）：
                // 稀疏 2~5 滴/秒、间隔对数正态(中位~180ms, 112~302ms)、
                // 单滴 12~300ms 指数衰减（中位~70ms，含长尾）、
                // 强度对数正态（远近层次）、活跃池允许偶尔重叠
                dropNext -= 1 / rate;
                if (dropNext <= 0) {
                    // 近处滴答：12~25 滴/秒（清晰可辨，主体）
                    dropNext = 0.04 + Math.random() * 0.045;
                    if (lightDrops.length < 4) {
                        // 单滴时长：25~70ms 饱满的噪声瞬态（足够长到能听见）
                        const dur = 0.025 + Math.random() * 0.045;
                        // 强度：响亮凸出（近处），带远近层次
                        const amp = Math.max(0.3, Math.min(0.65,
                            Math.exp(Math.log(0.42) + (Math.random() * 2 - 1) * 0.45)));
                        lightDrops.push({
                            f: 900 + Math.random() * 2000,       // 0.9~2.9kHz（中频为主）
                            amp,
                            dur,
                            ph: Math.random() * Math.PI * 2,
                            t: 0,
                        });
                    }
                }
                for (let di = lightDrops.length - 1; di >= 0; di--) {
                    const d = lightDrops[di];
                    if (d.t >= d.dur) { lightDrops.splice(di, 1); continue; }
                    const env = Math.exp(-(d.t / d.dur) * 4);    // 指数衰减
                    // 纯宽带噪声瞬态（无正弦/无音高）——真实雨滴撞击声是噪声，
                    // 加了正弦共振就会像敲乐器
                    const burst = nearHiL.lp(w) - nearLoL.lp(w);
                    s = burst * env * d.amp;
                    d.t += 1 / rate;
                }
            } else {
                // 暴雨近滴答（石面/硬面）：短促清脆高频脉冲，8~25 滴/秒
                dropNext -= 1 / rate;
                if (dropNext <= 0) {
                    dropDur = 0.0015 + Math.random() * 0.004;     // 1.5~5.5ms 极短促
                    dropPhase = dropDur;
                    dropEnv = 0.22 + Math.random() * 0.28;        // 响亮（近）
                    dropNext = 0.04 + Math.random() * 0.085;      // 8~25 滴/秒
                }
                if (dropPhase > 0) {
                    s = (nearHi.lp(w) - nearLo.lp(w)) * Math.exp(-(dropDur - dropPhase) * 600) * dropEnv;
                    dropPhase -= 1 / rate;
                }
            }
        } else if (key === 'dropsFar') {
            const farLight = def && def.light;
            if (farLight) {
                // 细雨远噼啪：轻柔密集颗粒底，25~70 滴/秒，幅度小（远），
                // 与近处清晰滴答形成远近层次
                farNext -= 1 / rate;
                if (farNext <= 0) {
                    farDur = 0.001 + Math.random() * 0.002;      // 1~3ms
                    farPhase = farDur;
                    farEnv = 0.04 + Math.random() * 0.05;        // 轻柔
                    farNext = 0.014 + Math.random() * 0.026;     // 25~70 滴/秒
                }
                if (farPhase > 0) {
                    s = (farHi.lp(w) - farLo.lp(w)) * Math.exp(-(farDur - farPhase) * 800) * farEnv;
                    farPhase -= 1 / rate;
                }
            } else {
                // 暴雨远噼啪：密集轻柔中频脉冲，40~120 滴/秒
                farNext -= 1 / rate;
                if (farNext <= 0) {
                    farDur = 0.001 + Math.random() * 0.0025;      // 1~3.5ms
                    farPhase = farDur;
                    farEnv = 0.05 + Math.random() * 0.07;         // 轻柔（远）
                    farNext = 0.008 + Math.random() * 0.017;      // 40~120 滴/秒
                }
                if (farPhase > 0) {
                    s = (farHi.lp(w) - farLo.lp(w)) * Math.exp(-(farDur - farPhase) * 800) * farEnv;
                    farPhase -= 1 / rate;
                }
            }
        } else if (key === 'dripGutter') {
            // 房檐滴水：慢速（0.5~1.5s 一滴）、短促"嗒"——中低频噪声脉冲，
            // 无音高不拖尾（不是低频"咚"，避免像鼓点），与石面清脆滴答形成材质差
            gutNext -= 1 / rate;
            if (gutNext <= 0) {
                gutDur = 0.025 + Math.random() * 0.035;      // 25~60ms 短促
                gutPhase = gutDur;
                gutEnv = 0.28 + Math.random() * 0.3;         // 中等
                gutNext = 0.5 + Math.random() * 1.0;         // 0.5~1.5s 一滴
            }
            if (gutPhase > 0) {
                s = gutLo.lp(w) * Math.exp(-(gutDur - gutPhase) * 90) * gutEnv * 0.75;
                gutPhase -= 1 / rate;
            }
        } else if (key === 'wind') {
            s = windLo.lp(brown) * 2.4 * walkEnv(i);
        } else if (key === 'leaf') {
            s = leafLo.lp(w) * 0.35 * walkEnv(i);
        } else if (key === 'pink') {
            s = pink * 0.2 * walkEnv(i);
        } else if (key === 'swell') {
            const lfo = 0.75 + 0.25 * Math.sin(2 * Math.PI * lfoFreq * i / rate);
            s = swellLo.lp(brown) * 3.6 * lfo;
        } else if (key === 'foam') {
            s = (foamHi.lp(w) - foamLo.lp(w)) * 1.1 * walkEnv(i);
        } else if (key === 'abyss') {
            // 深海低频水压底噪：棕色噪声 → 140Hz 极低通，恒定、无起伏（水下压力感）
            // 背景音调大 3 倍：增益 ×3（×8→×24），峰值限幅在 1.0 内防削波
            s = Math.max(-1, Math.min(1, abyssLo.lp(brown) * 24.0));
        } else if (key === 'bubble') {
            // 深海气泡（可重叠的咕噜串）：一串 1~8 个（偏大分布），串内密集触发，
            // 新气泡不等旧气泡播完——多个气泡同时发声叠加，像水下气泡群一起冒；
            // quiet=小音量远层组：音色与主组完全一致（低频咕噜、同样时长），
            // 只是音量更小、串略小略稀——"远处也有一组同样的气泡"
            const BUBBLE_VOLUME = 0.9;    // 气泡整体音量：提到能压过背景底噪、清晰可闻（原 0.35）
            const quiet = def && def.quiet;
            const ampScale = (quiet ? 0.12 : 0.3) * BUBBLE_VOLUME;
            const maxConc = quiet ? 3 : 4;
            // 串内触发间隔：以 > 气泡时长为主 → 多数气泡一前一后，偶尔重叠
            const inSerGap = quiet ? 0.3 + Math.pow(Math.random(), 2) * 0.4   // 0.3~0.7s
                                   : 0.24 + Math.pow(Math.random(), 2) * 0.32; // 0.24~0.56s
            const serGap = quiet ? 1.4 + Math.pow(Math.random(), 2) * 4.5
                                 : 1.2 + Math.pow(Math.random(), 2) * 4;   // 串间隔
            bubNext -= 1 / rate;
            if (bubNext <= 0) {
                if (bubRemain <= 0) {
                    bubRemain = quiet
                        ? 1 + Math.floor(Math.pow(Math.random(), 0.7) * 5)  // 小声组串略小
                        : 3 + Math.floor(Math.random() * 8);                // 主组串 3~10 个
                    bubNext = serGap;
                } else {
                    bubRemain--;
                    bubNext = inSerGap;
                }
                if (bubbles.length < maxConc) {
                    // "咕噜"浊音：低频 60~160Hz，正弦 + 少量噪声混合（不是纯正弦电子音），
                    // 时长 120~260ms——像水泡翻滚的"咕"，不是"呜"长音
                    const f0 = 60 + Math.random() * 100;
                    bubbles.push({
                        f0,
                        f1: f0 * (1.05 + Math.random() * 0.25),
                        amp: (0.3 + Math.random() * 0.3) * ampScale,
                        dur: 0.12 + Math.random() * 0.14,
                        t: 0,
                    });
                }
            }
            // 渲染所有活跃气泡（各自独立推进，可同时发声）
            for (let bi = bubbles.length - 1; bi >= 0; bi--) {
                const b = bubbles[bi];
                if (b.t >= b.dur) { bubbles.splice(bi, 1); continue; }
                const p = b.t / b.dur;
                const f = b.f0 + (b.f1 - b.f0) * p;
                const env = Math.sin(Math.PI * Math.min(1, p));
                // 正弦 80% + 低频噪声 20% 混合 → 浊音"咕"（纯正弦会像电子音）
                s += (Math.sin(2 * Math.PI * f * b.t) * 0.8 + w * 0.2) * env * b.amp;
                b.t += 1 / rate;
            }
        }
        data[i] = s;
    }
    return buf;
}

// ── 空间化与播放 ─────────────────────────────────────────────────────────

// HRTF 摆位：声源放在 X-Z 平面单位圆上（listener 默认在原点朝 -Z）
function placePanner(panner, az, when) {
    const t = when !== undefined ? when : audioCtx.currentTime;
    panner.positionX.setTargetAtTime(Math.sin(az), t, 0.05);
    panner.positionY.setTargetAtTime(0, t, 0.05);
    panner.positionZ.setTargetAtTime(-Math.cos(az), t, 0.05);
}

function makeSource(buffer, gain, panner) {
    const s = audioCtx.createBufferSource();
    s.buffer = buffer;
    s.connect(gain);
    gain.connect(panner);
    s.start();
    return s;
}

// 漂移层：每 3.5~7s 才微调一次目标方位（±8°），HRTF 用 τ=4s 极缓渐变跟随——
// 声像几乎静止，只在很长的尺度上缓缓呼吸（如"左40% 20秒内渐到左44%"）
function scheduleDrift(ly) {
    ly.driftTimer = setTimeout(() => {
        const az = Math.min(1.0, Math.max(-1.0, ly.az + (Math.random() - 0.5) * 0.28));
        ly.az = az;
        const t = audioCtx.currentTime;
        ly.panner.positionX.setTargetAtTime(Math.sin(az), t, 4);
        ly.panner.positionY.setTargetAtTime(0, t, 4);
        ly.panner.positionZ.setTargetAtTime(-Math.cos(az), t, 4);
        scheduleDrift(ly);
    }, 3500 + Math.random() * 3500);
}

function createLayer(def) {
    const ly = { def, panner: null, bufs: [null, null], srcs: [null, null], gains: [null, null], driftTimer: null, swapTimer: null, active: 0, az: def.az ?? 0 };
    if (def.drift && ly.az === 0) ly.az = (Math.random() * 2 - 1) * 0.6;  // 漂移层随机起点
    ly.panner = new PannerNode(audioCtx, {
        panningModel: 'HRTF',
        distanceModel: 'inverse',
        refDistance: 1,
        maxDistance: 100,
        rolloffFactor: 0,   // 无距离衰减：方位只决定方向，音量统一
    });
    // 风声层走 windBoost 节点（阵风调度），其他层直连 masterGain
    ly.panner.connect(def.key === 'wind' && windBoost ? windBoost : masterGain);
    for (let i = 0; i < 2; i++) {
        ly.bufs[i] = makeLayerBuffer(audioCtx, def.key, def);
        ly.gains[i] = audioCtx.createGain();
        ly.gains[i].gain.value = i === 0 ? 1 : 0;
    }
    ly.srcs[0] = makeSource(ly.bufs[0], ly.gains[0], ly.panner);
    ly.srcs[1] = makeSource(ly.bufs[1], ly.gains[1], ly.panner);
    return ly;
}

// 单层交叉淡化：该层独立切换，间隔带随机 jitter（错峰 → 任何时刻只一层在过渡）。
// 间隔 = buffer - FADE - 余量 + jitter，保证切换发生时旧声源还剩 >FADE 的信号可淡出，
// 新声源淡入前不会出现"无声间隙"。
function scheduleLayerSwap(ly) {
    const delay = (BUFFER_SECONDS - FADE - SWAP_LEAD + Math.random() * SWAP_JITTER) * 1000;
    ly.swapTimer = setTimeout(() => {
        swapLayer(ly);
        scheduleLayerSwap(ly);
    }, delay);
}

// 等功率交叉淡化曲线（sin/cos）：两段不相关噪声线性交叉（各 0.5 时功率 = 0.25+0.25 = 0.5）
// 会在中间塌陷 -3dB——用户听到的"每 20 秒左右音量骤降 0.几秒"就是这个。
// 等功率用 cos(θ) 淡出 + sin(θ) 淡入：cos²+sin²=1，任意时刻总功率恒定，听感无缝。
const FADE_CURVE_LEN = 128;
const EQ_FADE_OUT = new Float32Array(FADE_CURVE_LEN);
const EQ_FADE_IN = new Float32Array(FADE_CURVE_LEN);
for (let i = 0; i < FADE_CURVE_LEN; i++) {
    const th = (i / (FADE_CURVE_LEN - 1)) * (Math.PI / 2);
    EQ_FADE_OUT[i] = Math.cos(th);
    EQ_FADE_IN[i] = Math.sin(th);
}

function swapLayer(ly) {
    const t = audioCtx.currentTime + 0.02;
    const next = 1 - ly.active;
    ly.gains[ly.active].gain.cancelScheduledValues(t);
    ly.gains[ly.active].gain.setValueAtTime(Math.max(ly.gains[ly.active].gain.value, 0.0001), t);
    ly.gains[ly.active].gain.setValueCurveAtTime(EQ_FADE_OUT, t, FADE);
    try { ly.srcs[next].stop(); } catch (e) {}
    try { ly.srcs[next].disconnect(); } catch (e) {}
    ly.srcs[next] = makeSource(ly.bufs[next], ly.gains[next], ly.panner);
    ly.gains[next].gain.cancelScheduledValues(t);
    ly.gains[next].gain.setValueAtTime(0, t);
    ly.gains[next].gain.setValueCurveAtTime(EQ_FADE_IN, t, FADE);
    ly.active = next;
}

// 三角分布 [min,max]，峰值在 mode（高潮持续时间：更多落在 ~40s）
function triMode(min, max, mode) {
    const u = Math.random();
    const p = (mode - min) / (max - min);
    return u < p
        ? min + (mode - min) * Math.sqrt(u / p)
        : mode + (max - mode) * (1 - Math.sqrt((1 - u) / (1 - p)));
}

// 深海高潮包络：整体增益 0.55~1.55 起伏（大浪来了又走）
const swell = { phase: 'calm', remain: 0, target: 1 };
let swellTimer = null;
let swellGain = null;

function planSwell() {
    if (Math.random() < 0.4) {
        swell.phase = 'swell';
        swell.remain = triMode(20, 90, 55);
        swell.target = 1.05 + Math.random() * 0.3;
    } else {
        swell.phase = 'calm';
        swell.remain = 25 + Math.random() * 45;
        swell.target = 0.7 + Math.random() * 0.15;
    }
}

function startSwellScheduler() {
    planSwell();
    swellTimer = setInterval(() => {
        if (!swellGain) return;
        swellGain.gain.setTargetAtTime(swell.target, audioCtx.currentTime, 1.5);
        swell.remain -= 0.1;
        if (swell.remain <= 0) planSwell();
    }, 100);
}

// ── 森林阵风调度器：主体保持稳定，偶尔来一阵风（阴风）再平静 ──
// 只作用于风声层（windBoost 节点）；阵风增益 1.25~1.5 倍、平滑渐变（τ=1.5s），
// 既"突然来一下"又不会过头；多数时间平静（18~48s）保持主体稳定
const windGust = { phase: 'steady', remain: 0, target: 1 };
let windTimer = null;
let windBoost = null;

function planWindGust() {
    if (Math.random() < 0.3) {
        windGust.phase = 'gust';
        windGust.remain = 4 + Math.random() * 8;         // 阵风持续 4~12s
        windGust.target = 1.25 + Math.random() * 0.25;   // 抬升 1.25~1.5 倍（不过头）
    } else {
        windGust.phase = 'steady';
        windGust.remain = 18 + Math.random() * 30;       // 平静期 18~48s
        windGust.target = 1;
    }
}

function startWindScheduler() {
    planWindGust();
    windTimer = setInterval(() => {
        if (!windBoost) return;
        windBoost.gain.setTargetAtTime(windGust.target, audioCtx.currentTime, 1.5);
        windGust.remain -= 0.1;
        if (windGust.remain <= 0) planWindGust();
    }, 100);
}

// 开始播放指定类型的噪声
async function startNoise(type) {
    await initAudio();

    if (audioCtx.state === 'suspended') {
        try { await audioCtx.resume(); } catch (e) { console.error('音频恢复失败:', e); }
    }
    stopNoise();

    if (type === 'off') {
        state.soundType = 'off';
        updateSoundButtons();
        return;
    }
    if (!LAYER_DEFS[type]) return;

    const stereo = state.settings.stereo !== false;

    // 总音量节点（海面后接高潮包络——浪涌来了又走；深海恒定水压，不挂包络；
    // 森林后接阵风节点——风声偶发突袭，只作用于风声层）
    masterGain = audioCtx.createGain();
    masterGain.gain.value = state.settings.volume / 100;
    if (type === 'sea') {
        swellGain = audioCtx.createGain();
        swellGain.gain.value = 1;
        masterGain.connect(swellGain);
        swellGain.connect(audioCtx.destination);
        startSwellScheduler();
    } else if (type === 'forest') {
        windBoost = audioCtx.createGain();
        windBoost.gain.value = 1;
        masterGain.connect(windBoost);
        windBoost.connect(audioCtx.destination);
        startWindScheduler();
    } else {
        masterGain.connect(audioCtx.destination);
    }

    // 创建各声源层并摆位，每层独立错峰交叉淡化
    layers = LAYER_DEFS[type].map((def) => {
        const ly = createLayer(def);
        if (!stereo) {
            placePanner(ly.panner, 0);      // 单声道：全部居中
        } else {
            placePanner(ly.panner, ly.az);  // 立体声：立即摆到初始方位（固定层到位、漂移层从起点开始）
            if (def.drift) scheduleDrift(ly); // 漂移层：后续在此基础上缓慢游走
        }
        scheduleLayerSwap(ly);           // 每层独立切换（随机错峰）
        return ly;
    });

    state.soundType = type;
    updateSoundButtons();
}

// 停止噪声
function stopNoise() {
    if (swellTimer) { clearInterval(swellTimer); swellTimer = null; }
    if (windTimer) { clearInterval(windTimer); windTimer = null; }
    for (const ly of layers) {
        if (ly.driftTimer) clearTimeout(ly.driftTimer);
        if (ly.swapTimer) clearTimeout(ly.swapTimer);
        for (let i = 0; i < 2; i++) {
            if (ly.srcs[i]) { try { ly.srcs[i].stop(); } catch (e) {} try { ly.srcs[i].disconnect(); } catch (e) {} ly.srcs[i] = null; }
            if (ly.gains[i]) { try { ly.gains[i].disconnect(); } catch (e) {} ly.gains[i] = null; }
            ly.bufs[i] = null;
        }
        if (ly.panner) { try { ly.panner.disconnect(); } catch (e) {} ly.panner = null; }
    }
    layers = [];
    if (swellGain) { try { swellGain.disconnect(); } catch (e) {} swellGain = null; }
    if (windBoost) { try { windBoost.disconnect(); } catch (e) {} windBoost = null; }
    if (masterGain) {
        try { masterGain.disconnect(); } catch (e) {}
        masterGain = null;
    }
}

// 更新音量（供音量滑块调用）
function updateNoiseVolume() {
    if (masterGain && audioCtx) {
        masterGain.gain.setTargetAtTime(state.settings.volume / 100, audioCtx.currentTime, 0.08);
    }
}

// 播放完成提示音（和弦铃声）
function playChime() {
    if (!audioCtx) return;
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    [523.25, 659.25, 783.99].forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        const t = audioCtx.currentTime + i * 0.15;
        gain.gain.setValueAtTime(0, t);
        gain.gain.linearRampToValueAtTime(0.2, t + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.4);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(t);
        osc.stop(t + 0.45);
    });
}
