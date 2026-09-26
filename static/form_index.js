const STORAGE_KEY = 'kobe_review_v2';

// uid を localStorage に永続化し、URL にない場合はリダイレクトで補完
(function() {
  const params = new URLSearchParams(location.search);
  const urlUid = params.get('uid');
  if (urlUid) {
    localStorage.setItem('kobe_uid', urlUid);
  } else {
    const cached = localStorage.getItem('kobe_uid');
    if (cached) {
      params.set('uid', cached);
      location.replace(location.pathname + '?' + params.toString());
    }
  }
})();

// ── LIFF初期化とプロフィールプリフィル ──
// 修正理由: 以前は line_user_id/uid をクライアントから送られた値のまま
// サーバーが信用しており、任意のLINEユーザーIDを偽装してなりすまし投稿・
// プロフィール改ざんが可能だった。liff.getIDToken()で取得した署名付き
// ID tokenをサーバー側でLINEのverifyエンドポイントに照会し、真正な
// ユーザーIDのみを信用するように変更した。
let _liffInitPromise = null;
function ensureLiffInit() {
  if (!_liffInitPromise) {
    _liffInitPromise = liff.init({ liffId: LIFF_ID });
  }
  return _liffInitPromise;
}

// LIFF IDトークン期限切れ→強制再ログインの共通処理は _partials/liff_auth.html の
// window.LiffAuth に集約（idExpired / forceReauth / clearFlag）。
// このページ(/)はLIFFエンドポイントURL未登録で、liff.login({redirectUri:'/'}) が400になる。
// そのため reauthMode='replace' で登録済みエンドポイント /liff/review へ遷移して復帰させる。
LiffAuth.configure({
  form: 'review', draftKey: 'kobe_review_v2',
  reauthMode: 'replace', reauthUrl: '/liff/review',
});
const idTokenExpired = LiffAuth.idExpired;
const forceReauthAndReload = LiffAuth.forceReauth;

// 修正理由: 同じ学籍番号で同じ科目×担当教員に重複投稿できてしまっていたため、
// 既に投稿済みの組み合わせをプリフィル時に取得し、科目選択画面でグレーアウト表示する
// （実際の受付可否は/submit側で再確認するため、これはあくまで補助的なUI表示）
let _reviewedSet = new Set();

// 会員登録情報（学部・学科）。プリフィルで解決する。専門科目の候補絞り込みに使う。
let _profile = null;
// ?course= の自動選択・プリロード取得はプリフィル（学部の確定）を待ってから行う。
document.getElementById('groupHelpBtn').addEventListener('click', (e) => {
  e.preventDefault();
  const help = document.getElementById('groupHelp');
  const open = help.classList.toggle('hidden') === false;
  e.currentTarget.setAttribute('aria-expanded', String(open));
});

// 団体コード欄の状態: 'empty'（未入力）/ 'checking' / 'ok' / 'invalid' / 'member'（所属済みの案内表示）
let _groupState = 'empty';
let _groupCheckSeq = 0;
let _groupTimer;
function setGroupStatus(state, text) {
  _groupState = state;
  const el = document.getElementById('groupStatus');
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('hidden', !text);
  el.classList.remove('text-green-600', 'text-red-500', 'text-gray-400');
  el.classList.add(state === 'ok' ? 'text-green-600' : state === 'invalid' ? 'text-red-500' : 'text-gray-400');
  // 無効な番号のまま送れないよう、送信ボタンの表示も更新する
  if (typeof updateProgressUI === 'function' && typeof PROGRESS_ITEMS !== 'undefined') updateProgressUI();
}
async function checkGroupCode() {
  const input = document.getElementById('group_code');
  const code = input.value.trim();
  if (!code) { setGroupStatus('empty', ''); return true; }
  const seq = ++_groupCheckSeq;
  setGroupStatus('checking', '確認中…');
  try {
    const res = await fetch('/api/group/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (res.status === 429) {
      if (seq === _groupCheckSeq) setGroupStatus('invalid', '確認の回数が多すぎます。少し待ってからもう一度お試しください');
      return false;
    }
    const d = await res.json();
    if (seq !== _groupCheckSeq) return false; // 入力が変わった後の古い応答は捨てる
    if (d.ok) { setGroupStatus('ok', `✅ ${d.name} として投稿します`); return true; }
    setGroupStatus('invalid', d.message || '団体コードを確認できませんでした');
    return false;
  } catch (_) {
    if (seq === _groupCheckSeq) setGroupStatus('invalid', '通信に失敗しました。もう一度お試しください');
    return false;
  }
}
document.getElementById('group_code').addEventListener('input', () => {
  clearTimeout(_groupTimer);
  _groupCheckSeq++;
  const v = document.getElementById('group_code').value.trim();
  if (!v) { setGroupStatus('empty', ''); return; }
  setGroupStatus('checking', '');
  _groupTimer = setTimeout(checkGroupCode, 600);
});
document.getElementById('group_code').addEventListener('blur', () => {
  if (_groupState === 'checking' && document.getElementById('group_code').value.trim()) {
    clearTimeout(_groupTimer);
    checkGroupCode();
  }
});

let _prefillResolve;
const _prefillDone = new Promise(r => { _prefillResolve = r; });

// この科目にレビュー投稿できるか（教養は全員／専門は本人の学部＝科目学部一致、
// 学科は一致・科目側が学科不明・本人が学科未登録のいずれかで可。農学部のみ
// 本人のコース名を学科名に変換してから突合する。海洋政策科学部は学部一致のみで可）。
function _isSubmittable(c) {
  const cat = (c && c.category) || '';
  if (cat === SUBMISSION_KYOYO_CATEGORY) return true;
  if (cat !== SUBMISSION_SENMON_CATEGORY) return false;
  const sf = ((c && c.faculty) || '').trim();
  if (sf === KYOTSU_SENMON_KISO_FACULTY) return true;
  const pf = ((_profile && _profile.faculty) || '').trim();
  if (!sf || !pf || sf !== pf) return false;
  if (pf === KAIYO_SEISAKU_FACULTY) return true;
  const sd = ((c && c.department) || '').trim();
  let pd = ((_profile && _profile.department) || '').trim();
  if (pf === '農学部') pd = NOGAKU_COURSE_TO_DEPARTMENT[pd] || pd;
  return !sd || !pd || sd === pd;
}

// 会員登録ゲートのブラー＋オーバーレイ機構（.blurredクラス・#authOverlay構造・
// showOverlay/hideOverlayの3点セット）はtemplates/liff/course.html（showAuthOverlay/
// hideAuthOverlay）・templates/contact.htmlにも同種の実装が存在する。表示要素のID体系
// （本ファイルはformWrapper、liff/course.htmlはcontent）とCSS基盤（本ファイルはTailwind、
// liff/course.htmlは独自<style>）が異なりFlex Message同様の共通部品化はしていないため、
// ゲートの見た目・文言・タイミングを変更する場合は他の2ファイルも合わせて確認すること
function showOverlay(html) {
  document.getElementById('authOverlayBox').innerHTML = html;
  document.getElementById('authOverlay').style.display = 'flex';
  document.getElementById('formWrapper').classList.add('blurred');
}

function hideOverlay() {
  document.getElementById('authOverlay').style.display = 'none';
  document.getElementById('formWrapper').classList.remove('blurred');
}

// 修正理由: 以前は未登録ユーザーでもその場でreg_nameを入力してプロフィールを
// 自動作成できたが、会員登録(/register)を必ず経由させる方針に変更したため、
// 未登録・未ログインの間はフォームをブラー＋オーバーレイで覆い、会員登録へ誘導する
async function initProfilePrefill() {
  try {
    await ensureLiffInit();
  } catch (e) {
    showOverlay(`<div class="text-4xl">⚠️</div>
      <p class="text-sm text-gray-500">LINEログインの確認に失敗しました。リッチメニューからこのページを開き直してください。</p>`);
    return;
  }

  if (!liff.isLoggedIn()) {
    // 修正理由: このページ(/)はLIFFのエンドポイントURLとして登録されていない
    // （登録先は/liff/review）。ここでliff.login()を呼ぶとredirectUriの検証に
    // 失敗し400 Bad Requestになるため、in-client/外部ブラウザを問わず必ず
    // エンドポイントURL(/liff/review)へ遷移し、そちらでログインを完結させる
    // （2026-08-30 外部ブラウザ経由で発覚。2026-09-08 liff.logout()後の
    //  in-clientでも同じ400が出るため isInClient の特例分岐を廃止）
    location.replace('/liff/review' + location.search);
    return;
  }

  let idToken;
  try {
    idToken = await liff.getIDToken();
  } catch (e) {
    idToken = null;
  }
  if (!idToken) {
    showOverlay(`<div class="text-4xl">⚠️</div>
      <p class="text-sm text-gray-500">ログイン情報の取得に失敗しました。もう一度お試しください。</p>`);
    return;
  }
  if (idTokenExpired(idToken)) {
    if (forceReauthAndReload('expired', 'page_load', idToken)) return;
    showOverlay(`<div class="text-4xl">⚠️</div>
      <p class="text-sm text-gray-500">ログイン情報の有効期限が切れています。リッチメニューからこのページを開き直してください。</p>`);
    return;
  }

  let d;
  try {
    const res = await fetch('/api/profile/prefill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: idToken }),
    });
    d = await res.json();
  } catch (e) {
    showOverlay(`<div class="text-4xl">⚠️</div>
      <p class="text-sm text-gray-500">通信に失敗しました。もう一度お試しください。</p>`);
    return;
  }

  if (d.banned) {
    // BAN中はフォームを開かせず、そのままブロックし続ける（hideOverlayを呼ばない）
    showOverlay(`<div class="text-4xl">🚫</div>
      <p class="text-sm text-gray-500">現在、このアカウントはご利用を停止しております。心当たりがある場合は<a href="/contact" class="underline hover:text-indigo-500">お問い合わせフォーム</a>よりご連絡ください。</p>`);
    return;
  }

  if (d.auth_failed) {
    // 修正理由: LINE側のID token検証が一時的に失敗しただけなのに、
    // 会員登録済みの本人を「未登録」と誤判定していたバグの修正
    // (2026-08-31)。期限切れトークンが原因のことが多いので、まず
    // logout→再ログインで自動復帰を試み、ループ防止で復帰不能なときだけ手動導線を出す。
    if (forceReauthAndReload('auth_failed', 'page_load', idToken)) return;
    showOverlay(`<div class="text-4xl">⚠️</div>
      <p class="text-sm text-gray-500">ログイン確認に失敗しました。もう一度お試しください。</p>
      <button onclick="location.reload()" class="block w-full text-center bg-indigo-600 text-white font-bold py-2.5 rounded-xl text-sm">🔄 再読み込み</button>`);
    return;
  }

  if (!d.found) {
    const registerBase = REGISTER_LIFF_ID ? `https://liff.line.me/${REGISTER_LIFF_ID}` : '/register';
    // ?uid= は登録画面を開いた人数の集計用（bot経由の案内リンクと同じ形。core/funnel.py）
    const registerUrl = d.uid ? `${registerBase}?uid=${encodeURIComponent(d.uid)}` : registerBase;
    showOverlay(`<div class="text-4xl">🎓</div>
      <p class="text-sm text-gray-600">⚠️ レビュー投稿には会員登録が必要です</p>
      <p class="text-xs text-gray-400">お名前・学籍番号・学部・学科を入力するだけ（30秒で完了）</p>
      <a href="${registerUrl}" class="block text-center bg-indigo-600 text-white font-bold py-2.5 rounded-xl text-sm">📝 今すぐ登録する（30秒）</a>
      <p class="text-xs text-gray-400">登録することで<a href="/terms" target="_blank" class="underline hover:text-indigo-500">利用規約</a>および<a href="/privacy" target="_blank" class="underline hover:text-indigo-500">プライバシーポリシー</a>に同意したものとします。</p>`);
    return;
  }

  const sidInput = document.getElementById('student_id');
  if (d.student_id) {
    sidInput.value = d.student_id;
    sidInput.readOnly = true;
    sidInput.classList.add('bg-gray-50', 'text-gray-500', 'cursor-not-allowed');
  }
  if (d.group) {
    // 所属団体の番号を入力済みにする（毎回入力しなくてよい）。団体に数えるのは番号が入った状態で
    // 投稿したレビューだけなので、本人が欄を消して投稿すればそのレビューは団体に数えない
    if (d.group.active) {
      document.getElementById('group_code').value = d.group.code || '';
      setGroupStatus('ok', `✅ ${d.group.name} として投稿します`);
    } else {
      setGroupStatus('member', `🤝 ${d.group.name}（現在は受付を終了しています）`);
    }
  }
  if (Array.isArray(d.reviewed_pairs)) {
    _reviewedSet = new Set(d.reviewed_pairs.map(([cid, name]) => cid + '::' + name));
  }
  // 会員登録した学部・学科を控え、その学部の専門科目を含むプリロードを取得する
  _profile = { faculty: d.faculty || '', department: d.department || '' };
  await loadPreload();
  document.getElementById('idTokenHidden').value = idToken;
  LiffAuth.clearFlag(idToken);
  hideOverlay();
}
// プリフィルが（成功・失敗いずれでも）決着したら、?course=自動選択とフォールバック検索を解禁する。
// プリフィルが途中で失敗して _profile が確定しなかった場合でも、教養科目ぶん（学部未指定）の
// プリロードだけは取得しておく。これをしないと _preload が永久に null のままになり、
// 検索欄のキーストロークごとに /api/courses を叩き続けることになる。
initProfilePrefill().finally(() => {
  _prefillResolve();
  if (!_preload) loadPreload();
});

