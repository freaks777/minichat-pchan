/* RP Standalone — i18n (ja default, en toggle) */

const I18N = {
  ja: {
    langToggle: "EN",
    langLabel: "言語",

    /* style panel */
    styleTitle: "文体プリセット",
    stylePersona: "ペルソナ",
    styleCustom: "カスタム...",
    styleStart: "セッション開始",
    styleStarting: "開始中...",
    styleCustomStart: "カスタム設定で開始",
    styleLoadError: "読み込み失敗。サーバーが起動しているか確認してください。",

    customViewpoint: "語り手",
    customNarration: "地の文",
    customPerson: "人称",
    optAIChar: "AIキャラ視点",
    optUserChar: "ユーザーキャラ視点",
    optNarrationOn: "あり",
    optNarrationOff: "なし",
    optFirstPerson: "一人称",
    optThirdPerson: "三人称",

    memoryScope: "記憶スコープ",
    optMemorySession: "このセッションのみ",
    optMemoryPersona: "全セッション共通",
    hintMemoryScope: "このセッションのみ：会話の記憶を他のセッションと共有しません。全セッション共通：同一キャラの全セッションで記憶を共有します。",

    /* chat UI */
    headerNotConnected: "未接続",
    inputPlaceholder: "メッセージを入力...",
    sendButton: "送信",
    btnState: "状態",
    stateEmpty: "変化なし",
    navSessions: "セッション",
    navStudio: "Studio",
    navSettings: "設定",
    statusConnected: "接続済み",
    statusDisconnected: "切断 — ページ再読込で再接続",
    roleYou: "あなた",
    roleAssistant: "アシスタント",

    btnEdit: "編集",
    btnRegenerate: "再生成",
    btnDelete: "削除",
    btnEdited: "[編集済]",
    btnStop: "停止",

    /* studio */
    studioTitle: "Persona Studio",
    tabTemplate: "固定フォーム",
    tabFreetext: "自由入力",
    tabStyleOnly: "SOUL.md",
    tabSaved: "ペルソナ一覧",
    tabDirect: "直接入力",
    tabText: "テキスト入力",
    tabFile: "ファイル指定",
    hintTemplate: "全RP共通のキャラクター基本設定です。",

    fieldPersonaId: "ペルソナID（半角英数）",
    fieldsetBasic: "基本情報",
    fieldName: "名前",
    fieldSex: "身体的性別",
    fieldGender: "性自認・表現",
    fieldAge: "年齢",
    fieldBirthday: "誕生日",
    fieldSpecies: "種族",
    fieldBlood: "血液型",
    fieldHeight: "身長",
    fieldWeight: "体重",
    fieldBWH: "B / W / H",
    fieldsetAppearance: "外見",
    fieldHair: "髪",
    fieldEyes: "目",
    fieldSkin: "肌",
    fieldClothing: "服装スタイル",
    fieldsetPersonality: "人物",
    fieldPersonality: "性格",
    fieldPrinciples: "行動原理・判断基準",
    fieldFirstPerson: "一人称",
    fieldSecondPerson: "二人称",
    fieldTone: "口調",
    fieldSpeechSample: "口調サンプル",
    fieldLikes: "好き嫌い",
    fieldHabits: "癖・習慣",
    fieldsetPosition: "立場",
    fieldOccupation: "職業/所属",
    fieldSkills: "特殊能力/スキル",
    fieldsetOther: "その他",
    fieldBackground: "背景",
    fieldForbidden: "禁止事項",
    fieldOpeningScene: "開始時の状況",
    fieldsetExtra: "自由設定",
    hintExtra: "固定項目に収まらない情報を自由に追加できます。タイトルは任意です。",
    fieldStyle: "スタイル",

    presetNovelAI: "小説調（AI視点・一人称）",
    presetNovelUser: "小説調・ユーザー視点（三人称）",
    presetChat: "チャット調（地の文なし）",
    presetCustom: "カスタム...",

    btnGenerate: "SOUL/SKILL生成",
    btnConvert: "変換",
    btnExtractFields: "テキストからフィールド抽出",
    btnEstimate: "文体推定",
    btnRefine: "修正指示",
    btnSave: "保存",
    btnReset: "リセット",
    btnClear: "クリア",
    btnAddSection: "+ 追加",
    btnValidate: "ファイル確認",
    btnImport: "登録",

    labelFreetextPrompt: "キャラクター設定文（メモ・プロフィール等）",
    labelFreetextStyleDefault: "スタイル（省略時 = 小説調・三人称・AI視点）",
    labelSoulPaste: "SOUL.md テキスト（貼り付け）",
    labelSourceDir: "登録元フォルダ",
    hintSourceDir: "SOUL.md / SKILL.md / style.yaml を含むフォルダのパス。3ファイル揃っていれば即登録、不足分は自動生成します。",

    testChat: "テスト会話",
    testPlaceholder: "テストメッセージを入力...",
    testSend: "送信",
    btnHideEditor: "閉じる",

    savedHint: "クリックで読み込み、ダブルクリックで削除",
    draftNone: "未生成",
    statusReady: "準備完了",
    statusNeedText: "テキストを入力してください",

    /* error codes */
    err_api_key_missing: "APIキーが設定されていません。.envファイルを確認してください。",
    err_api_unauthorized: "APIキーが無効です。.envファイルの値を確認してください。",
    err_api_network: "APIサーバーに接続できません。ネットワークを確認してください。",
    err_api_timeout: "応答が返ってきませんでした。モデルが混雑しているか、リクエストが重すぎる可能性があります。しばらく待ってから再試行してください。",
    err_api_unknown: "APIエラーが発生しました。サーバーログを確認してください。",
  },

  en: {
    langToggle: "日本語",
    langLabel: "Language",

    styleTitle: "Style Preset",
    stylePersona: "Persona",
    styleCustom: "Custom...",
    styleStart: "Start Session",
    styleStarting: "Starting...",
    styleCustomStart: "Start with Custom",
    styleLoadError: "Load failed. Check if the server is running.",

    customViewpoint: "Narrator",
    customNarration: "Narration",
    customPerson: "Person",
    optAIChar: "AI char. view",
    optUserChar: "User char. view",
    optNarrationOn: "On",
    optNarrationOff: "Off",
    optFirstPerson: "First-person",
    optThirdPerson: "Third-person",

    memoryScope: "Memory Scope",
    optMemorySession: "This session only",
    optMemoryPersona: "All sessions",
    hintMemoryScope: "This session only: memory is not shared across sessions. All sessions: memory is shared across all sessions with the same character.",

    headerNotConnected: "Not connected",
    inputPlaceholder: "Type a message...",
    sendButton: "Send",
    btnState: "State",
    stateEmpty: "No changes",
    navSessions: "Sessions",
    navStudio: "Studio",
    navSettings: "Settings",
    statusConnected: "Connected",
    statusDisconnected: "Disconnected — reload to reconnect",
    roleYou: "You",
    roleAssistant: "Assistant",

    btnEdit: "Edit",
    btnRegenerate: "Regenerate",
    btnDelete: "Delete",
    btnEdited: "[Edited]",
    btnStop: "Stop",

    studioTitle: "Persona Studio",
    tabTemplate: "Fixed Form",
    tabFreetext: "Free Input",
    tabStyleOnly: "SOUL.md",
    tabSaved: "Persona List",
    tabDirect: "Direct Input",
    tabText: "Text Input",
    tabFile: "File Import",
    hintTemplate: "Basic character settings for all RP scenarios.",

    fieldPersonaId: "Persona ID (alphanumeric)",
    fieldsetBasic: "Basic Info",
    fieldName: "Name",
    fieldSex: "Biological Sex",
    fieldGender: "Gender Identity",
    fieldAge: "Age",
    fieldBirthday: "Birthday",
    fieldSpecies: "Species",
    fieldBlood: "Blood Type",
    fieldHeight: "Height",
    fieldWeight: "Weight",
    fieldBWH: "B / W / H",
    fieldsetAppearance: "Appearance",
    fieldHair: "Hair",
    fieldEyes: "Eyes",
    fieldSkin: "Skin",
    fieldClothing: "Clothing Style",
    fieldsetPersonality: "Character",
    fieldPersonality: "Personality",
    fieldPrinciples: "Principles / Decision Rules",
    fieldFirstPerson: "First-Person Pronoun",
    fieldSecondPerson: "Second-person",
    fieldTone: "Tone",
    fieldSpeechSample: "Speech Samples",
    fieldLikes: "Likes/Dislikes",
    fieldHabits: "Habits",
    fieldsetPosition: "Position",
    fieldOccupation: "Occupation/Affiliation",
    fieldSkills: "Abilities/Skills",
    fieldsetOther: "Other",
    fieldBackground: "Background",
    fieldForbidden: "Restrictions",
    fieldOpeningScene: "Opening Scene",
    fieldsetExtra: "Custom Settings",
    hintExtra: "Add information that doesn't fit the fixed fields. Titles are optional.",
    fieldStyle: "Style",

    presetNovelAI: "Novel (AI view, 1st-person)",
    presetNovelUser: "Novel (User view, 3rd-person)",
    presetChat: "Chat (no narration)",
    presetCustom: "Custom...",

    btnGenerate: "Generate SOUL/SKILL",
    btnConvert: "Convert",
    btnExtractFields: "Extract Fields from Text",
    btnEstimate: "Estimate Style",
    btnRefine: "Refine",
    btnSave: "Save",
    btnReset: "Reset",
    btnClear: "Clear",
    btnAddSection: "+ Add",
    btnValidate: "Check Files",
    btnImport: "Import",

    labelFreetextPrompt: "Character settings (notes, profile, etc.)",
    labelFreetextStyleDefault: "Style (default = novel, 3rd-person, AI view)",
    labelSoulPaste: "SOUL.md text (paste here)",
    labelSourceDir: "Source Folder",
    hintSourceDir: "Path to folder containing SOUL.md / SKILL.md / style.yaml. Missing files will be auto-generated on import.",

    testChat: "Test Chat",
    testPlaceholder: "Type test message...",
    testSend: "Send",
    btnHideEditor: "Close",

    savedHint: "Click to load, double-click to delete",
    draftNone: "Not generated",
    statusReady: "Ready",
    statusNeedText: "Please enter text",

    /* error codes */
    err_api_key_missing: "API key is not set. Check your .env file.",
    err_api_unauthorized: "API key is invalid. Check the value in your .env file.",
    err_api_network: "Cannot connect to API server. Check your network.",
    err_api_timeout: "No response received. The model may be busy or the request may be too heavy. Please wait and try again.",
    err_api_unknown: "An API error occurred. Check the server log.",
  }
};

