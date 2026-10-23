/* Words on the listener's screen, in the listener's language. A worker who
   scans a QR code reads every step in the language they picked first.
   Missing keys fall back to English. To add a language: add a block, keyed
   by the language id in server.py's LANGUAGES. scan = the line printed
   under the QR code on the meeting sheet (every language has it). */
window.VMI18N = {
  en: {wifi: 'Connect to the company Wi-Fi, then scan again.', choose: 'Choose your language', listen: 'LISTEN', listenHint: 'Tap to hear this meeting in your language. Headphones work.',
    live: 'Live now', startsAt: 'Starts', waiting: 'Waiting for the meeting to start. Keep this screen open — it starts by itself.',
    ended: 'The meeting has ended. Thank you.', leave: 'LEAVE', voiceOn: 'VOICE ON', voiceOff: 'VOICE OFF', more: 'MORE',
    here: 'The translation appears here when someone speaks.', removed: 'You were removed from this meeting.',
    newcode: 'This QR code has changed. Scan the new one.', reconnecting: 'Reconnecting…', connected: 'Connected',
    enterCode: 'Scan the meeting’s QR code with your camera, or type the code:', next: 'NEXT', staff: 'Staff sign-in',
    done: 'OK', keepOpen: 'Keep this app open. The screen stays on.', speed: 'Voice speed', orig: 'Show the original words',
    lang: 'Language', unavailable: 'Translation unavailable for this phrase.',
    scan: 'Scan to hear this meeting in your language'},
  es: {wifi: 'Con\u00e9ctese al Wi-Fi de la empresa y vuelva a escanear.', choose: 'Elija su idioma', listen: 'ESCUCHAR', listenHint: 'Toque para escuchar esta reunión en su idioma. Puede usar audífonos.',
    live: 'En vivo', startsAt: 'Empieza', waiting: 'Esperando a que empiece la reunión. Mantenga esta pantalla abierta; empezará sola.',
    ended: 'La reunión ha terminado. Gracias.', leave: 'SALIR', voiceOn: 'VOZ: SÍ', voiceOff: 'VOZ: NO', more: 'MÁS',
    here: 'La traducción aparece aquí cuando alguien habla.', removed: 'Lo retiraron de esta reunión.',
    newcode: 'Este código QR cambió. Escanee el nuevo.', reconnecting: 'Reconectando…', connected: 'Conectado',
    enterCode: 'Escanee el código QR de la reunión con la cámara o escriba el código:', next: 'SIGUIENTE', staff: 'Acceso del personal',
    done: 'ACEPTAR', keepOpen: 'Mantenga esta aplicación abierta. La pantalla seguirá encendida.', speed: 'Velocidad de la voz',
    orig: 'Mostrar las palabras originales', lang: 'Idioma', unavailable: 'No hay traducción para esta frase.',
    scan: 'Escanee para escuchar esta reunión en su idioma'},
  fr: {wifi: 'Connectez-vous au Wi-Fi de l\u2019entreprise, puis scannez \u00e0 nouveau.', choose: 'Choisissez votre langue', listen: 'ÉCOUTER', listenHint: 'Touchez pour écouter cette réunion dans votre langue. Les écouteurs fonctionnent.',
    live: 'En direct', startsAt: 'Commence', waiting: 'En attente du début de la réunion. Gardez cet écran ouvert ; elle démarrera toute seule.',
    ended: 'La réunion est terminée. Merci.', leave: 'QUITTER', voiceOn: 'VOIX : OUI', voiceOff: 'VOIX : NON', more: 'PLUS',
    here: 'La traduction s’affiche ici quand quelqu’un parle.', removed: 'Vous avez été retiré de cette réunion.',
    newcode: 'Ce code QR a changé. Scannez le nouveau.', reconnecting: 'Reconnexion…', connected: 'Connecté',
    enterCode: 'Scannez le code QR de la réunion avec l’appareil photo, ou tapez le code :', next: 'SUIVANT', staff: 'Connexion du personnel',
    done: 'OK', keepOpen: 'Gardez cette application ouverte. L’écran reste allumé.', speed: 'Vitesse de la voix',
    orig: 'Afficher les mots d’origine', lang: 'Langue', unavailable: 'Traduction indisponible pour cette phrase.',
    scan: 'Scannez pour écouter cette réunion dans votre langue'},
  sw: {wifi: 'Unganisha kwenye Wi-Fi ya kampuni, kisha changanua tena.', choose: 'Chagua lugha yako', listen: 'SIKILIZA', listenHint: 'Gusa ili kusikiliza mkutano huu kwa lugha yako. Unaweza kutumia vipokea sauti.',
    live: 'Unaendelea sasa', startsAt: 'Unaanza', waiting: 'Tunasubiri mkutano uanze. Acha skrini hii wazi; utaanza wenyewe.',
    ended: 'Mkutano umekwisha. Asante.', leave: 'TOKA', voiceOn: 'SAUTI: IMEWASHWA', voiceOff: 'SAUTI: IMEZIMWA', more: 'ZAIDI',
    here: 'Tafsiri itaonekana hapa mtu anapozungumza.', removed: 'Umeondolewa kwenye mkutano huu.',
    newcode: 'Msimbo huu wa QR umebadilika. Changanua mpya.', reconnecting: 'Inaunganisha upya…', connected: 'Imeunganishwa',
    enterCode: 'Changanua msimbo wa QR wa mkutano kwa kamera, au andika msimbo:', next: 'ENDELEA', staff: 'Kuingia kwa wafanyakazi',
    done: 'SAWA', keepOpen: 'Acha programu hii wazi. Skrini itabaki imewaka.', speed: 'Kasi ya sauti',
    orig: 'Onyesha maneno asilia', lang: 'Lugha', unavailable: 'Hakuna tafsiri ya sentensi hii.',
    scan: 'Changanua ili usikilize mkutano huu kwa lugha yako'},
  pl: {scan: 'Zeskanuj, aby słuchać tego spotkania w swoim języku', choose: 'Wybierz swój język', listen: 'SŁUCHAJ'},
  de: {scan: 'Scannen, um diese Besprechung in Ihrer Sprache zu hören', choose: 'Wählen Sie Ihre Sprache', listen: 'ANHÖREN'},
  zh: {scan: '扫码，用您的语言收听本次会议', choose: '选择您的语言', listen: '收听'},
  pt: {scan: 'Escaneie para ouvir esta reunião no seu idioma', choose: 'Escolha o seu idioma', listen: 'OUVIR'},
  vi: {scan: 'Quét mã để nghe cuộc họp này bằng ngôn ngữ của bạn', choose: 'Chọn ngôn ngữ của bạn', listen: 'NGHE'},
  ar: {scan: 'امسح الرمز للاستماع إلى هذا الاجتماع بلغتك', choose: 'اختر لغتك', listen: 'استماع'},
  hi: {scan: 'इस बैठक को अपनी भाषा में सुनने के लिए स्कैन करें', choose: 'अपनी भाषा चुनें', listen: 'सुनें'},
  ru: {scan: 'Отсканируйте, чтобы слушать эту встречу на своём языке', choose: 'Выберите язык', listen: 'СЛУШАТЬ'},
  uk: {scan: 'Відскануйте, щоб слухати цю зустріч своєю мовою', choose: 'Оберіть мову', listen: 'СЛУХАТИ'},
  ko: {scan: '스캔하여 이 회의를 원하는 언어로 들으세요', choose: '언어를 선택하세요', listen: '듣기'},
  ja: {scan: 'スキャンすると、この会議をあなたの言語で聞けます', choose: '言語を選んでください', listen: '聞く'},
  it: {scan: 'Scansiona per ascoltare questa riunione nella tua lingua', choose: 'Scegli la tua lingua', listen: 'ASCOLTA'},
  tl: {scan: 'I-scan para marinig ang pulong na ito sa iyong wika', choose: 'Piliin ang iyong wika', listen: 'MAKINIG'},
  ro: {scan: 'Scanați pentru a asculta această ședință în limba dvs.', choose: 'Alegeți limba', listen: 'ASCULTAȚI'}
};
window.VMT = function (lang, key) {
  const t = window.VMI18N;
  return (t[lang] && t[lang][key]) || t.en[key] || key;
};