const RATING_LABELS = ['', 'ほぼ学びなし 😔', 'ちょい学び 🙂', 'まあまあ学びあり 😊', 'かなり学べた 😄', 'めちゃくちゃ学べた 🤩'];
const EASE_VALS   = ['', 'C', 'B', 'A', 'S', 'SS'];
const EASE_LABELS = ['', '修羅場 😱', '大変 😤', '標準 😐', '楽々 😌', '天国 😇'];
let currentRating = 0;
let currentEase = 0;

function buildStars() {
  const c = document.getElementById('starContainer');
  c.innerHTML = '';
  for (let i = 1; i <= 5; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = i <= currentRating ? '★' : '☆';
    btn.className = 'text-5xl transition-all hover:scale-110 active:scale-95 ' +
      (i <= currentRating ? 'text-purple-400' : 'text-gray-200');
    btn.style.touchAction = 'manipulation';
    btn.style.webkitTapHighlightColor = 'transparent';
    btn.onclick = () => {
      currentRating = i;
      document.getElementById('ratingHidden').value = i;
      document.getElementById('ratingLabel').textContent = RATING_LABELS[i];
      document.getElementById('ratingError').classList.add('hidden');
      buildStars();
      saveData();
    };
    c.appendChild(btn);
  }
}

function buildEaseStars() {
  const c = document.getElementById('easeStarContainer');
  c.innerHTML = '';
  for (let i = 1; i <= 5; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = i <= currentEase ? '★' : '☆';
    btn.className = 'text-5xl transition-all hover:scale-110 active:scale-95 ' +
      (i <= currentEase ? 'text-yellow-400' : 'text-gray-200');
    btn.style.touchAction = 'manipulation';
    btn.style.webkitTapHighlightColor = 'transparent';
    btn.onclick = () => {
      currentEase = i;
      document.getElementById('easeHidden').value = EASE_VALS[i];
      document.getElementById('easeLabel').textContent = EASE_LABELS[i];
      document.getElementById('easeError').classList.add('hidden');
      buildEaseStars();
      saveData();
    };
    c.appendChild(btn);
  }
}

document.querySelectorAll('.chip-btn').forEach(btn => {
  btn.onclick = () => {
    if (btn.classList.contains('single')) {
      const wasActive = btn.classList.contains('active');
      document.querySelectorAll(`[data-group="${btn.dataset.group}"]`).forEach(b => b.classList.remove('active'));
      if (!wasActive) btn.classList.add('active');
    } else {
      btn.classList.toggle('active');
    }
    const errMap = { attendance: 'attendanceError', attendance_method: 'attendanceMethodError', attendance_surprise: 'attendanceSurpriseError', homework: 'homeworkError', homework_frequency: 'homeworkFrequencyError', class_format: 'classFormatError' };
    if (errMap[btn.dataset.group]) document.getElementById(errMap[btn.dataset.group]).classList.add('hidden');
    if (btn.dataset.value === '__custom__') {
      const customInput = document.getElementById('evalCustomInput');
      if (btn.classList.contains('active')) {
        customInput.classList.remove('hidden');
        customInput.focus();
      } else {
        customInput.classList.add('hidden');
        customInput.value = '';
      }
    }
    if (btn.dataset.value === '__format_custom__') {
      const formatInput = document.getElementById('formatCustomInput');
      if (btn.classList.contains('active')) {
        formatInput.classList.remove('hidden');
        formatInput.focus();
      } else {
        formatInput.classList.add('hidden');
        formatInput.value = '';
      }
    }
    if (btn.dataset.value === '__attendance_method_custom__') {
      const methodInput = document.getElementById('attendanceMethodCustomInput');
      if (btn.classList.contains('active')) {
        methodInput.classList.remove('hidden');
        methodInput.focus();
      } else {
        methodInput.classList.add('hidden');
        methodInput.value = '';
      }
    }
    if (btn.dataset.group === 'attendance') {
      const methodSection = document.getElementById('attendanceMethodSection');
      const noneConfirm = document.getElementById('attendanceNoneConfirm');
      if (btn.dataset.value === 'なし') {
        methodSection.classList.add('hidden');
        document.querySelectorAll('[data-group="attendance_method"]').forEach(b => b.classList.remove('active'));
        document.getElementById('attendanceMethodCustomInput').classList.add('hidden');
        document.getElementById('attendanceMethodCustomInput').value = '';
        document.getElementById('attendanceMethodError').classList.add('hidden');
        noneConfirm.classList.remove('hidden');
      } else {
        methodSection.classList.remove('hidden');
        noneConfirm.classList.add('hidden');
      }
      const surpriseSection = document.getElementById('attendanceSurpriseSection');
      if (btn.dataset.value === 'たまにあり') {
        surpriseSection.classList.remove('hidden');
      } else {
        surpriseSection.classList.add('hidden');
        document.querySelectorAll('[data-group="attendance_surprise"]').forEach(b => b.classList.remove('active'));
        document.getElementById('attendanceSurpriseError').classList.add('hidden');
      }
    }
    if (btn.dataset.group === 'homework_frequency') {
      const homeworkSection = document.getElementById('homeworkSection');
      if (btn.dataset.value === '毎授業' || btn.dataset.value === '数回') {
        homeworkSection.classList.remove('hidden');
      } else {
        homeworkSection.classList.add('hidden');
        document.querySelectorAll('[data-group="homework"]').forEach(b => b.classList.remove('active'));
        document.getElementById('homeworkExtraInput').value = '';
        document.getElementById('homeworkError').classList.add('hidden');
      }
    }
    if (PERCENT_GROUPS[btn.dataset.group]) renderGroupPercents(btn.dataset.group);
    updateGradingMethod();
    saveData();
  };
});

['evalCustomInput', 'formatCustomInput', 'attendanceMethodCustomInput'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    if (id === 'formatCustomInput') renderGroupPercents('class_format');
    updateGradingMethod();
    saveData();
  });
});
document.getElementById('evalCustomInput').addEventListener('input', () => {
  if (document.getElementById('evalCustomInput').value.trim()) {
    document.getElementById('evalCustomError').classList.add('hidden');
    document.getElementById('evalCustomInput').classList.remove('border-red-400');
  }
});
document.getElementById('formatCustomInput').addEventListener('input', () => {
  if (document.getElementById('formatCustomInput').value.trim()) {
    document.getElementById('formatCustomError').classList.add('hidden');
    document.getElementById('formatCustomInput').classList.remove('border-red-400');
  }
});
document.getElementById('attendanceMethodCustomInput').addEventListener('input', () => {
  if (document.getElementById('attendanceMethodCustomInput').value.trim()) {
    document.getElementById('attendanceMethodCustomError').classList.add('hidden');
    document.getElementById('attendanceMethodCustomInput').classList.remove('border-red-400');
  }
});

['classFormatExtraInput', 'attendanceMethodExtraInput',
 'homeworkFrequencyExtraInput', 'homeworkExtraInput', 'evalExtraInput'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    updateGradingMethod();
    saveData();
  });
});

document.getElementById('reviewForm').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.type !== 'submit') {
    e.preventDefault();
  }
});

document.querySelectorAll('[data-group="academic_year"]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.getElementById('academic_year').value = btn.dataset.value;
  });
});

const PERCENT_GROUPS = {
  class_format: {
    containerId: 'formatPercentContainer', barId: 'formatSliderBar', trackId: 'formatSliderTrack',
    listId: 'formatPercentList', errorId: 'formatPercentError', customInputId: 'formatCustomInput',
    customValue: '__format_custom__', dragged: false,
  },
};

function partsForGroup(group) {
  const cfg = PERCENT_GROUPS[group];
  const btns = [...document.querySelectorAll(`[data-group="${group}"].active`)];
  const percents = {};
  document.querySelectorAll(`.percent-input[data-slider-group="${group}"]`).forEach(inp => {
    percents[inp.dataset.percentFor] = inp.value.trim();
  });
  return btns.map(b => {
    const label = b.dataset.value === cfg.customValue
      ? (document.getElementById(cfg.customInputId).value.trim() || null)
      : b.dataset.value;
    if (!label) return null;
    const pct = btns.length >= 2 ? percents[b.dataset.value] : '';
    return pct ? `${label}(${pct}%)` : label;
  }).filter(Boolean);
}

function withExtraText(text, extraInputId) {
  const extra = document.getElementById(extraInputId).value.trim();
  return extra ? `${text}(補足:${extra})` : text;
}

// grading_methodは[{"label","text"}, ...]のJSON配列として保存する（core/grading_method.py参照）。
// 修正理由: 旧実装は' / '・':'・'・'・括弧が入れ子になった独自区切り文字列を組み立てており、
// 補足欄（ユーザー自由記述）にこれらの記号が含まれると表示側のパースが壊れていたため、
// JSONで構造化してlabel/textを分離する（textの中身は表示専用の不透明な文字列として扱う）
function updateGradingMethod() {
  const parts = [];
  const formats = partsForGroup('class_format');
  if (formats.length) parts.push({ label: '形式', text: withExtraText(formats.join('・'), 'classFormatExtraInput') });
  const att = document.querySelector('[data-group="attendance"].active');
  if (att) parts.push({ label: '出席', text: att.dataset.value });
  if (att && att.dataset.value === 'たまにあり') {
    const surprise = document.querySelector('[data-group="attendance_surprise"].active');
    if (surprise) parts.push({ label: '出席確認', text: surprise.dataset.value });
  }
  if (att && att.dataset.value !== 'なし') {
    const attMethods = [...document.querySelectorAll('[data-group="attendance_method"].active')].map(b => {
      return b.dataset.value === '__attendance_method_custom__'
        ? (document.getElementById('attendanceMethodCustomInput').value.trim() || null)
        : b.dataset.value;
    }).filter(Boolean);
    if (attMethods.length) parts.push({ label: '出席確認方法', text: withExtraText(attMethods.join('・'), 'attendanceMethodExtraInput') });
  }
  const hw = document.querySelector('[data-group="homework"].active');
  if (hw) parts.push({ label: '課題', text: withExtraText(hw.dataset.value, 'homeworkExtraInput') });
  const hwFreq = document.querySelector('[data-group="homework_frequency"].active');
  if (hwFreq) parts.push({ label: '頻度', text: withExtraText(hwFreq.dataset.value, 'homeworkFrequencyExtraInput') });
  // 評価方法は割合入力なし（2026-09-26廃止）。選択されたラベルのみ
  const evals = [...document.querySelectorAll('[data-group="eval"].active')].map(b => {
    return b.dataset.value === '__custom__'
      ? (document.getElementById('evalCustomInput').value.trim() || null)
      : b.dataset.value;
  }).filter(Boolean);
  if (evals.length) parts.push({ label: '評価', text: withExtraText(evals.join('・'), 'evalExtraInput') });
  document.getElementById('gradingMethodHidden').value = JSON.stringify(parts);
}