const LANG_KEY = "rp-standalone-lang";

function getLang() {
  return localStorage.getItem(LANG_KEY) || "ja";
}

function setLang(lang) {
  localStorage.setItem(LANG_KEY, lang);
}

function t(key) {
  const lang = getLang();
  return I18N[lang]?.[key] || I18N["ja"][key] || key;
}

/** Apply i18n text and attributes to the current page. */
function i18nApply() {
  const lang = getLang();

  // data-i18n text
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (I18N[lang]?.[key]) el.textContent = I18N[lang][key];
  });

  // data-i18n-placeholder
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (I18N[lang]?.[key]) el.placeholder = I18N[lang][key];
  });

  // data-i18n-value (for <option>)
  document.querySelectorAll("[data-i18n-value]").forEach(el => {
    const key = el.getAttribute("data-i18n-value");
    if (I18N[lang]?.[key]) el.textContent = I18N[lang][key];
  });

  // i18n dynamic labels (preset-list radio spans, etc.)
  document.querySelectorAll("[data-i18n-label]").forEach(el => {
    const key = el.getAttribute("data-i18n-label");
    if (I18N[lang]?.[key]) el.textContent = I18N[lang][key];
  });
}

function toggleLang() {
  const next = getLang() === "ja" ? "en" : "ja";
  setLang(next);
  i18nApply();
  updateLangToggle();
}

function updateLangToggle() {
  const btn = document.getElementById("lang-toggle");
  if (!btn) return;
  const isJa = getLang() === "ja";
  btn.innerHTML = isJa
    ? '<span class="lang-active">日本語</span> / <span class="lang-inactive">English</span>'
    : '<span class="lang-inactive">日本語</span> / <span class="lang-active">English</span>';
}
