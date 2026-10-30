/* 色牌 · 抢灯 —— 页面脚本
 *
 * 两条数据来源，互不重复：
 *   · SSE /world/{id}/events   推「公开视图」——台面、张数、灯、记录，谁都看不到别人的手牌
 *   · 动作响应里的 me          我自己的手牌 + 这一手能打哪几张（合法性只由引擎判）
 *
 * 手牌以公开视图的张数为准：对不上就补一次 look，这样别人 +2 给我、
 * 或对手重开了一局，页面都能自己跟上，不靠轮询。
 */
(function () {
  'use strict';

  var WID = window.WORLD_ID;
  var UID = window.USER_ID;
  var MY_KEY = UID != null ? 'human:' + UID : null;

  var lobby = null;    // 大厅：几张桌子、谁坐着
  var tid = 1;         // 当前看的桌号
  var screen = 'lobby';// lobby | table
  var pub = null;      // 公开视图（当前桌）
  var me = null;       // 我自己的椅子：{i, name, kind, hand, legal, turn, lanterns, count}
  var busy = false;    // 一次只发一个动作，避免重复出牌
  var lastLook = 0;
  var toastTimer = 0;

  var $ = function (id) { return document.getElementById(id); };

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function token() { return localStorage.getItem('access_token') || ''; }

  // ── 输入 ────────────────────────────────────────────────
  function toast(text) {
    var box = $('toast');
    box.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.hidden = true; }, 3400);
  }

  function send(payload, quiet) {
    if (!WID) return Promise.resolve(null);
    if (busy) return Promise.resolve(null);
    // 所有动作都指明桌子：牌室里有好几张，默认那张未必是玩家在看的那张
    var body = { type: 'page_command', payload: payload };
    if (payload.action !== 'lobby' && payload.table == null) payload.table = tid;
    busy = true;
    return fetch('/world/' + WID + '/api/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token() },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || !data.success) {
          toast(data.detail || data.reason || '这一步没成功（' + res.status + '）');
          return null;
        }
        apply(data.result, quiet);
        return data.result;
      });
    }).catch(function () {
      toast('没连上牌桌，再点一次试试。');
      return null;
    }).then(function (r) {
      busy = false;
      return r;
    }, function () {
      busy = false;
      return null;
    });
  }

  function apply(result, quiet) {
    if (!result) return;
    if (result.lobby) lobby = result.lobby;
    if (result.table) tid = result.table;
    if (result.view) pub = result.view;
    if ('me' in result) me = result.me;
    if (result.say && !quiet) toast(result.say);
    // 切不切屏由调用方决定：大厅里点"大厅"时 me 还在身上，按 me 判断会立刻弹回牌桌
    render();
  }

  // ── 实时订阅 ────────────────────────────────────────────
  function connect() {
    if (!WID || !window.EventSource) return;
    var es = new EventSource('/world/' + WID + '/events');
    es.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg) return;
      // 世界推的是 {lobby, table, state}：大厅永远更新，牌桌只更新我正在看的那张
      if (msg.lobby) lobby = msg.lobby;
      if (msg.state && msg.table === tid) pub = msg.state;
      render();
      if (screen === 'table') syncHand();
    };
  }

  /** 张数对不上就补一次 look（我自己的手牌只有我拿得到） */
  function syncHand() {
    var seat = mySeat();
    if (!seat) {
      if (me) { me = null; render(); }
      return;
    }
    if (me && me.i === seat.i && me.count === seat.count) return;
    if (busy) { setTimeout(syncHand, 400); return; }
    var now = Date.now();
    if (now - lastLook < 600) return;
    lastLook = now;
    send({ action: 'look' }, true);
  }

  function mySeat() {
    if (!pub || !pub.seats || !pub.seats.length) return null;
    if (MY_KEY) {
      for (var i = 0; i < pub.seats.length; i++) {
        if (pub.seats[i].key === MY_KEY) return pub.seats[i];
      }
      return null;
    }
    return me ? pub.seats[me.i] || null : null;
  }

  function otherSeat() {
    if (!pub || !pub.seats) return null;
    var seat = mySeat();
    for (var i = 0; i < pub.seats.length; i++) {
      if (!seat || pub.seats[i].i !== seat.i) return pub.seats[i];
    }
    return null;
  }

  // ── 渲染 ────────────────────────────────────────────────
  function render() {
    var inTable = screen === 'table';
    document.body.classList.toggle('in-table', inTable);
    $('lobby').hidden = inTable;
    $('btn-lobby').hidden = !inTable;
    if (!inTable) { renderLobby(); return; }
    renderFoe();
    renderMiddle();
    renderLog();
    renderMine();
    renderHand();
    renderActions();
  }

  function phaseLabel(row) {
    if (row.phase === 'playing') return '第 ' + row.round + ' 局进行中';
    if (row.phase === 'over') return '第 ' + row.round + ' 局已结束';
    return row.seats.length ? '等人入座' : '空着';
  }

  function renderLobby() {
    var box = $('lobby');
    clear(box);

    var head = node('div', 'lobby-head');
    head.appendChild(node('h1', 'lobby-title', '牌室'));
    head.appendChild(node('p', 'lobby-sub',
      '四张桌子，群里的 AI 和人随便坐。一人七张，跟色或跟点，先出完的人抢一盏灯。'));
    box.appendChild(head);

    var grid = node('div', 'lobby-grid');
    var rows = (lobby && lobby.tables) || [];
    if (!rows.length) {
      grid.appendChild(node('p', 'lobby-sub', '牌室还没开张，稍等一下再进来。'));
    }
    rows.forEach(function (row) {
      var mine = lobby && lobby.me_table === row.tid;
      var card = node('button', 'table-card' + (mine ? ' mine' : ''));
      card.type = 'button';

      var top = node('div', 'table-card-head');
      top.appendChild(node('span', 'table-card-no', row.tid + ' 号桌'));
      top.appendChild(node('span', 'table-card-state', mine ? '你坐这儿' : phaseLabel(row)));
      card.appendChild(top);

      var seats = node('div', 'table-card-seats');
      for (var i = 0; i < 2; i++) {
        var s = row.seats[i];
        var seat = node('div', 'table-seat');
        seat.appendChild(node('span', 'seat-name', s ? s.name : '空椅子'));
        if (s) {
          seat.appendChild(node('span', 'seat-kind', s.kind === 'ai' ? 'AI' : '玩家'));
          seat.appendChild(lamps(s.lanterns, lobby.goal));
        }
        seats.appendChild(seat);
      }
      card.appendChild(seats);
      card.onclick = function () { enterTable(row); };
      grid.appendChild(card);
    });
    box.appendChild(grid);

    var foot = node('div', 'lobby-foot');
    var help = node('button', 'btn', '怎么玩');
    help.type = 'button';
    help.onclick = openGuide;
    foot.appendChild(help);
    box.appendChild(foot);
  }

  /** 点桌子：没坐下就入座，坐下过就直接进那张桌 */
  function enterTable(row) {
    var seated = lobby && lobby.me_table;
    if (seated && seated !== row.tid) {
      toast('你已经在 ' + seated + ' 号桌坐着了——先在那儿打完这一局。');
      return;
    }
    tid = row.tid;
    if (!seated && row.open > 0) {
      send({ action: 'sit', table: row.tid }).then(function (r) {
        if (r && r.me) { screen = 'table'; render(); }
      });
      return;
    }
    screen = 'table';
    render();
    send({ action: 'look', table: row.tid }, true);
  }

  function lamps(n, goal) {
    var box = node('div', 'lamps');
    var lit = n || 0;
    var total = Math.max(goal || 3, lit);
    for (var i = 0; i < total; i++) box.appendChild(node('span', i < lit ? 'lamp on' : 'lamp'));
    box.title = '灯 ' + lit + ' / ' + (goal || 3);
    return box;
  }

  function seatHead(seat, fallbackName, goal) {
    var head = node('div', 'seat-head');
    head.appendChild(node('span', 'name', seat ? seat.name : fallbackName));
    if (seat) {
      head.appendChild(node('span', seat.kind === 'ai' ? 'tag ai' : 'tag', seat.kind === 'ai' ? 'AI' : '玩家'));
      head.appendChild(node('span', 'count', '· ' + seat.count + ' 张'));
    }
    head.appendChild(lamps(seat ? seat.lanterns : 0, pub && pub.goal));
    return head;
  }

  function renderFoe() {
    var box = $('foe');
    clear(box);
    var seat = otherSeat();
    var thinking = !!(seat && pub && pub.phase === 'playing' && pub.turn === seat.i);
    box.classList.toggle('thinking', thinking);
    box.appendChild(seatHead(seat, '空椅子'));

    var backs = node('div', 'backs');
    if (seat) {
      for (var i = 0; i < Math.min(seat.count, 7); i++) backs.appendChild(node('span', 'back'));
    } else {
      backs.appendChild(node('span', 'count', '等一位对手坐下'));
    }
    box.appendChild(backs);
  }

  function cardEl(card) {
    var el = node('button', 'card c-' + (card.color || 'W'));
    el.type = 'button';
    el.dataset.code = card.code;
    el.setAttribute('aria-label', card.text);
    el.title = card.text;
    var glyph = card.color ? (card.rank === 'S' ? '⊘' : card.rank === 'D' ? '+2' : card.rank) : '★';
    el.appendChild(node('span', 'g', glyph));
    return el;
  }

  function renderMiddle() {
    var topSlot = $('top-slot');
    var deckSlot = $('deck-slot');
    clear(topSlot);
    clear(deckSlot);

    if (pub && pub.top) {
      var top = cardEl(pub.top);
      top.classList.add('big');
      topSlot.appendChild(top);
    }

    var canDraw = !!(me && me.turn && pub && pub.phase === 'playing');
    var deck = node('button', 'deck-btn' + (canDraw ? ' hot' : ''));
    deck.type = 'button';
    deck.dataset.n = pub ? String(pub.deck) : '—';
    deck.setAttribute('aria-label', '摸一张牌');
    deck.disabled = !canDraw;
    deck.onclick = function () {
      if (!me) return toast('先坐下再摸牌。');
      if (!canDraw) return toast('现在不是你的回合。');
      send({ action: 'draw' });
    };
    deckSlot.appendChild(deck);

    var line = $('turn');
    clear(line);
    // 这一行是唯一的"现在该干什么"：说得越具体，越不需要先学会规则
    if (!pub) {
      line.appendChild(node('span', null, '牌桌还没开张。'));
      return;
    }
    if (!me) {
      line.appendChild(node('b', null, '你还没上桌'));
      line.appendChild(node('span', null, '：点下面的「坐下」，坐满两个人就发牌'));
      return;
    }
    if (pub.phase === 'waiting') {
      line.appendChild(node('b', null, '还差一个人'));
      line.appendChild(node('span', null, '：点「喊它来坐」，群里的人或 AI 坐下就发牌'));
      return;
    }
    if (pub.phase === 'over') {
      var winner = null;
      (pub.seats || []).forEach(function (s) { if (s.key === pub.winner) winner = s; });
      line.appendChild(node('span', null, '第 ' + pub.round + ' 局 · '));
      line.appendChild(node('b', null, winner ? winner.name : '有人'));
      line.appendChild(node('span', null, ' 抢到一盏灯'));
      return;
    }
    if (pub.color_name) {
      line.appendChild(node('span', null, '当前色 ' + pub.color_name + ' · 第 ' + pub.round + ' 局 · '));
    }
    if (me.turn) {
      var plays = (me.legal || []).length;
      line.appendChild(node('b', null, '轮到你'));
      line.appendChild(node('span', null,
        plays ? '：点亮起来的牌，点一下就打出去' : '：手上没有能跟的牌，点牌堆摸一张'));
    } else {
      var turnSeat = pub.seats[pub.turn % (pub.seats.length || 1)] || null;
      line.appendChild(node('b', null, turnSeat ? turnSeat.name : '对手'));
      line.appendChild(node('span', null, ' 正在想…'));
      if (turnSeat && turnSeat.kind === 'ai') {
        line.appendChild(node('span', null, '（等久了点「催一下」）'));
      }
    }
  }

  function renderLog() {
    var box = $('log');
    clear(box);
    ((pub && pub.log) || []).forEach(function (item) { box.appendChild(node('p', null, item.text)); });
    box.scrollTop = box.scrollHeight;
  }

  function renderMine() {
    var head = $('mine-head');
    clear(head);
    var seat = mySeat();
    head.appendChild(seatHead(seat, '你'));
  }

  function renderHand() {
    var box = $('hand');
    clear(box);
    if (!me) return;
    var legal = me.legal || [];
    var turn = !!me.turn;
    (me.hand || []).forEach(function (card) {
      var el = cardEl(card);
      var playable = legal.indexOf(card.code) !== -1;
      el.classList.add(playable ? 'playable' : 'dim');
      if (turn) {
        el.onclick = playable
          ? function () { tapCard(card); }
          : function () { toast('这张跟台面配不上：同色或同点才行。'); };
      }
      box.appendChild(el);
    });
  }

  function tapCard(card) {
    if (!me) return toast('先坐下再出牌。');
    if (!me.turn) return toast('现在不是你的回合，等对面出完。');
    if (busy) return;
    if (!card.color) return openWild();
    send({ action: 'play', code: card.code });
  }

  function renderActions() {
    var box = $('actions');
    clear(box);
    var phase = pub ? pub.phase : 'waiting';
    var seats = (pub && pub.seats) || [];

    function btn(label, primary, onclick, extra) {
      var b = node('button', 'btn' + (primary ? ' primary' : '') + (extra ? ' ' + extra : ''), label);
      b.type = 'button';
      b.onclick = onclick;
      box.appendChild(b);
      return b;
    }

    if (!me) {
      if (seats.length >= 2) {
        btn('桌上已满', false, function () { toast('两把椅子都有人了，等这一局打完再看。'); });
      } else {
        btn('坐下', true, function () { send({ action: 'sit' }); });
      }
      btn('玩法', false, openGuide, 'ghost');
      return;
    }
    if (phase === 'waiting') {
      btn('喊它来坐', true, function () { send({ action: 'invite' }); });
      btn('玩法', false, openGuide, 'ghost');
      return;
    }
    if (phase === 'over') {
      btn('再来一局', true, function () { send({ action: 'new' }); });
      btn('认输', false, function () {
        if (window.confirm('认输就把这一局的灯让给对面，确定？')) send({ action: 'quit' });
      }, 'ghost');
      return;
    }
    if (!me.turn) {
      // 对面的回合：不催也能等，卡住了给一个直接的手动重叫
      btn('催一下', false, function () { send({ action: 'poke' }); });
      btn('认输', false, function () {
        if (window.confirm('认输就把这一局的灯让给对面，确定？')) send({ action: 'quit' });
      }, 'ghost');
      return;
    }
    // 手上有能打的牌时，「摸一张」只是备选；没得打了才让它当主角
    var hasPlay = (me.legal || []).length > 0;
    var drawBtn = btn('摸一张', !hasPlay, function () { send({ action: 'draw' }); });
    drawBtn.disabled = !me.turn;
    btn('认输', false, function () {
      if (window.confirm('认输就把这一局的灯让给对面，确定？')) send({ action: 'quit' });
    }, 'ghost');
  }

  // ── 抽屉 ────────────────────────────────────────────────
  function onEsc(e) { if (e.key === 'Escape') closeSheet(); }

  function closeSheet() {
    $('sheet').hidden = true;
    document.removeEventListener('keydown', onEsc);
  }

  function showSheet(title, body, foot) {
    $('sheet-title').textContent = title;
    var bodyBox = $('sheet-body');
    clear(bodyBox);
    if (typeof body === 'string') bodyBox.textContent = body;
    else if (body) bodyBox.appendChild(body);
    var footBox = $('sheet-foot');
    clear(footBox);
    (foot || []).forEach(function (b) {
      var el = node('button', 'btn' + (b.primary ? ' primary' : ''), b.label);
      el.type = 'button';
      el.onclick = b.onclick;
      footBox.appendChild(el);
    });
    $('sheet').hidden = false;
    document.addEventListener('keydown', onEsc);
  }

  function openWild() {
    var pick = node('div', 'color-pick');
    [['R', '红'], ['Y', '黄'], ['B', '蓝'], ['G', '绿']].forEach(function (pair) {
      var b = node('button', 'color-btn', pair[1]);
      b.type = 'button';
      b.dataset.c = pair[0];
      b.onclick = function () {
        closeSheet();
        send({ action: 'play', code: 'WW', color: pair[0] });
      };
      pick.appendChild(b);
    });
    showSheet('变色牌：把台面改成哪一门？', pick, [{ label: '算了', onclick: closeSheet }]);
  }

  // 第一次进来先教一遍：玩家不用先读规则，照着提示点就行
  function guideSeen() {
    try { return !!localStorage.getItem('cc.guide.' + WID); } catch (e) { return true; }
  }

  function markGuided() {
    try { localStorage.setItem('cc.guide.' + WID, '1'); } catch (e) { /* 隐私模式下不记也行 */ }
  }

  function openGuide() {
    var body = node('div', 'guide');
    [
      ['1', '两张椅子，一人七张。先把手牌打光的人抢到一盏灯。'],
      ['2', '跟台面出：同色，或者同数字、同符号。'],
      ['3', '点亮的牌＝现在能出，点一下就打出去；一张都亮不起来，就点牌堆摸一张。'],
      ['4', '摸完这一张，回合交给对面（摸完不再补打）。'],
    ].forEach(function (row) {
      var line = node('div', 'guide-row');
      line.appendChild(node('span', 'guide-no', row[0]));
      line.appendChild(node('span', 'guide-text', row[1]));
      body.appendChild(line);
    });

    var legend = node('div', 'guide-legend');
    [['⊘', '跳过：对面这一轮不能出'],
     ['+2', '对面摸两张，还是你出'],
     ['★', '任意时刻都能出，出时选一个颜色']].forEach(function (pair) {
      var item = node('div', 'guide-legend-item');
      item.appendChild(node('span', 'guide-glyph', pair[0]));
      item.appendChild(node('span', 'guide-text', pair[1]));
      legend.appendChild(item);
    });
    body.appendChild(legend);

    showSheet('怎么玩', body, [
      { label: '完整规则', onclick: openRulesText },
      { label: '开始打牌', primary: true, onclick: function () { markGuided(); closeSheet(); } },
    ]);
  }

  function openRulesText() {
    send({ action: 'rules' }, true).then(function (r) {
      showSheet('完整规则', (r && r.say) || '还没连上牌桌，稍后再看。',
        [{ label: '知道了', primary: true, onclick: function () { markGuided(); closeSheet(); } }]);
    });
  }

  // ── 启动 ────────────────────────────────────────────────
  function boot() {
    $('sheet').addEventListener('click', function (e) {
      if (e.target === $('sheet')) closeSheet();
    });

    $('btn-full').onclick = function () {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) {
        var p = document.documentElement.requestFullscreen();
        if (p && p.catch) p.catch(function () {});
      }
    };
    $('btn-rules').onclick = openGuide;
    $('btn-lobby').onclick = function () {
      screen = 'lobby';
      render();
      send({ action: 'lobby' }, true);
    };

    if (!WID) {
      $('turn').textContent = '这个页面要在群视界里打开才有牌桌。';
      return;
    }

    render();
    send({ action: 'look' }, true).then(function (r) {
      // 已经坐在某张桌子上的人直接进牌桌，没坐的留在大厅挑桌子
      if (r && r.me) { screen = 'table'; render(); }
    });
    connect();
    if (!guideSeen()) openGuide();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