const EVAL_SLIDER_COLORS = ['#6366f1', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#8b5cf6', '#ef4444', '#14b8a6'];

function renderGroupPercents(group, overridePercents) {
  const cfg = PERCENT_GROUPS[group];
  const activeBtns = [...document.querySelectorAll(`[data-group="${group}"].active`)];
  const container = document.getElementById(cfg.containerId);
  const trackEl = document.getElementById(cfg.trackId);
  const legendEl = document.getElementById(cfg.listId);
  if (activeBtns.length < 2) {
    container.classList.add('hidden');
    trackEl.innerHTML = '';
    legendEl.innerHTML = '';
    document.getElementById(cfg.errorId).classList.add('hidden');
    return;
  }
  const existing = {};
  document.querySelectorAll(`.percent-input[data-slider-group="${group}"]`).forEach(inp => {
    existing[inp.dataset.percentFor] = parseFloat(inp.value);
  });
  const source = overridePercents || existing;
  container.classList.remove('hidden');

  const vals = activeBtns.map(b => b.dataset.value);
  const labels = {};
  activeBtns.forEach(b => {
    labels[b.dataset.value] = b.dataset.value === cfg.customValue
      ? (document.getElementById(cfg.customInputId).value.trim() || 'その他')
      : b.dataset.value;
  });

  let percents = vals.map(v => parseFloat(source[v]));
  if (!percents.every(p => Number.isFinite(p) && p > 0)) {
    const base = Math.floor(100 / vals.length);
    percents = vals.map(() => base);
    percents[percents.length - 1] += 100 - base * vals.length;
  } else {
    const sum = percents.reduce((a, b) => a + b, 0);
    if (sum !== 100) {
      percents = percents.map(p => Math.round(p / sum * 100));
      percents[percents.length - 1] += 100 - percents.reduce((a, b) => a + b, 0);
    }
  }

  buildSliderBar(group, vals, labels, percents);
}

function buildSliderBar(group, vals, labels, percents) {
  const cfg = PERCENT_GROUPS[group];
  const trackEl = document.getElementById(cfg.trackId);
  const barEl = document.getElementById(cfg.barId);
  const legendEl = document.getElementById(cfg.listId);
  trackEl.innerHTML = '';
  barEl.querySelectorAll('.eval-slider-divider').forEach(d => d.remove());
  legendEl.innerHTML = '';

  vals.forEach((val, i) => {
    const color = EVAL_SLIDER_COLORS[i % EVAL_SLIDER_COLORS.length];
    const seg = document.createElement('div');
    seg.className = 'eval-slider-segment';
    seg.style.background = color;
    seg.style.flexBasis = percents[i] + '%';
    seg.dataset.percentFor = val;
    seg.textContent = percents[i] + '%';
    trackEl.appendChild(seg);

    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.className = 'percent-input';
    hidden.dataset.percentFor = val;
    hidden.dataset.sliderGroup = group;
    hidden.value = percents[i];
    legendEl.appendChild(hidden);

    const row = document.createElement('div');
    row.className = 'flex items-center gap-1.5 text-xs text-gray-600';
    row.innerHTML = `<span class="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0" style="background:${color}"></span><span class="flex-1">${escapeHtml(labels[val])}</span>`;
    legendEl.appendChild(row);
  });

  // 区切り線（隣接する2項目の割合をドラッグで調整）
  let cumulative = 0;
  const cumulatives = percents.map(p => (cumulative += p));
  for (let i = 0; i < vals.length - 1; i++) {
    const divider = document.createElement('div');
    divider.className = 'eval-slider-divider' + (cfg.dragged ? '' : ' hint');
    divider.style.left = cumulatives[i] + '%';
    divider.dataset.index = i;
    barEl.appendChild(divider);
    attachDividerDrag(group, divider, i, vals);
  }
}

function attachDividerDrag(group, divider, index, vals) {
  const cfg = PERCENT_GROUPS[group];
  const barEl = document.getElementById(cfg.barId);
  const onMove = (clientX) => {
    const rect = barEl.getBoundingClientRect();
    const inputs = vals.map(v => document.querySelector(`.percent-input[data-slider-group="${group}"][data-percent-for="${v}"]`));
    const percents = inputs.map(inp => parseFloat(inp.value));
    let cumulative = 0;
    const cumulatives = percents.map(p => (cumulative += p));
    const prevBoundary = index === 0 ? 0 : cumulatives[index - 1];
    const nextBoundary = cumulatives[index + 1];
    let newBoundary = ((clientX - rect.left) / rect.width) * 100;
    newBoundary = Math.max(prevBoundary + 1, Math.min(nextBoundary - 1, Math.round(newBoundary)));

    percents[index] = newBoundary - prevBoundary;
    percents[index + 1] = nextBoundary - newBoundary;

    inputs[index].value = percents[index];
    inputs[index + 1].value = percents[index + 1];
    divider.style.left = newBoundary + '%';

    const track = document.getElementById(cfg.trackId);
    track.children[index].style.flexBasis = percents[index] + '%';
    track.children[index].textContent = percents[index] + '%';
    track.children[index + 1].style.flexBasis = percents[index + 1] + '%';
    track.children[index + 1].textContent = percents[index + 1] + '%';

    document.getElementById(cfg.errorId).classList.add('hidden');
    updateGradingMethod();
    saveData();
  };
  const onPointerMove = (e) => onMove(e.touches ? e.touches[0].clientX : e.clientX);
  const onPointerUp = () => {
    document.removeEventListener('mousemove', onPointerMove);
    document.removeEventListener('mouseup', onPointerUp);
    document.removeEventListener('touchmove', onPointerMove);
    document.removeEventListener('touchend', onPointerUp);
  };
  divider.addEventListener('mousedown', (e) => {
    e.preventDefault();
    cfg.dragged = true;
    divider.classList.remove('hint');
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('mouseup', onPointerUp);
  });
  divider.addEventListener('touchstart', (e) => {
    e.preventDefault();
    cfg.dragged = true;
    divider.classList.remove('hint');
    document.addEventListener('touchmove', onPointerMove, { passive: false });
    document.addEventListener('touchend', onPointerUp);
  }, { passive: false });
}

let searchMode = 'course';

document.getElementById('modeCourseBtn').addEventListener('click', () => {
  if (searchMode === 'course') return;
  searchMode = 'course';
  document.getElementById('modeCourseBtn').style.cssText = 'background:#6366f1;color:#fff;flex:1;padding:8px;font-size:14px;font-weight:600;transition:all .15s';
  document.getElementById('modeInstructorBtn').style.cssText = 'background:#fff;color:#6b7280;flex:1;padding:8px;font-size:14px;font-weight:600;transition:all .15s';
  document.getElementById('courseModeSection').classList.remove('hidden');
  document.getElementById('instructorModeSection').classList.add('hidden');
  document.getElementById('courseNameHidden').value = '';
  document.getElementById('selectedInstructorHidden').value = '';
  document.getElementById('selectedCourse').classList.add('hidden');
  document.getElementById('courseNameLabelInstr').classList.add('hidden');
  document.getElementById('selectedInstructorDisplay').classList.add('hidden');
  document.getElementById('instructorSelectSection').classList.add('hidden');
  syncSyllabusLink();
  searchInput.classList.remove('hidden');
  searchInput.value = '';
});

document.getElementById('modeInstructorBtn').addEventListener('click', () => {
  if (searchMode === 'instructor') return;
  searchMode = 'instructor';
  document.getElementById('modeInstructorBtn').style.cssText = 'background:#6366f1;color:#fff;flex:1;padding:8px;font-size:14px;font-weight:600;transition:all .15s';
  document.getElementById('modeCourseBtn').style.cssText = 'background:#fff;color:#6b7280;flex:1;padding:8px;font-size:14px;font-weight:600;transition:all .15s';
  document.getElementById('instructorModeSection').classList.remove('hidden');
  document.getElementById('courseModeSection').classList.add('hidden');
  document.getElementById('courseNameHidden').value = '';
  document.getElementById('selectedInstructorHidden').value = '';
  document.getElementById('selectedCourse').classList.add('hidden');
  document.getElementById('instructorSelectSection').classList.add('hidden');
  document.getElementById('courseNameLabelInstr').classList.add('hidden');
  syncSyllabusLink();
  document.getElementById('instructorSearchInput').value = '';
  document.getElementById('instructorCourseList').classList.add('hidden');
});

const searchInput = document.getElementById('courseSearchInput');
const dropdown = document.getElementById('courseDropdown');

// 募集終了バッジ（オンデマンド配信など、件数と無関係に締め切った科目×教員に付ける）。
// 2026-09-08に「科目×教員の投稿上限」を撤廃したため「残り○枠」は表示しない
// （full=true になるのは closed のケースのみ）。
function remainingBadgeHtml(remaining) {
  if (remaining == null) return '';
  if (remaining <= 0) {
    return `<span style="font-size:11px;font-weight:700;border-radius:999px;padding:4px 10px;margin-left:6px;background:#f3f4f6;color:#9ca3af;white-space:nowrap">募集終了</span>`;
  }
  return '';
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// 科目名候補の行を科目カテゴリで淡く塗り分ける（教養科目＝青系 / 専門科目＝黄系）。
// 表示順も教養科目→専門科目の順に並べ替える。
function _catRowBg(cat) {
  if (cat === SUBMISSION_KYOYO_CATEGORY) return '#eff6ff';
  if (cat === SUBMISSION_SENMON_CATEGORY) return '#fefce8';
  return '';
}
function _catRowOrder(cat) {
  if (cat === SUBMISSION_KYOYO_CATEGORY) return 0;
  if (cat === SUBMISSION_SENMON_CATEGORY) return 1;
  return 2;
}

// ── プリロードキャッシュ ──
let _preload = null;
// 専門科目の候補は会員登録した学部で変わるため、プリフィル（_profile確定）を待ってから取得する。
// フォーム本体は会員登録ゲートのオーバーレイで覆われており、その解除前に検索は行われない。
// 修正理由: res.okを確認せずJSON化していたため、サーバーエラー時に
// {"detail": "..."}のような形の異なるレスポンスが_preloadに入り、
// 検索欄に1文字入力した瞬間 `_preload.courses.filter` がTypeErrorで無言クラッシュしていた。
// res.ok以外なら_preloadをnullのままにし、既存の/api/coursesフォールバックへ委ねる。
function _facultyQS(prefix) {
  const f = (_profile && _profile.faculty || '').trim();
  return f ? (prefix + 'faculty=' + encodeURIComponent(f)) : '';
}
// オンデマンド配信科目（担当教員によらず内容が同一）の科目id集合。
// 募集締切扱いはしないが、担当教員チップの注記を「どちらを選んでもかまいません」に切り替える。
let _onDemandCids = new Set();
function _collectOnDemand(d) {
  for (const c of (d.courses || [])) if (c.on_demand) _onDemandCids.add(c.id);
  for (const inst of (d.instructors || [])) for (const c of (inst.courses || [])) if (c.on_demand) _onDemandCids.add(c.id);
}
function loadPreload() {
  return fetch('/api/preload' + _facultyQS('?'))
    .then(r => r.ok ? r.json() : Promise.reject(new Error(r.status)))
    .then(d => { _preload = d; _collectOnDemand(d); })
    .catch(() => {});
}

function _normStr(s) { return s.replace(/[・･（）()]/g, '').toLowerCase(); }

// 語尾の数字・アルファベットのみが異なる科目（例: 生物学各論A1/A2/C1/C2）を
// 検索候補として1件にまとめ、担当教員は全変種分をマージした一覧として提示する。
// どの変種になるかは教員選択時に自動で解決する（変種そのものはユーザーに意識させない）。
function _groupCourseItems(items) {
  // グループ化キーはサーバーが決める（variantGroupKey）。専門科目は管理画面と同じ
  // compute_variant_display_groups() 単位のラベルをキーにするため、"線形代数(1/2)" と
  // "線形代数(1/2)(再履修)" のようにベース名が同じでも別グループに分かれる。教養科目は
  // 従来どおりベース名＋遠隔/対面フラグ（サーバー未提供時のフォールバックも同式）。
  const groupKey = c => c.variantGroupKey || (c.variantGroup ? c.variantGroup + (c.isRemote ? ' remote' : '') : '');
  const byGroup = new Map();
  for (const c of items) {
    const k = groupKey(c);
    if (!k) continue;
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k).push(c);
  }
  const emitted = new Set();
  const result = [];
  for (const c of items) {
    const key = groupKey(c) || null;
    const members = key ? byGroup.get(key) : null;
    if (!members || members.length < 2) { result.push(c); continue; }
    if (emitted.has(key)) continue;
    // 同じ教員が複数の変種を担当していても、教員名は1回だけ表示する
    // （最初に見つかった変種の科目へ解決する。表示済みのレビューは科目詳細ページ側で
    // グループ全体分をまとめて見せているため、どの変種に紐づけても実害は無い）
    const mergedInstructors = [];
    const seenNames = new Set();
    for (const m of members) {
      for (const inst of (m.instructors || [])) {
        if (seenNames.has(inst.name)) continue;
        seenNames.add(inst.name);
        mergedInstructors.push({ ...inst, courseId: m.id, courseName: m.name });
      }
    }
    if (mergedInstructors.length === 0) { result.push(c); continue; } // 教員未登録で解決不能な場合は統合しない
    emitted.add(key);
    // 専門科目は管理画面と同じ完全なグループラベル（variantGroupLabel）をそのまま表示名に
    // 使う。教養科目はサーバーがラベルを持たないため、従来どおりベース名＋接尾辞を組み立てる。
    const exactLabel = members[0].variantGroupLabel || c.variantGroupLabel || '';
    result.push({
      id: null,
      name: exactLabel || c.variantGroup,
      isGroup: true,
      exactLabel: !!exactLabel,
      category: c.category || '',
      variantSuffixes: exactLabel ? [] : members.map(m => m.name.startsWith(c.variantGroup) ? m.name.slice(c.variantGroup.length) : m.name),
      instructors: mergedInstructors,
    });
  }
  return result;
}

function _showCourseDropdown(items, q) {
  if (items.length === 0) {
    dropdown.innerHTML = `<div class="px-4 py-4 text-sm text-gray-500">候補がありません</div>`;
    dropdown.classList.remove('hidden');
    return;
  }
  // 全担当教員が募集締切なら、この科目は募集を完全に終了しているものとして扱う
  const _closedCourse = c => c.instructors.length > 0 && c.instructors.every(i => i.full);
  // 表示順は 教養 → 専門 → 募集終了。募集終了は最後にまとめ、その中でも教養→専門を保つ
  // （同カテゴリ・同状態の中では元の並びを維持）
  const sorted = items.slice().sort((a, b) =>
    (_closedCourse(a) ? 1 : 0) - (_closedCourse(b) ? 1 : 0)
    || _catRowOrder(a.category) - _catRowOrder(b.category));
  dropdown.innerHTML = sorted.slice(0, 30).map((c, idx) => {
    const bg = _catRowBg(c.category);
    // 募集を完全に終了した科目は検索候補の行に斜線＋「募集終了」を表示する
    const closed = _closedCourse(c);
    return `<div class="px-4 py-3 cursor-pointer" data-idx="${idx}"
      style="border-bottom:1px solid #f3f4f6;transition:background .1s;background:${bg}"
      onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='${bg}'">
      <div style="font-size:14px;font-weight:600;color:#1e293b${closed ? ';text-decoration:line-through;opacity:.55' : ''}">${escapeHtml(c.name)}${
        c.isGroup && c.variantSuffixes && c.variantSuffixes.length ? ` <span style="color:#94a3b8;font-weight:400">(${escapeHtml(c.variantSuffixes.join('/'))})</span>` : ''
      }${closed ? remainingBadgeHtml(0) : ''}</div>
      ${c.instructors.length
        ? `<div style="font-size:12px;color:#94a3b8;margin-top:2px">担当教員 ${c.instructors.length}名</div>`
        : ''}
    </div>`;
  }).join('');
  dropdown.classList.remove('hidden');
  dropdown.querySelectorAll('[data-idx]').forEach(el => {
    const c = sorted[parseInt(el.dataset.idx)];
    el.addEventListener('click', () => selectCourse(c.name, c.instructors, c.id, !!c.isGroup, c.variantSuffixes));
  });
}

function searchCourses(q) {
  document.getElementById('courseNameHidden').value = '';
  if (!q) { dropdown.classList.add('hidden'); return; }
  if (_preload) {
    const tokens = q.trim().split(/[\s　]+/).filter(Boolean).map(t => t.toLowerCase());
    const normTokens = tokens.map(_normStr);
    const items = _preload.courses.filter(c => {
      if (!_isSubmittable(c)) return false;
      const name = c.name.toLowerCase();
      const reading = (c.reading || '').toLowerCase();
      const normName = _normStr(c.name);
      return tokens.every((tok, i) =>
        name.includes(tok) || reading.includes(tok) || normName.includes(normTokens[i])
      );
    });
    _showCourseDropdown(_groupCourseItems(items), q);
  } else {
    fetch(`/api/courses?q=${encodeURIComponent(q)}${_facultyQS('&')}`)
      .then(r => r.json())
      .then(d => { _collectOnDemand(d); _showCourseDropdown((d.courses || []).filter(_isSubmittable), q); })
      .catch(() => {});
  }
}

searchInput.addEventListener('compositionend', () => {
  searchCourses(searchInput.value.trim());
});

searchInput.addEventListener('input', () => {
  searchCourses(searchInput.value.trim());
});

let currentInstructors = [];
let currentCourseId = null;
const chipsEl = document.getElementById('instructorChips');

// ── 外部ブラウザ導線（うりぼーポータル / 科目×教員のシラバス） ──
// LINE内蔵ブラウザではうりぼーポータル等のログインが通らないため、必ず外部ブラウザで開く。
const URIBO_PORTAL_URL = 'https://www.uriboportal.ofc.kobe-u.ac.jp/';
function openExternalUrl(url) {
  if (!url) return;
  try {
    if (typeof liff !== 'undefined' && liff.openWindow) {
      liff.openWindow({ url, external: true });
      return;
    }
  } catch (e) {}
  window.open(url, '_blank', 'noopener');
}
document.getElementById('uriboPortalBtn').addEventListener('click', () => openExternalUrl(URIBO_PORTAL_URL));

const syllabusLinkBtn = document.getElementById('syllabusLinkBtn');
let _currentSyllabusUrl = '';
syllabusLinkBtn.addEventListener('click', () => openExternalUrl(_currentSyllabusUrl));
// シラバス導線の表示は「今アクティブな担当教員チップの data-syllabus-url」だけで決まる。
// 以前は科目/教員の選択・クリア・下書き復元の各所で showSyllabusLink('') を個別に
// 呼んでおり、リセット経路を1つ追加するたびに呼び忘れて古いボタンが残るバグの温床だった。
// 現在の状態から毎回導出する syncSyllabusLink() に一本化し、saveData()（全状態変更で走る）
// からも呼ぶことで、経路ごとの明示呼び出しへの依存をなくす。
function syncSyllabusLink() {
  // 担当教員が選ばれていなければ（＝ selectedInstructorHidden が空。モード切替・クリアで
  // 必ずクリアされる）シラバス導線は出さない。選ばれていれば、その教員チップに載っている
  // data-syllabus-url を使う（未登録・オムニバスなら空文字でボタンは隠れる）。
  const hasInstr = !!document.getElementById('selectedInstructorHidden').value;
  // アクティブチップは「今のモードの容器」からだけ拾う。#instructorChips（科目名モード）と
  // #instructorCourseItems（先生名モード）の両方を1つのセレクタで見ると、モードを跨いだ際に
  // もう片方の容器へ古い .active チップが残ったままになり（各ハンドラは相手側の容器を
  // クリアしない）、querySelector が DOM 出現順で先にある #instructorCourseItems 側の
  // 古いチップを拾って、別科目のシラバスに飛ぶバグになる。各容器は科目/教員選択のたびに
  // innerHTML ごと再描画されるので、現モードの容器だけ見れば取り違えは起きない。
  const containerSel = searchMode === 'course' ? '#instructorChips' : '#instructorCourseItems';
  const active = document.querySelector(containerSel + ' .chip-btn.active');
  _currentSyllabusUrl = (hasInstr && active && active.dataset.syllabusUrl) || '';
  syllabusLinkBtn.classList.toggle('hidden', !_currentSyllabusUrl);
}

function selectCourse(name, instructors, courseId, isGroup, variantSuffixes) {
  currentInstructors = instructors || [];
  // 統合科目（isGroup）は教員を選ぶまでどの変種か確定しないため、courseNameHiddenは
  // 一旦空にしておき（送信バリデーションで弾かれる）、教員選択時に実際の科目名へ差し替える
  currentCourseId = isGroup ? null : (courseId ?? null);
  // オンデマンド配信科目（担当教員によらず内容が同一）は「担当教員選択は不要」と案内する
  const _isOnDemand = _onDemandCids.has(courseId)
    || currentInstructors.some(i => (i.courseId != null) && _onDemandCids.has(i.courseId));
  document.getElementById('courseNameHidden').value = isGroup ? '' : name;
  // 統合科目は教員選択後も「英語科教育論(A1/A2/B1/B2)」のようにグループ全体の表記を
  // 表示し続ける（実際にどの変種に紐づいたか＝courseNameHiddenの値をユーザーに
  // 意識させない設計のため。selectCourse呼び出し時点でのisGroup/variantSuffixesを
  // クロージャで保持し、下の教員クリックハンドラでも使い回す）
  const displayName = (isGroup && variantSuffixes && variantSuffixes.length)
    ? `${name}(${variantSuffixes.join('/')})` : name;
  document.getElementById('selectedCourseName').textContent = displayName;
  document.getElementById('selectedCourse').classList.remove('hidden');
  document.getElementById('courseNameLabelInstr').classList.remove('hidden');
  if (searchMode === 'course') {
    searchInput.classList.add('hidden');
    document.getElementById('instructorSelectSection').classList.remove('hidden');
  }
  dropdown.classList.add('hidden');
  document.getElementById('courseError').classList.add('hidden');
  document.getElementById('selectedInstructorHidden').value = '';
  document.getElementById('instructorError').classList.add('hidden');
  document.getElementById('instructorUnregistered').classList.add('hidden');
  document.getElementById('instructorLimitNotice').classList.add('hidden');
  syncSyllabusLink();  // 新しい科目を選んだ直後は selectedInstructorHidden 空＝シラバス導線は隠れる

  // 担当教員チップは常に表示する（末尾のオムニバス擬似候補は全科目で選べるため、
  // 登録教員が0人の科目でも「募集していません」ではなくオムニバスで投稿できる）
  chipsEl.classList.remove('hidden');
  const _instrHint = document.getElementById('instructorHint');
  // オンデマンド配信科目は「タップして選択」ではなく、どの教員でも構わない旨だけを案内する
  _instrHint.textContent = _isOnDemand
    ? 'この科目は担当教員はどの先生を選んでもかまいません。'
    : '👆 担当教員をタップして選択してください';
  _instrHint.classList.remove('hidden');
  const withReviewed = currentInstructors.map(inst => {
    const cid = inst.courseId ?? currentCourseId;
    return {
      ...inst,
      _cid: cid,
      _cname: inst.courseName ?? name,
      reviewed: !inst.full && cid != null && _reviewedSet.has(cid + '::' + inst.name),
    };
  });
  // チーム開講（オムニバス）用の擬似候補。実在の教員ではないので残り枠・投稿済み
  // グレーアウトの対象にはせず、常に選択可能な状態で末尾に並べる。送信時は
  // サーバー側で科目の代表course_sectionへ束ねる（グループ科目は先頭の変種へ）。
  const _firstInst = currentInstructors[0];
  const _omniCid = (isGroup && _firstInst) ? (_firstInst.courseId ?? '') : (currentCourseId ?? '');
  const _omniCname = (isGroup && _firstInst) ? (_firstInst.courseName ?? name) : name;
  const omnibusChip = `<button type="button" class="chip-btn single" data-group="instructor" data-value="${escapeHtml(OMNIBUS_INSTRUCTOR_LABEL)}" data-course-id="${_omniCid}" data-course-name="${escapeHtml(_omniCname)}" style="display:inline-flex;align-items:center">${escapeHtml(OMNIBUS_INSTRUCTOR_LABEL)}</button>`;
  chipsEl.innerHTML = withReviewed.map(inst => {
    const label = inst.name;
    const cidAttr = ` data-course-id="${inst._cid ?? ''}" data-course-name="${escapeHtml(inst._cname)}" data-syllabus-url="${escapeHtml(inst.syllabus_url || inst.url || '')}"`;
    if (inst.full) {
      return `<button type="button" class="chip-btn single" disabled data-group="instructor" data-value="${escapeHtml(inst.name)}"${cidAttr} style="opacity:.45;text-decoration:line-through;cursor:not-allowed;display:inline-flex;align-items:center">${escapeHtml(label)}${remainingBadgeHtml(inst.remaining)}</button>`;
    }
    if (inst.reviewed) {
      return `<button type="button" class="chip-btn single" disabled data-group="instructor" data-value="${escapeHtml(inst.name)}"${cidAttr} style="opacity:.5;cursor:not-allowed">${escapeHtml(label)}（投稿済み）</button>`;
    }
    return `<button type="button" class="chip-btn single" data-group="instructor" data-value="${escapeHtml(inst.name)}"${cidAttr} style="display:inline-flex;align-items:center">${escapeHtml(label)}${remainingBadgeHtml(inst.remaining)}</button>`;
  }).join('') + omnibusChip;
  // オンデマンド配信科目の案内は担当教員ラベル直下の #instructorHint に集約したため、
  // 下の注記は常に重複投稿の共通文言のみ
  const _limitNotice = document.getElementById('instructorLimitNotice');
  _limitNotice.textContent = '同じ科目・先生の組み合わせに投稿済みの場合は選べません。';
  _limitNotice.classList.remove('hidden');
  chipsEl.querySelectorAll('.chip-btn:not([disabled])').forEach(btn => {
    btn.onclick = () => {
      chipsEl.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('selectedInstructorHidden').value = btn.dataset.value;
      // 統合科目（グループ）はどの教員を選んだかで実際の科目（変種）が確定するため、
      // クリックのたびにdata-course-id/dataset-course-nameへ差し替える
      // （非グループ選択時もchip生成時点で自分自身のcourseId/courseNameが入っているため無害）
      if (btn.dataset.courseId) {
        const cName = btn.dataset.courseName;
        document.getElementById('courseNameHidden').value = cName;
        // isGroupの間はグループ表記(displayName)のまま維持し、実際に解決された
        // 変種名(cName)はcourseNameHidden（送信用）にのみ反映する
        document.getElementById('selectedCourseName').textContent = isGroup ? displayName : cName;
        currentCourseId = parseInt(btn.dataset.courseId, 10);
      }
      document.getElementById('instructorHint').classList.add('hidden');
      document.getElementById('instructorError').classList.add('hidden');
      syncSyllabusLink();
      saveData();
    };
  });
  saveData();
}

document.getElementById('clearCourse').addEventListener('click', () => {
  document.getElementById('courseNameHidden').value = '';
  document.getElementById('selectedCourse').classList.add('hidden');
  document.getElementById('selectedInstructorHidden').value = '';
  document.getElementById('selectedInstructorDisplay').classList.add('hidden');
  syncSyllabusLink();
  chipsEl.classList.add('hidden');
  chipsEl.innerHTML = '';
  document.getElementById('instructorHint').classList.add('hidden');
  document.getElementById('instructorUnregistered').classList.add('hidden');
  document.getElementById('instructorLimitNotice').classList.add('hidden');
  currentInstructors = [];
  if (searchMode === 'course') {
    searchInput.classList.remove('hidden');
    searchInput.value = '';
    document.getElementById('courseNameLabelInstr').classList.add('hidden');
    document.getElementById('instructorSelectSection').classList.add('hidden');
    searchInput.focus();
  } else {
    document.getElementById('courseNameLabelInstr').classList.add('hidden');
    document.getElementById('instructorModeSection').classList.remove('hidden');
    document.getElementById('instructorSelectSection').classList.add('hidden');
    document.getElementById('instructorCourseList').classList.remove('hidden');
  }
});

document.addEventListener('click', e => {
  if (!searchInput.contains(e.target) && !dropdown.contains(e.target)) {
    dropdown.classList.add('hidden');
  }
  const instrInput = document.getElementById('instructorSearchInput');
  const instrDrop = document.getElementById('instructorSearchDropdown');
  if (instrInput && instrDrop && !instrInput.contains(e.target) && !instrDrop.contains(e.target)) {
    instrDrop.classList.add('hidden');
  }
});

// 先生名検索
const instrSearchInput = document.getElementById('instructorSearchInput');
const instrSearchDropdown = document.getElementById('instructorSearchDropdown');

function clearInstrSearch() {
  instrSearchDropdown.classList.add('hidden');
  document.getElementById('instructorCourseList').classList.add('hidden');
  document.getElementById('instructorCourseUnregistered').classList.add('hidden');
  document.getElementById('instructorCourseLimitNotice').classList.add('hidden');
  document.getElementById('selectedInstructorHidden').value = '';
  syncSyllabusLink();
}

function _showInstrDropdown(items, q) {
  if (items.length === 0) {
    instrSearchDropdown.innerHTML = `<div class="px-4 py-4 text-sm text-gray-500">候補がありません</div>`;
    instrSearchDropdown.classList.remove('hidden');
    return;
  }
  // 担当科目がすべて募集締切なら、この教員は募集を完全に終了しているものとして扱う
  const _closedInstr = inst => inst.courses.length > 0 && inst.courses.every(c => c.full);
  // 募集終了の教員は末尾にまとめる（その中の並びは元のまま維持）
  const sorted = items.slice().sort((a, b) => (_closedInstr(a) ? 1 : 0) - (_closedInstr(b) ? 1 : 0));
  instrSearchDropdown.innerHTML = sorted.slice(0, 20).map((inst, idx) => {
    // 募集を完全に終了した教員は検索候補の行に斜線＋「募集終了」を表示する
    const closed = _closedInstr(inst);
    return `<div class="px-4 py-3 cursor-pointer" data-instr-idx="${idx}"
      style="border-bottom:1px solid #f3f4f6;transition:background .1s"
      onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background=''">
      <div style="font-size:14px;font-weight:600;color:#1e293b${closed ? ';text-decoration:line-through;opacity:.55' : ''}">${escapeHtml(inst.name)}${closed ? remainingBadgeHtml(0) : ''}</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:2px">${inst.courses.length}科目担当</div>
    </div>`;
  }).join('');
  instrSearchDropdown.classList.remove('hidden');
  instrSearchDropdown.querySelectorAll('[data-instr-idx]').forEach(el => {
    const inst = sorted[parseInt(el.dataset.instrIdx)];
    el.addEventListener('click', () => selectInstructor(inst));
  });
}

function searchInstructors(q) {
  if (!q) { clearInstrSearch(); return; }
  if (_preload) {
    const ql = q.replace(/　/g, ' ').trim().toLowerCase();
    const items = _preload.instructors
      .map(i => ({ ...i, courses: (i.courses || []).filter(_isSubmittable) }))
      .filter(i => i.courses.length && i.name.toLowerCase().includes(ql))
      .sort((a, b) => {
        const al = a.name.toLowerCase(), bl = b.name.toLowerCase();
        return (al.startsWith(ql) ? 0 : 1) - (bl.startsWith(ql) ? 0 : 1) || al.localeCompare(bl);
      });
    _showInstrDropdown(items, q);
  } else {
    fetch(`/api/instructors?q=${encodeURIComponent(q)}${_facultyQS('&')}`)
      .then(r => r.json())
      .then(d => { _collectOnDemand(d); _showInstrDropdown(
        (d.instructors || [])
          .map(i => ({ ...i, courses: (i.courses || []).filter(_isSubmittable) }))
          .filter(i => i.courses.length),
        q); })
      .catch(() => {});
  }
}

instrSearchInput.addEventListener('compositionend', () => {
  searchInstructors(instrSearchInput.value.trim());
});

instrSearchInput.addEventListener('input', () => {
  searchInstructors(instrSearchInput.value.trim());
});

// 語尾の数字・アルファベットのみが異なる科目（例: 生物学各論A1/A2/C1/C2）を担当する
// 教員の場合、教員名検索から選んだ担当科目一覧でも1件にまとめて表示する
// （_groupCourseItemsと同じ統合規則。ここでは教員は既に確定しているため、代表として
// 「未投稿かつ空きのある変種」→「空きのある変種」→「先頭の変種」の優先順で
// 実際に送信されるsubjectを1つ選ぶだけでよい）。
function _groupInstructorCourseItems(courses, instName) {
  // グループ化キーは _groupCourseItems と同じ（サーバー提供の variantGroupKey 優先、
  // 教養科目はベース名＋遠隔/対面フラグにフォールバック）。
  const groupKey = c => c.variantGroupKey || (c.variantGroup ? c.variantGroup + (c.isRemote ? ' remote' : '') : '');
  const byGroup = new Map();
  for (const c of courses) {
    const k = groupKey(c);
    if (!k) continue;
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k).push(c);
  }
  const emitted = new Set();
  const result = [];
  for (const c of courses) {
    const key = groupKey(c) || null;
    const members = key ? byGroup.get(key) : null;
    if (!members || members.length < 2) { result.push(c); continue; }
    if (emitted.has(key)) continue;
    emitted.add(key);
    const scored = members.map(m => ({ m, reviewed: !m.full && _reviewedSet.has(m.id + '::' + instName) }));
    const rep = (scored.find(s => !s.m.full && !s.reviewed) || scored.find(s => !s.m.full) || scored[0]).m;
    // 専門科目は管理画面と同じ完全なグループラベルをそのまま表示。教養科目はベース名＋接尾辞。
    const exactLabel = members[0].variantGroupLabel || c.variantGroupLabel || '';
    const suffixes = members.map(m => m.name.startsWith(c.variantGroup) ? m.name.slice(c.variantGroup.length) : m.name);
    result.push({
      id: rep.id, name: rep.name, full: rep.full, remaining: rep.remaining,
      syllabus_url: rep.syllabus_url || '',
      displayName: exactLabel || `${c.variantGroup}(${suffixes.join('/')})`,
    });
  }
  return result;
}

function selectInstructor(inst) {
  instrSearchInput.value = inst.name;
  instrSearchDropdown.classList.add('hidden');
  document.getElementById('selectedInstructorHidden').value = inst.name;
  const courseLabel = document.getElementById('instructorCourseLabel');
  const courseItems = document.getElementById('instructorCourseItems');
  const courseUnregistered = document.getElementById('instructorCourseUnregistered');
  if (inst.courses.length === 0) {
    courseLabel.textContent = '';
    courseItems.innerHTML = '';
    courseUnregistered.classList.remove('hidden');
    document.getElementById('instructorCourseLimitNotice').classList.add('hidden');
    document.getElementById('instructorCourseList').classList.remove('hidden');
    return;
  }
  courseUnregistered.classList.add('hidden');
  const groupedCourses = _groupInstructorCourseItems(inst.courses, inst.name);
  courseLabel.textContent = `${inst.name} の担当科目（${groupedCourses.length}件）`;
  const coursesWithReviewed = groupedCourses.map(c => ({
    ...c,
    reviewed: !c.full && _reviewedSet.has(c.id + '::' + inst.name),
  }));
  courseItems.innerHTML = coursesWithReviewed.map(c => {
    const label = c.displayName || c.name;
    if (c.full) {
      return `<button type="button" class="chip-btn" disabled style="opacity:.45;text-decoration:line-through;cursor:not-allowed;display:inline-flex;align-items:center"
        data-course-name="${escapeHtml(c.name)}" data-display-name="${escapeHtml(label)}">${escapeHtml(label)}${remainingBadgeHtml(c.remaining)}</button>`;
    }
    if (c.reviewed) {
      return `<button type="button" class="chip-btn" disabled style="opacity:.5;cursor:not-allowed"
        data-course-name="${escapeHtml(c.name)}" data-display-name="${escapeHtml(label)}">${escapeHtml(label)}（投稿済み）</button>`;
    }
    return `<button type="button" class="chip-btn" style="display:inline-flex;align-items:center"
      data-course-name="${escapeHtml(c.name)}" data-display-name="${escapeHtml(label)}" data-course-id="${c.id}" data-syllabus-url="${escapeHtml(c.syllabus_url || '')}">${escapeHtml(label)}${remainingBadgeHtml(c.remaining)}</button>`;
  }).join('');
  document.getElementById('instructorCourseLimitNotice').classList.remove('hidden');
  document.getElementById('instructorCourseList').classList.remove('hidden');
  courseItems.querySelectorAll('[data-course-name]:not([disabled])').forEach(el => {
    el.addEventListener('click', () => {
      courseItems.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
      el.classList.add('active');
      currentInstructors = [{ name: inst.name, syllabus_url: el.dataset.syllabusUrl || '' }];
      currentCourseId = el.dataset.courseId ? parseInt(el.dataset.courseId, 10) : null;
      document.getElementById('courseNameHidden').value = el.dataset.courseName;
      document.getElementById('selectedInstructorHidden').value = inst.name;
      document.getElementById('selectedCourseName').textContent = el.dataset.displayName || el.dataset.courseName;
      document.getElementById('selectedCourse').classList.remove('hidden');
      document.getElementById('courseNameLabelInstr').classList.remove('hidden');
      document.getElementById('selectedInstructorName').textContent = inst.name;
      document.getElementById('selectedInstructorDisplay').classList.remove('hidden');
      document.getElementById('instructorModeSection').classList.add('hidden');
      document.getElementById('instructorSelectSection').classList.add('hidden');
      document.getElementById('courseError').classList.add('hidden');
      syncSyllabusLink();
      saveData();
    });
  });
}

const COMMENT_FEEDBACK = [
  [0,   '',    '#e5e7eb', 0],
  [1,   'もう少し詳しく書いてみよう！✍️', '#fbbf24', 5],
  [10,  'いい感じ！もう少し書いてみて💪', '#f97316', 25],
  [25,  'ナイス！後輩の参考になりそう😊', '#6366f1', 55],
  [40,  '充実したレビューです！🎉', '#10b981', 80],
  [60,  '神レビュー！ありがとうございます🙏', '#10b981', 100],
];
function updateCommentFeedback(len) {
  let row = COMMENT_FEEDBACK[0];
  for (const r of COMMENT_FEEDBACK) { if (len >= r[0]) row = r; }
  document.getElementById('commentFeedback').textContent = row[1];
  document.getElementById('charBar').style.width = row[3] + '%';
  document.getElementById('charBar').style.background = row[2];
}

// コメントは MIN_COMMENT_LEN 文字以上を必須とする。未満の間は送信ボタンが非活性表示に
// なり、押しても送信されない（reviewForm submit 内で valid=false）。1文字以上
// MIN_COMMENT_LEN 文字未満の間は「もう少し書こう」＋残り文字数ヒントを表示し、
// 到達すると自動で消える。サーバー側 /submit の同チェックはバックストップとして残す。
// 閾値は core/config.py MIN_COMMENT_LEN が正。
// 文字数は `【…】` の見出し（チップが挿入するもの）を除いて数える。
// サーバー側 core/config.py count_comment_chars と同じ規則。
function commentLen() {
  return document.getElementById('comment').value.replace(/【[^】]*】/g, '').trim().length;
}
function updateCommentHint() {
  const hint = document.getElementById('commentMinLenHint');
  if (!hint) return;
  const el = document.getElementById('comment');
  const len = commentLen();
  if (len === 0 || len >= MIN_COMMENT_LEN) {
    hint.classList.add('hidden');
    el.classList.remove('ring-2', 'ring-amber-400', 'border-amber-400');
    return;
  }
  document.getElementById('commentHintCur').textContent = len;
  document.getElementById('commentHintRemain').textContent = MIN_COMMENT_LEN - len;
  document.getElementById('commentHintBar').style.width =
    Math.round(len / MIN_COMMENT_LEN * 100) + '%';
  hint.classList.remove('hidden');
  el.classList.add('ring-2', 'ring-amber-400', 'border-amber-400');
}

// コメント欄は入力量に応じて高さを自動で伸ばす（長文でもスクロールせず書ける）。
// rows=5の初期高さを下限に、最大60vhまで伸ばしてそれ以降は内部スクロールにする。
function autoGrowComment() {
  const el = document.getElementById('comment');
  const maxHeight = Math.round(window.innerHeight * 0.6);
  el.style.height = 'auto';
  const next = Math.min(el.scrollHeight, maxHeight);
  el.style.height = next + 'px';
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
}
autoGrowComment();

document.querySelectorAll('.prompt-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const el = document.getElementById('comment');
    const prompt = chip.dataset.prompt;
    const cur = el.value;
    // 見出しは新しい行の頭に置き、直後に改行して本文をその下から書かせる
    if (cur && !cur.endsWith('\n')) el.value = cur + '\n';
    el.value += prompt + '\n';
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    const hintEl = document.getElementById('promptHint');
    if (hintEl && chip.dataset.hint) {
      hintEl.textContent = '💡 例：' + chip.dataset.hint;
      hintEl.classList.remove('hidden');
    }
    updateCommentFeedback(commentLen());
    updateCommentHint();
    autoGrowComment();
    saveData();
  });
});

document.getElementById('comment').addEventListener('input', () => {
  const el = document.getElementById('comment');
  updateCommentFeedback(commentLen());
  if (el.value.trim()) el.classList.remove('ring-2', 'ring-red-400', 'border-red-400');
  updateCommentHint();
  autoGrowComment();
  saveData();
});
const studentIdInput = document.getElementById('student_id');
const studentIdError = document.getElementById('studentIdError');
const STUDENT_ID_RE = /^\d{7}(MM|ME|MH|[LHJEBSTAZX])$/;

if (studentIdInput) {
  studentIdInput.addEventListener('input', () => {
    const v = studentIdInput.value.replace(/[\s　]+/g, '').toUpperCase();
    const invalid = v && !STUDENT_ID_RE.test(v);
    studentIdError.classList.toggle('hidden', !invalid);
    studentIdInput.classList.toggle('border-red-400', invalid);
    studentIdInput.classList.toggle('ring-2', invalid);
    studentIdInput.classList.toggle('ring-red-300', invalid);
    if (!invalid) clearCardError('studentIdCard');
    updateProgressUI();
  });
}

// ── 入力状況（7項目）の進捗計算 ──
// 送信時の厳密なバリデーション（reviewForm submit内）とは別の、表示専用の簡易チェック。
// 既存の送信時バリデーションはそのまま残し、これは進捗カード・送信ボタンのラベル切り替えにのみ使う。
// 成績評価方法カードの必須4小問（授業形式・出席確認・課題の頻度・課題の量）
// それぞれの充足判定。isGradingMethodComplete()（進捗カードの1項目としての判定）と
// gradingProgressCountの「◯/4」表示の両方でこの4関数を共有する。
// 🎯 評価方法（eval）は任意入力なのでこの充足判定には含めない。
function classFormatDone() {
  const fmtActives = [...document.querySelectorAll('[data-group="class_format"].active')];
  if (fmtActives.length === 0) return false;
  if (fmtActives.some(b => b.dataset.value === '__format_custom__') &&
      !document.getElementById('formatCustomInput').value.trim()) return false;
  if (fmtActives.length >= 2) {
    const inputs = [...document.querySelectorAll('.percent-input[data-slider-group="class_format"]')];
    const total = inputs.reduce((sum, i) => sum + (parseFloat(i.value) || 0), 0);
    if (inputs.length !== fmtActives.length || total !== 100) return false;
  }
  return true;
}
function attendanceDone() {
  const attActive = document.querySelector('[data-group="attendance"].active');
  if (!attActive) return false;
  if (attActive.dataset.value === 'たまにあり' && !document.querySelector('[data-group="attendance_surprise"].active')) return false;
  if (attActive.dataset.value !== 'なし') {
    const methodActives = [...document.querySelectorAll('[data-group="attendance_method"].active')];
    if (methodActives.length === 0) return false;
    if (methodActives.some(b => b.dataset.value === '__attendance_method_custom__') &&
        !document.getElementById('attendanceMethodCustomInput').value.trim()) return false;
  }
  return true;
}
function homeworkDone() {
  const hwFreq = document.querySelector('[data-group="homework_frequency"].active');
  if (hwFreq && hwFreq.dataset.value === 'なし') return true;
  return !!document.querySelector('[data-group="homework"].active');
}
function homeworkFrequencyDone() { return !!document.querySelector('[data-group="homework_frequency"].active'); }
// 評価方法（🎯 eval）は任意入力なので進捗カウント・完了判定の対象外（2026-09-08、ユーザー指示）。

function isGradingMethodComplete() {
  return classFormatDone() && attendanceDone() && homeworkDone() && homeworkFrequencyDone();
}

function updateGradingProgress() {
  const count = [classFormatDone(), attendanceDone(), homeworkDone(), homeworkFrequencyDone()]
    .filter(Boolean).length;
  const el = document.getElementById('gradingProgressCount');
  if (el) el.textContent = `${count} / 4`;
}

function scrollToItem(el) {
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (el.focus) {
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
  }
}

const PROGRESS_ITEMS = [
  { label: '学籍番号', done: () => !studentIdInput || (studentIdInput.value.trim() !== '' && STUDENT_ID_RE.test(studentIdInput.value.replace(/[\s　]+/g, '').toUpperCase())),
    focus: () => scrollToItem(studentIdInput) },
  { label: '科目・担当教員', done: () => document.getElementById('courseNameHidden').value.trim() !== ''
      && document.getElementById('selectedInstructorHidden').value.trim() !== '',
    focus: () => scrollToItem(document.getElementById('courseCard')) },
  { label: '受講年度', done: () => {
      const v = document.getElementById('academic_year').value;
      return !!v && v !== '0';
    },
    focus: () => scrollToItem(document.getElementById('academicYearCard')) },
  { label: '楽単度', done: () => currentEase > 0, focus: () => scrollToItem(document.getElementById('easeCard')) },
  { label: '充実度', done: () => currentRating > 0, focus: () => scrollToItem(document.getElementById('ratingCard')) },
  { label: '成績評価方法', done: isGradingMethodComplete, focus: () => scrollToItem(document.getElementById('gradingCard')) },
  // コメントは MIN_COMMENT_LEN 文字以上で「入力済み」とみなす。未満の間は送信ボタンが
  // 非活性表示になり、押しても送信されず未入力バナー＋琥珀ヒントで残り文字数を促す。
  { label: 'コメント', done: () => commentLen() >= MIN_COMMENT_LEN, focus: () => scrollToItem(document.getElementById('comment')) },
];

// ── 未入力項目のまとめバナー ──
// 送信を試みてバリデーションに落ちた時だけ表示する（入力の最初から出しっぱなしにすると
// 進捗カードと情報が重複するため）。項目が埋まるたびにsaveData()経由で自動的に消える。
let errorBannerArmed = false;

function updateErrorBanner() {
  const banner = document.getElementById('errorSummaryBanner');
  if (!banner) return;
  if (!errorBannerArmed) { banner.classList.add('hidden'); return; }
  const missing = PROGRESS_ITEMS.filter(item => !item.done());
  if (missing.length === 0) {
    banner.classList.add('hidden');
    errorBannerArmed = false;
    return;
  }
  document.getElementById('errorSummaryTitle').textContent = `あと${missing.length}項目で送信できます`;
  document.getElementById('errorSummaryChips').innerHTML = missing.map((item, i) =>
    `<button type="button" class="error-summary-chip" data-idx="${i}">${item.label} <span style="font-size:10px">&#8595;</span></button>`
  ).join('');
  document.getElementById('errorSummaryChips').querySelectorAll('[data-idx]').forEach(btn => {
    btn.addEventListener('click', () => missing[parseInt(btn.dataset.idx, 10)].focus());
  });
  banner.classList.remove('hidden');
}

function updateProgressUI() {
  const missing = PROGRESS_ITEMS.filter(item => !item.done());
  const filled = PROGRESS_ITEMS.length - missing.length;
  const countEl = document.getElementById('progressCount');
  const barEl = document.getElementById('progressBar');
  if (countEl) countEl.textContent = `${filled} / ${PROGRESS_ITEMS.length} 項目`;
  if (barEl) barEl.style.width = `${Math.round(filled / PROGRESS_ITEMS.length * 100)}%`;

  const btn = document.getElementById('submitBtn');
  if (!btn || btn.disabled) return; // 送信処理中はラベルを上書きしない
  const activeClasses = SUBMIT_BTN_ACTIVE_CLASS.split(' ');
  const inactiveClasses = 'w-full bg-gray-300 text-white font-bold py-4 rounded-2xl transition text-base shadow-md cursor-not-allowed'.split(' ');
  if (_groupState === 'invalid' || _groupState === 'checking') {
    // 団体コードが無効（または確認中）の間は送信できない。番号を直すか、欄を空にすると送信できる
    btn.textContent = _groupState === 'invalid' ? '団体コードが無効です（直すか空にしてください）' : '団体コードを確認中…';
    btn.className = inactiveClasses.join(' ');
  } else if (missing.length === 0) {
    btn.textContent = 'レビューを投稿する';
    btn.className = activeClasses.join(' ');
  } else {
    btn.textContent = missing.length === 1
      ? `${missing[0].label}を入力すると送信できます`
      : `あと${missing.length}項目で送信できます`;
    btn.className = inactiveClasses.join(' ');
  }
}

let saveTimer;
function persistDraft() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      nickname: document.getElementById('nickname').value,
      academic_year: document.getElementById('academic_year').value,
      course: document.getElementById('courseNameHidden').value,
      courseId: currentCourseId,
      instructors: currentInstructors,
      selectedInstructor: document.getElementById('selectedInstructorHidden').value,
      rating: currentRating,
      ease: currentEase,
      classFormats: [...document.querySelectorAll('[data-group="class_format"].active')].map(b => b.dataset.value),
      formatCustom: document.getElementById('formatCustomInput').value,
      formatPercents: Object.fromEntries([...document.querySelectorAll('.percent-input[data-slider-group="class_format"]')].map(i => [i.dataset.percentFor, i.value])),
      attendance: document.querySelector('[data-group="attendance"].active')?.dataset.value || '',
      attendanceSurprise: document.querySelector('[data-group="attendance_surprise"].active')?.dataset.value || '',
      attendanceMethods: [...document.querySelectorAll('[data-group="attendance_method"].active')].map(b => b.dataset.value),
      attendanceMethodCustom: document.getElementById('attendanceMethodCustomInput').value,
      homework: document.querySelector('[data-group="homework"].active')?.dataset.value || '',
      homeworkFrequency: document.querySelector('[data-group="homework_frequency"].active')?.dataset.value || '',
      evals: [...document.querySelectorAll('[data-group="eval"].active')].map(b => b.dataset.value),
      evalCustom: document.getElementById('evalCustomInput').value,
      comment: document.getElementById('comment').value,
      classFormatExtra: document.getElementById('classFormatExtraInput').value,
      attendanceMethodExtra: document.getElementById('attendanceMethodExtraInput').value,
      homeworkFrequencyExtra: document.getElementById('homeworkFrequencyExtraInput').value,
      homeworkExtra: document.getElementById('homeworkExtraInput').value,
      evalExtra: document.getElementById('evalExtraInput').value,
    }));
    document.getElementById('saveStatus').textContent = '自動保存済み ✓';
}

function saveData() {
  document.getElementById('saveStatus').textContent = '保存中...';
  syncSyllabusLink();  // 全状態変更でここを通るので、シラバス導線は現在の選択から毎回導出できる
  updateProgressUI();
  updateGradingProgress();
  updateErrorBanner();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(persistDraft, 500);
}

function loadData() {
  try {
    const d = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!d) return;
    if (d.academic_year && Number(d.academic_year) >= 2022) {
      document.getElementById('academic_year').value = d.academic_year;
      document.querySelectorAll('[data-group="academic_year"]').forEach(b => {
        b.classList.toggle('active', b.dataset.value === String(d.academic_year));
      });
      if (['2023', '2022'].includes(String(d.academic_year))) {
        document.getElementById('academicYearOld').open = true;
      }
    }
    if (d.nickname) document.getElementById('nickname').value = d.nickname;
    if (d.course) {
      selectCourse(d.course, d.instructors || [], d.courseId);
      if (d.selectedInstructor) {
        document.getElementById('selectedInstructorHidden').value = d.selectedInstructor;
        document.querySelectorAll('[data-group="instructor"]').forEach(b => {
          b.classList.toggle('active', b.dataset.value === d.selectedInstructor);
        });
        document.getElementById('instructorHint').classList.add('hidden');
        syncSyllabusLink();
      }
    }
    if (d.rating) {
      currentRating = d.rating;
      document.getElementById('ratingHidden').value = d.rating;
      document.getElementById('ratingLabel').textContent = RATING_LABELS[d.rating];
      buildStars();
    }
    if (d.ease) {
      currentEase = d.ease;
      document.getElementById('easeHidden').value = EASE_VALS[d.ease];
      document.getElementById('easeLabel').textContent = EASE_LABELS[d.ease];
      buildEaseStars();
    }
    if (d.classFormats?.length) {
      document.querySelectorAll('[data-group="class_format"]').forEach(b => {
        b.classList.toggle('active', d.classFormats.includes(b.dataset.value));
      });
      if (d.classFormats.includes('__format_custom__')) {
        const formatInput = document.getElementById('formatCustomInput');
        formatInput.classList.remove('hidden');
        if (d.formatCustom) formatInput.value = d.formatCustom;
      }
      renderGroupPercents('class_format', d.formatPercents);
    }
    if (d.attendance) {
      document.querySelectorAll('[data-group="attendance"]').forEach(b => {
        b.classList.toggle('active', b.dataset.value === d.attendance);
      });
      if (d.attendance === 'たまにあり') {
        document.getElementById('attendanceSurpriseSection').classList.remove('hidden');
        if (d.attendanceSurprise) {
          document.querySelectorAll('[data-group="attendance_surprise"]').forEach(b => {
            b.classList.toggle('active', b.dataset.value === d.attendanceSurprise);
          });
        }
      }
      if (d.attendance !== 'なし') {
        document.getElementById('attendanceMethodSection').classList.remove('hidden');
        if (d.attendanceMethods?.length) {
          document.querySelectorAll('[data-group="attendance_method"]').forEach(b => {
            b.classList.toggle('active', d.attendanceMethods.includes(b.dataset.value));
          });
          if (d.attendanceMethods.includes('__attendance_method_custom__')) {
            const methodInput = document.getElementById('attendanceMethodCustomInput');
            methodInput.classList.remove('hidden');
            if (d.attendanceMethodCustom) methodInput.value = d.attendanceMethodCustom;
          }
        }
      } else {
        document.getElementById('attendanceNoneConfirm').classList.remove('hidden');
      }
    }
    if (d.homeworkFrequency) {
      document.querySelectorAll('[data-group="homework_frequency"]').forEach(b => {
        b.classList.toggle('active', b.dataset.value === d.homeworkFrequency);
      });
      if (d.homeworkFrequency === '毎授業' || d.homeworkFrequency === '数回') {
        document.getElementById('homeworkSection').classList.remove('hidden');
      }
    }
    if (d.homework) {
      document.querySelectorAll('[data-group="homework"]').forEach(b => {
        b.classList.toggle('active', b.dataset.value === d.homework);
      });
    }
    if (d.evals?.length) {
      document.querySelectorAll('[data-group="eval"]').forEach(b => {
        b.classList.toggle('active', d.evals.includes(b.dataset.value));
      });
      if (d.evals.includes('__custom__')) {
        const customInput = document.getElementById('evalCustomInput');
        customInput.classList.remove('hidden');
        if (d.evalCustom) customInput.value = d.evalCustom;
      }
    }
    if (d.comment) {
      document.getElementById('comment').value = d.comment;
      updateCommentFeedback(commentLen());
      autoGrowComment();
    }
    if (d.classFormatExtra) document.getElementById('classFormatExtraInput').value = d.classFormatExtra;
    if (d.attendanceMethodExtra) document.getElementById('attendanceMethodExtraInput').value = d.attendanceMethodExtra;
    if (d.homeworkFrequencyExtra) document.getElementById('homeworkFrequencyExtraInput').value = d.homeworkFrequencyExtra;
    if (d.homeworkExtra) document.getElementById('homeworkExtraInput').value = d.homeworkExtra;
    if (d.evalExtra) document.getElementById('evalExtraInput').value = d.evalExtra;
    updateGradingMethod();
  } catch (_) {}
}

const CARD_IDS = ['studentIdCard','academicYearCard','courseCard','easeCard','ratingCard','gradingCard'];
function clearCardError(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.remove('ring-2','ring-red-400'); el.style.background = ''; }
}
function markCardError(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.add('ring-2','ring-red-400'); el.style.background = '#fff5f5'; }
}
function refreshCardErrors() {
  CARD_IDS.forEach(clearCardError);
  const map = [
    ['studentIdError','studentIdCard'],
    ['academicYearError','academicYearCard'],
    ['courseError','courseCard'],
    ['instructorError','courseCard'],
    ['ratingError','ratingCard'],
    ['easeError','easeCard'],
    ['classFormatError','gradingCard'],
    ['attendanceError','gradingCard'],
    ['attendanceSurpriseError','gradingCard'],
    ['attendanceMethodError','gradingCard'],
    ['homeworkError','gradingCard'],
    ['homeworkFrequencyError','gradingCard'],
  ];
  map.forEach(([errId, cardId]) => {
    const errEl = document.getElementById(errId);
    if (errEl && !errEl.classList.contains('hidden')) markCardError(cardId);
  });
}

document.getElementById('reviewForm').addEventListener('submit', async e => {
  // 修正理由: id_tokenを送信直前に非同期取得する必要があるため、常にpreventDefault()
  // してから、バリデーション通過後にliff.getIDToken()→hidden inputセット→
  // form.submit()（ネイティブ送信。'submit'イベントは再発火しないため無限ループしない）
  // という順で処理する。
  e.preventDefault();
  // 送信ボタン以外（スマホキーボードのGoキーなど）からの送信は静かに止める
  if (!e.submitter || e.submitter.id !== 'submitBtn') {
    return;
  }
  // 全エラーを一旦リセット
  ['studentIdError','academicYearError',
   'courseError','instructorError','ratingError','easeError',
   'classFormatError','formatCustomError','attendanceError','attendanceSurpriseError','attendanceMethodError','attendanceMethodCustomError','homeworkError',
   'homeworkFrequencyError','evalCustomError','formatPercentError'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  });
  CARD_IDS.forEach(clearCardError);

  let valid = true;

  // 団体コード: 入力があるのに確認できていない（無効・確認中）ときは、黙って無視せず送信を止める
  if (document.getElementById('group_code').value.trim()) {
    clearTimeout(_groupTimer);
    if (!(await checkGroupCode())) {
      document.getElementById('groupCard').scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
  }

  if (studentIdInput) {
    studentIdInput.value = studentIdInput.value.replace(/[\s　]+/g, '').toUpperCase();
    if (!STUDENT_ID_RE.test(studentIdInput.value)) {
      studentIdError.classList.remove('hidden');
      valid = false;
    }
  }

  if (!document.getElementById('academic_year').value || document.getElementById('academic_year').value === '0') {
    document.getElementById('academicYearError').classList.remove('hidden');
    valid = false;
  }
  if (!document.getElementById('courseNameHidden').value.trim()) {
    document.getElementById('courseError').classList.remove('hidden');
    valid = false;
  }
  if (!document.getElementById('ratingHidden').value) {
    document.getElementById('ratingError').classList.remove('hidden');
    valid = false;
  }
  if (!document.getElementById('easeHidden').value) {
    document.getElementById('easeError').classList.remove('hidden');
    valid = false;
  }
  if (document.getElementById('courseNameHidden').value &&
      !document.getElementById('selectedInstructorHidden').value) {
    document.getElementById('instructorSelectSection').classList.remove('hidden');
    document.getElementById('instructorError').classList.remove('hidden');
    valid = false;
  }
  const attActive = document.querySelector('[data-group="attendance"].active');
  if (!attActive) {
    document.getElementById('attendanceError').classList.remove('hidden');
    valid = false;
  } else {
    if (attActive.dataset.value === 'たまにあり' && !document.querySelector('[data-group="attendance_surprise"].active')) {
      document.getElementById('attendanceSurpriseError').classList.remove('hidden');
      valid = false;
    }
    if (attActive.dataset.value !== 'なし') {
      const attMethodActives = [...document.querySelectorAll('[data-group="attendance_method"].active')];
      if (attMethodActives.length === 0) {
        document.getElementById('attendanceMethodError').classList.remove('hidden');
        valid = false;
      } else if (attMethodActives.some(b => b.dataset.value === '__attendance_method_custom__') &&
                 !document.getElementById('attendanceMethodCustomInput').value.trim()) {
        document.getElementById('attendanceMethodCustomError').classList.remove('hidden');
        document.getElementById('attendanceMethodCustomInput').classList.add('border-red-400');
        valid = false;
      }
    }
  }
  const hwFreqActive = document.querySelector('[data-group="homework_frequency"].active');
  if (!hwFreqActive) {
    document.getElementById('homeworkFrequencyError').classList.remove('hidden');
    valid = false;
  }
  if (hwFreqActive && hwFreqActive.dataset.value !== 'なし' && !document.querySelector('[data-group="homework"].active')) {
    document.getElementById('homeworkError').classList.remove('hidden');
    valid = false;
  }
  // 評価方法（任意入力。選択されたときのみ「その他」の記述をチェックする）
  const evalActives = [...document.querySelectorAll('[data-group="eval"].active')];
  if (evalActives.some(b => b.dataset.value === '__custom__') &&
      !document.getElementById('evalCustomInput').value.trim()) {
    document.getElementById('evalCustomError').classList.remove('hidden');
    document.getElementById('evalCustomInput').classList.add('border-red-400');
    valid = false;
  }
  // 授業形式
  const fmtActives = [...document.querySelectorAll('[data-group="class_format"].active')];
  if (fmtActives.length === 0) {
    document.getElementById('classFormatError').classList.remove('hidden');
    valid = false;
  } else if (fmtActives.some(b => b.dataset.value === '__format_custom__') &&
             !document.getElementById('formatCustomInput').value.trim()) {
    document.getElementById('formatCustomError').classList.remove('hidden');
    document.getElementById('formatCustomInput').classList.add('border-red-400');
    valid = false;
  } else if (fmtActives.length >= 2) {
    const percentInputs = [...document.querySelectorAll('.percent-input[data-slider-group="class_format"]')];
    const total = percentInputs.reduce((sum, i) => sum + (parseFloat(i.value) || 0), 0);
    const allFilled = percentInputs.length === fmtActives.length &&
      percentInputs.every(i => i.value.trim() !== '' && parseFloat(i.value) > 0);
    if (!allFilled || total !== 100) {
      document.getElementById('formatPercentError').classList.remove('hidden');
      valid = false;
    }
  }
  // コメントは MIN_COMMENT_LEN 文字未満をフォーム側でも弾く（サーバー側 /submit の
  // 「30文字以上で入力してください」チェックはバックストップとして残す）。
  if (commentLen() < MIN_COMMENT_LEN) {
    const c = document.getElementById('comment');
    c.classList.remove('ring-amber-400', 'border-amber-400');
    c.classList.add('ring-2', 'ring-red-400', 'border-red-400');
    valid = false;
  }
  if (!valid) {
    refreshCardErrors();
    errorBannerArmed = true;
    updateErrorBanner();
    setTimeout(() => {
      const banner = document.getElementById('errorSummaryBanner');
      if (banner && !banner.classList.contains('hidden')) {
        banner.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        const firstErr = document.querySelector('.text-red-500:not(.hidden)');
        if (firstErr) firstErr.scrollIntoView({ behavior: 'smooth', block: 'center' });
        else window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    }, 150);
    return;
  }

  updateGradingMethod();
  const btn = document.getElementById('submitBtn');
  btn.textContent = '送信中...';
  btn.disabled = true;

  try {
    await ensureLiffInit();
    const idToken = liff.isLoggedIn() ? await liff.getIDToken() : null;
    if (!idToken) throw new Error('not logged in');
    if (idTokenExpired(idToken)) {
      // 期限切れ: 下書きを保存してから logout→再ログインで新しいトークンを取り直す
      // （復帰不能なときだけ従来のalert誘導へフォールバック）
      clearTimeout(saveTimer);
      persistDraft();
      if (forceReauthAndReload('expired', 'submit', idToken)) return;
      throw new Error('id token expired');
    }
    document.getElementById('idTokenHidden').value = idToken;
  } catch (err) {
    btn.textContent = '送信する';
    btn.disabled = false;
    alert('LINEアプリでのログイン確認に失敗しました。LINEアプリの「レビュー投稿」メニューから開き直してください。');
    return;
  }

  // 修正理由: 以前はここで下書きを消してから送信していたが、送信後に
  // サーバー側でLINEトークン検証が失敗するケース（期限切れ等）があり、
  // 「レビュー投稿」から開き直すと入力内容が全て消えてしまっていた。
  // 送信成功はform_success.html側で確定してから消すことにし、
  // ここでは直前の入力（debounce待ち分）を確実に保存するだけに留める
  clearTimeout(saveTimer);
  persistDraft();
  // 次回以降のレビュー投稿でニックネームを自動入力するため、下書き（送信成功時に
  // form_success.html 側でクリアされる）とは別キーに保持する。学籍番号と違い
  // 読み取り専用にはせず、次回そのまま書き換えられるようにしておく。
  try { localStorage.setItem('kobe_nickname', document.getElementById('nickname').value.trim()); } catch (_) {}
  // 冪等キーを送信ごとに1個発行。この直後のネイティブ送信でPOST bodyに載り、
  // OS/webviewが同じPOSTを再送した場合も同じ値が繰り返し届くのでサーバー側で
  // 二重作成を防げる（新規の送信操作では毎回新しい値になる）。
  try {
    document.getElementById('submitNonceHidden').value =
      (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : (Date.now().toString(36) + Math.random().toString(36).slice(2));
  } catch (_) {}
  // 成功画面「レビューを受け付けました」に出す科目名は、ユーザーが今まさに見ている
  // 表示名（バリアント統合科目なら「英米法(A/B)」のようなグループ表記）に揃える。
  // courseNameHidden は実際に紐づく1変種名なのでDB検索用として別に持つ。
  try {
    document.getElementById('courseDisplayNameHidden').value =
      (document.getElementById('selectedCourseName').textContent || '').trim();
  } catch (_) {}
  e.target.submit();
});

buildStars();
buildEaseStars();
loadData();

// 投稿成功の直後に「戻る」でこのフォームへ返ってきた場合、下書き(kobe_review_v2)は
// form_success.html 側で消してあるが、ブラウザが bfcache / 履歴復元で素のコメント欄
// だけ自前で戻してしまい「投稿済みの本文が残る」状態になる。コメントは毎回まっさらに
// したいので明示クリアする（ニックネームは kobe_nickname から常に復元するため対象外）。
function clearCommentAfterSubmit(consume) {
  let submitted = false;
  try { submitted = sessionStorage.getItem('kobe_review_submitted') === '1'; } catch (_) {}
  if (!submitted) return;
  const el = document.getElementById('comment');
  if (el && el.value) {
    el.value = '';
    updateCommentFeedback(0);
    updateCommentHint();
    const ph = document.getElementById('promptHint');
    if (ph) ph.classList.add('hidden');
    updateProgressUI();
  }
  // フラグの消費は pageshow 側だけで行う。インライン実行時点ではブラウザの
  // 履歴復元がまだ走っておらず値が空に見えることがあり、そこで消すと取りこぼす。
  if (consume) { try { sessionStorage.removeItem('kobe_review_submitted'); } catch (_) {} }
}
clearCommentAfterSubmit(false);
// pageshow は通常ロード・bfcache 復元のどちらでも復元完了後に必ず発火する
window.addEventListener('pageshow', () => clearCommentAfterSubmit(true));

// 前回投稿したニックネームを自動入力（下書きに残っていればそちらを優先。
// 学籍番号と違い readOnly にはしないので、その場で自由に書き換えられる）
(function() {
  try {
    const nickEl = document.getElementById('nickname');
    if (nickEl && !nickEl.value) {
      const saved = localStorage.getItem('kobe_nickname');
      if (saved) nickEl.value = saved;
    }
  } catch (_) {}
})();
updateProgressUI();
updateGradingProgress();

// 学籍番号キャッシュの読み書き（uid なしでアクセスされた場合の保険）
(function() {
  try {
    const sidEl = document.getElementById('student_id');
    if (sidEl.readOnly) {
      localStorage.setItem('kobe_profile', JSON.stringify({ student_id: sidEl.value }));
    } else {
      const p = JSON.parse(localStorage.getItem('kobe_profile') || 'null');
      if (p && p.student_id && !sidEl.value) {
        sidEl.value = p.student_id;
        sidEl.readOnly = true;
        sidEl.classList.add('bg-gray-50', 'text-gray-500', 'cursor-not-allowed');
      }
    }
  } catch(_) {}
})();

// URL の ?course= パラメーターがあれば科目を自動選択
(async () => {
  const urlCourse = new URLSearchParams(location.search).get('course');
  if (!urlCourse) return;
  // プリフィル（学部の確定）とプリロード取得を待ってから判定する
  await _prefillDone;
  const trySelect = (data) => {
    _collectOnDemand(data);
    const match = (data.courses || []).find(c => c.name === urlCourse);
    if (!match) return;
    if (!_isSubmittable(match)) {
      // 他学部の専門科目など、この会員登録情報では投稿できない科目が指定された場合
      searchInput.value = urlCourse;
      dropdown.innerHTML = `<div class="px-4 py-4 text-sm text-gray-500">「${escapeHtml(urlCourse)}」は、ご登録の学部ではレビュー投稿の対象外です。投稿できる科目を検索してください。</div>`;
      dropdown.classList.remove('hidden');
      return;
    }
    selectCourse(match.name, match.instructors, match.id);
  };
  if (_preload) { trySelect(_preload); return; }
  try {
    const res = await fetch(`/api/courses?q=${encodeURIComponent(urlCourse)}${_facultyQS('&')}`);
    trySelect(await res.json());
  } catch (_) {}
})();
