import websocket
import json
import time
import threading
import requests
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template_string

# ============================================================
# KONFIGURACJA
# ============================================================
TELEGRAM_TOKEN = "8679402917:AAG3eqNpk1U00VpnQnlVkzYa1qRUWR-_7Y0"   # ← WKLEJ SWÓJ TOKEN!
CHAT_ID = "6655163131"                    # ← Twój Chat ID

# Serwer XTB (tylko do cen - nie do tradowania!)
SERVER_STREAM = "wss://ws.xapi.pro/demoStream"
SERVER_MAIN   = "wss://ws.xapi.pro/demo"
LOGIN    = "21014080"
HASLO    = os.environ.get("XTB_HASLO")            # ← WKLEJ HASŁO XTB!

MIN_SILA_SYGNALU = 70    # Minimalna siła sygnału (0-100)
PORT = 8080

SYMBOLE = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "EURGBP",
    "GOLD", "SILVER", "OIL",
    "US100", "US500", "DE40",
    "BITCOIN", "ETHEREUM",
]

SESJE = {
    "Krypto 24/7": {"zawsze_otwarty": True},
    "Forex 24/5":  {"zawsze_otwarty": True},
    "Azja":        {"otwarcie_utc": 0,  "zamkniecie_utc": 6},
    "Europa":      {"otwarcie_utc": 7,  "zamkniecie_utc": 15},
    "USA":         {"otwarcie_utc": 13, "zamkniecie_utc": 20},
}

# ============================================================
# Stan
sesja_id = None
ws_main_conn = None
ceny_historia = {}
wyslane_sygnaly = {}    # Żeby nie wysyłać tego samego sygnału dwa razy
ostatnie_ceny = {}
polaczony = False
logi = []
sygnaly_historia = []
lock = threading.Lock()

# ============================================================
# TELEGRAM
# ============================================================
def wyslij_telegram(tekst):
    """Wysyła wiadomość na Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": tekst,
            "parse_mode": "HTML"
        }, timeout=10)
        if r.status_code == 200:
            loguj("✅ Wysłano sygnał na Telegram!", "OK")
        else:
            loguj(f"Błąd Telegram: {r.text}", "BLAD")
    except Exception as e:
        loguj(f"Błąd Telegram: {e}", "BLAD")

def formatuj_sygnal(sym, kier, cena, sila, sl, tp, uzasadnienie):
    """Formatuje ładną wiadomość na Telegram"""
    emoji_kier = "🟢 BUY" if kier == "BUY" else "🔴 SELL"
    gwiazdki = "⭐" * (1 + int(sila / 25))  # 1-4 gwiazdki

    return f"""
📊 <b>SYGNAŁ TRADINGOWY</b> {gwiazdki}
━━━━━━━━━━━━━━━━━━━━
<b>Symbol:</b> {sym}
<b>Kierunek:</b> {emoji_kier}
<b>Cena wejścia:</b> <code>{cena}</code>
<b>Stop Loss:</b> <code>{sl}</code> 🛑
<b>Take Profit:</b> <code>{tp}</code> 🎯
<b>Siła sygnału:</b> {sila}/100
━━━━━━━━━━━━━━━━━━━━
<b>Powód:</b> {uzasadnienie}
━━━━━━━━━━━━━━━━━━━━
⏰ {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}
⚠️ <i>To sygnał informacyjny, nie porada inwestycyjna!</i>
"""

def formatuj_aktualizacje():
    """Wysyła co godzinę podsumowanie rynku"""
    linie = ["📈 <b>PODSUMOWANIE RYNKU</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for sym, cena in list(ostatnie_ceny.items())[:10]:
        hist = ceny_historia.get(sym, [])
        if len(hist) >= 2:
            zmiana = (hist[-1] - hist[0]) / hist[0] * 100
            emoji = "🟢" if zmiana >= 0 else "🔴"
            linie.append(f"{emoji} <b>{sym}:</b> {cena:.5f} ({'+' if zmiana >= 0 else ''}{zmiana:.3f}%)")
    linie.append(f"\n⏰ {datetime.now().strftime('%H:%M %d.%m.%Y')}")
    return "\n".join(linie)

# ============================================================
# ANALIZA SYGNAŁÓW
# ============================================================
def analizuj(sym, cena_ask, cena_bid):
    """Analizuje symbol i zwraca sygnał z uzasadnieniem"""
    if sym not in ceny_historia: ceny_historia[sym] = []
    ceny_historia[sym].append(cena_ask)
    if len(ceny_historia[sym]) < 20: return None, 0, ""
    ceny_historia[sym] = ceny_historia[sym][-50:]
    c = ceny_historia[sym]

    # Średnie kroczące
    sma5  = sum(c[-5:])  / 5
    sma10 = sum(c[-10:]) / 10
    sma20 = sum(c[-20:]) / 20

    # RSI
    zm = [c[i]-c[i-1] for i in range(1, len(c))]
    w = [z for z in zm[-14:] if z > 0]
    s = [abs(z) for z in zm[-14:] if z < 0]
    aw = sum(w)/len(w) if w else 0
    as_ = sum(s)/len(s) if s else 0.0001
    rsi = 100-(100/(1+aw/as_))

    # Momentum
    mom = (c[-1]-c[-5])/c[-5]*100

    # Spread
    spread = (cena_ask-cena_bid)/cena_bid*100

    sila = 0
    kierunek = None
    powody = []

    if sma5 > sma10 > sma20:
        sila += 35
        powody.append("Wszystkie trendy rosną (SMA5>SMA10>SMA20)")
        if rsi < 70:
            sila += 20
            powody.append(f"RSI={rsi:.0f} — nie wykupiony")
        if rsi < 40:
            sila += 15
            powody.append("RSI wyprzedany — sygnał odbicia!")
        if mom > 0:
            sila += 20
            powody.append(f"Momentum={mom:.3f}% w górę")
        if spread < 0.05:
            sila += 10
            powody.append("Mały spread — dobra płynność")
        kierunek = "BUY"

    elif sma5 < sma10 < sma20:
        sila += 35
        powody.append("Wszystkie trendy spadają (SMA5<SMA10<SMA20)")
        if rsi > 30:
            sila += 20
            powody.append(f"RSI={rsi:.0f} — nie wyprzedany")
        if rsi > 60:
            sila += 15
            powody.append("RSI wykupiony — sygnał spadku!")
        if mom < 0:
            sila += 20
            powody.append(f"Momentum={mom:.3f}% w dół")
        if spread < 0.05:
            sila += 10
            powody.append("Mały spread — dobra płynność")
        kierunek = "SELL"

    uzasadnienie = " | ".join(powody) if powody else ""
    return kierunek, sila, uzasadnienie

def oblicz_sl_tp(cena, kier):
    if kier == "BUY":
        return round(cena*0.995, 5), round(cena*1.01, 5)
    return round(cena*1.005, 5), round(cena*0.99, 5)

def czy_nowy_sygnal(sym, kier, cena):
    """Sprawdza czy to nowy sygnał (nie duplikat)"""
    klucz = f"{sym}_{kier}"
    ostatni = wyslane_sygnaly.get(klucz)
    teraz = time.time()

    # Wyślij tylko jeśli nie wysyłano w ostatniej godzinie
    # lub cena zmieniła się o więcej niż 0.5%
    if ostatni is None:
        wyslane_sygnaly[klucz] = {"czas": teraz, "cena": cena}
        return True

    czas_od_ostatniego = teraz - ostatni["czas"]
    zmiana_ceny = abs(cena - ostatni["cena"]) / ostatni["cena"] * 100

    if czas_od_ostatniego > 3600 or zmiana_ceny > 0.5:
        wyslane_sygnaly[klucz] = {"czas": teraz, "cena": cena}
        return True

    return False

# ============================================================
# DASHBOARD HTML
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Signals Bot</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');
  * { margin:0; padding:0; box-sizing:border-box; }
  :root { --bg:#080c12; --panel:#0d1420; --border:#1a2535; --green:#00ff88; --red:#ff3355; --blue:#00aaff; --yellow:#ffcc00; --text:#c8d8f0; --muted:#4a6080; }
  body { background:var(--bg); color:var(--text); font-family:'Space Mono',monospace; padding:20px; }
  body::before { content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,170,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,170,255,0.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }
  .wrap { position:relative; z-index:1; max-width:1400px; margin:0 auto; }
  header { display:flex; justify-content:space-between; align-items:center; padding:20px 0; border-bottom:1px solid var(--border); margin-bottom:24px; }
  .logo { font-family:'Syne',sans-serif; font-size:24px; font-weight:800; }
  .logo span { color:var(--green); }
  .sb { display:flex; gap:16px; align-items:center; font-size:12px; color:var(--muted); }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 8px var(--green); animation:pulse 2s infinite; }
  .dot.off { background:var(--red); box-shadow:0 0 8px var(--red); animation:none; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .g4 { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:16px; }
  .g2 { display:grid; grid-template-columns:1.5fr 1fr; gap:16px; margin-bottom:16px; }
  .p { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:20px; position:relative; overflow:hidden; }
  .p::before { content:''; position:absolute; top:0; left:0; right:0; height:2px; background:linear-gradient(90deg,var(--blue),transparent); }
  .pt { font-family:'Syne',sans-serif; font-size:11px; font-weight:600; letter-spacing:2px; text-transform:uppercase; color:var(--muted); margin-bottom:16px; }
  .sv { font-family:'Syne',sans-serif; font-size:36px; font-weight:800; line-height:1; margin-bottom:6px; }
  .sl { font-size:11px; color:var(--muted); }
  .green{color:var(--green)} .red{color:var(--red)} .blue{color:var(--blue)} .yellow{color:var(--yellow)}
  .sygnal { background:rgba(255,255,255,.03); border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:10px; }
  .sygnal-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
  .sygnal-sym { font-family:'Syne',sans-serif; font-size:18px; font-weight:800; }
  .sygnal-info { font-size:11px; color:var(--muted); margin-top:4px; line-height:1.6; }
  .sygnal-sila { font-size:11px; margin-top:6px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:700; margin-left:8px; }
  .bb { background:rgba(0,255,136,.15); color:var(--green); }
  .bs { background:rgba(255,51,85,.15); color:var(--red); }
  .sila-bar { background:rgba(255,255,255,.05); border-radius:4px; height:4px; margin-top:6px; overflow:hidden; }
  .sila-fill { height:100%; border-radius:4px; background:linear-gradient(90deg,var(--red),var(--yellow),var(--green)); }
  .ceny-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .cena-item { background:rgba(255,255,255,.03); border:1px solid var(--border); border-radius:6px; padding:10px; }
  .cena-sym { font-size:11px; color:var(--muted); }
  .cena-val { font-family:'Syne',sans-serif; font-size:14px; font-weight:700; margin-top:2px; }
  .cena-zmiana { font-size:10px; margin-top:2px; }
  .sesja { display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border); font-size:12px; }
  .sesja:last-child { border-bottom:none; }
  .ss { padding:3px 10px; border-radius:20px; font-size:10px; font-weight:700; }
  .so { background:rgba(0,255,136,.15); color:var(--green); }
  .sc { background:rgba(74,96,128,.3); color:var(--muted); }
  #logs { height:200px; overflow-y:auto; font-size:11px; line-height:1.8; }
  .le { padding:2px 0; border-bottom:1px solid rgba(255,255,255,.03); }
  .lt { color:var(--muted); margin-right:8px; }
  .lb{color:var(--green)} .ls{color:var(--red)} .lo{color:var(--blue)} .le2{color:#ff6688}
  .brak { color:var(--muted); font-size:12px; text-align:center; padding:20px; }
  .tg-badge { background:rgba(0,170,255,.15); color:var(--blue); padding:4px 12px; border-radius:20px; font-size:11px; }
  @media(max-width:1000px){.g4{grid-template-columns:repeat(2,1fr)}.g2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">📊 <span>SIGNALS</span>BOT <span style="font-size:12px;color:var(--muted)">Telegram</span></div>
    <div class="sb">
      <div class="dot" id="dot"></div>
      <span id="status">Łączenie...</span>
      <span class="tg-badge">📱 Telegram aktywny</span>
      <span>|</span>
      <span id="clock">--:--:--</span>
    </div>
  </header>

  <div class="g4">
    <div class="p">
      <div class="pt">Sygnały wysłane</div>
      <div class="sv blue" id="ile-syg">0</div>
      <div class="sl">na Telegram dzisiaj</div>
    </div>
    <div class="p">
      <div class="pt">Skanowane symbole</div>
      <div class="sv green" id="ile-sym">0</div>
      <div class="sl">instrumentów</div>
    </div>
    <div class="p">
      <div class="pt">Ostatni sygnał</div>
      <div class="sv yellow" id="ostatni-sym">--</div>
      <div class="sl" id="ostatni-czas">czekam na sygnał...</div>
    </div>
    <div class="p">
      <div class="pt">Min. siła sygnału</div>
      <div class="sv" id="min-sila">{{ min_sila }}</div>
      <div class="sl">z 100 punktów</div>
    </div>
  </div>

  <div class="g2">
    <!-- Sygnały -->
    <div>
      <div class="p" style="margin-bottom:16px">
        <div class="pt">Ostatnie sygnały → Telegram</div>
        <div id="sygnaly-lista"><div class="brak">Czekam na sygnały... (analizuję rynek)</div></div>
      </div>
      <div class="p">
        <div class="pt">Logi bota</div>
        <div id="logs"></div>
      </div>
    </div>

    <!-- Prawy panel -->
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="p">
        <div class="pt">Ceny na żywo</div>
        <div class="ceny-grid" id="ceny-lista"></div>
      </div>
      <div class="p">
        <div class="pt">Sesje giełdowe</div>
        <div id="sesje"></div>
      </div>
    </div>
  </div>
</div>

<script>
setInterval(()=>{ document.getElementById('clock').textContent=new Date().toLocaleTimeString('pl-PL'); },1000);

async function upd() {
  try {
    const d = await (await fetch('/api/status')).json();
    document.getElementById('dot').className='dot'+(d.polaczony?'':' off');
    document.getElementById('status').textContent=d.polaczony?'Połączony z rynkiem ✓':'Rozłączony';
    document.getElementById('ile-syg').textContent=d.sygnaly_wyslane;
    document.getElementById('ile-sym').textContent=d.symbole;

    if(d.ostatni_sygnal){
      document.getElementById('ostatni-sym').textContent=d.ostatni_sygnal.symbol;
      document.getElementById('ostatni-sym').className='sv '+(d.ostatni_sygnal.kierunek==='BUY'?'green':'red');
      document.getElementById('ostatni-czas').textContent=d.ostatni_sygnal.czas;
    }

    // Sygnały
    const sl=document.getElementById('sygnaly-lista');
    if(!d.sygnaly.length){ sl.innerHTML='<div class="brak">Czekam na sygnały... (analizuję rynek)</div>'; }
    else {
      sl.innerHTML=d.sygnaly.slice(-8).reverse().map(s=>{
        const k=s.kierunek==='BUY';
        return `<div class="sygnal">
          <div class="sygnal-header">
            <span class="sygnal-sym">${s.symbol}<span class="badge ${k?'bb':'bs'}">${s.kierunek}</span></span>
            <span style="font-size:11px;color:var(--muted)">${s.czas}</span>
          </div>
          <div class="sygnal-info">
            Wejście: <b>${s.cena}</b> | SL: ${s.sl} | TP: ${s.tp}
          </div>
          <div class="sygnal-sila">Siła: <b>${s.sila}/100</b></div>
          <div class="sila-bar"><div class="sila-fill" style="width:${s.sila}%"></div></div>
          <div class="sygnal-info" style="margin-top:6px;font-size:10px">${s.uzasadnienie}</div>
        </div>`;
      }).join('');
    }

    // Ceny
    const cl=document.getElementById('ceny-lista');
    cl.innerHTML=Object.entries(d.ceny).map(([sym,info])=>{
      const k=info.zmiana>=0;
      return `<div class="cena-item">
        <div class="cena-sym">${sym}</div>
        <div class="cena-val">${info.cena?.toFixed(5)||'--'}</div>
        <div class="cena-zmiana ${k?'green':'red'}">${k?'+':''}${info.zmiana?.toFixed(3)||'0'}%</div>
      </div>`;
    }).join('');

    // Sesje
    document.getElementById('sesje').innerHTML=d.sesje.map(s=>
      `<div class="sesja"><span>${s.nazwa}</span><span class="ss ${s.otwarta?'so':'sc'}">${s.otwarta?'● OTWARTA':'○ ZAMKNIĘTA'}</span></div>`
    ).join('');

    // Logi
    const le=document.getElementById('logs');
    if(d.logi.length){
      le.innerHTML=d.logi.slice(-50).reverse().map(l=>{
        let c='';
        if(l.includes('🟢'))c='lb'; else if(l.includes('🔴'))c='ls';
        else if(l.includes('✅'))c='lo'; else if(l.includes('❌'))c='le2';
        const m=l.match(/\[(\d+:\d+:\d+)\] (.+)/);
        if(m) return `<div class="le"><span class="lt">${m[1]}</span><span class="${c}">${m[2]}</span></div>`;
        return `<div class="le ${c}">${l}</div>`;
      }).join('');
    }
  } catch(e) { document.getElementById('dot').className='dot off'; }
}
upd();
setInterval(upd,3000);
</script>
</body>
</html>
"""

app = Flask(__name__)

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML, min_sila=MIN_SILA_SYGNALU)

@app.route('/api/status')
def api_status():
    with lock:
        ceny_info = {}
        for sym, hist in list(ceny_historia.items())[:14]:
            if hist:
                cena = hist[-1]
                zmiana = (hist[-1]-hist[0])/hist[0]*100 if len(hist) > 1 else 0
                ceny_info[sym] = {"cena": cena, "zmiana": round(zmiana,3)}

        ostatni = sygnaly_historia[-1] if sygnaly_historia else None
        sesje_status = [{"nazwa": n, "otwarta": czy_sesja_otwarta(i)} for n,i in SESJE.items()]

        return jsonify({
            "polaczony": polaczony,
            "symbole": len(SYMBOLE),
            "sygnaly_wyslane": len(sygnaly_historia),
            "sygnaly": sygnaly_historia,
            "ostatni_sygnal": ostatni,
            "ceny": ceny_info,
            "sesje": sesje_status,
            "logi": logi[-100:]
        })

# ============================================================
# POMOCNICZE
# ============================================================
def czy_sesja_otwarta(info):
    if info.get("zawsze_otwarty"): return True
    if datetime.now(timezone.utc).weekday() >= 5: return False
    h = datetime.now(timezone.utc).hour
    return info.get("otwarcie_utc",0) <= h < info.get("zamkniecie_utc",24)

def loguj(msg, typ="INFO"):
    ikony = {"INFO":"ℹ️","KUP":"🟢","SPRZEDAJ":"🔴","BLAD":"❌","OK":"✅","PORTFEL":"💰","TG":"📱"}
    tekst = f"[{datetime.now().strftime('%H:%M:%S')}] {ikony.get(typ,'ℹ️')} {msg}"
    print(tekst)
    logi.append(tekst)
    if len(logi)>500: logi.pop(0)

# ============================================================
# STREAM - Pobieranie cen
# ============================================================
def uruchom_stream():
    def on_open(ws):
        loguj("Stream połączony! Subskrybuję ceny...", "OK")
        for sym in SYMBOLE:
            ws.send(json.dumps({
                "command": "getTickPrices",
                "streamSessionId": sesja_id,
                "symbol": sym,
                "minArrivalTime": 10000,
                "maxLevel": 2
            }))
            time.sleep(0.1)
        loguj(f"Subskrybowano {len(SYMBOLE)} symboli!", "OK")

    def on_msg(ws, msg):
        try:
            d = json.loads(msg)
            if d.get("command") == "tickPrices":
                td = d.get("data",{})
                sym = td.get("symbol")
                ask = td.get("ask")
                bid = td.get("bid")
                if sym and ask and bid:
                    ostatnie_ceny[sym] = ask
                    if sym not in ceny_historia: ceny_historia[sym] = []
                    ceny_historia[sym].append(ask)
                    ceny_historia[sym] = ceny_historia[sym][-50:]

                    # Analizuj i wyślij sygnał jeśli mocny
                    kier, sila, uzas = analizuj(sym, ask, bid)
                    if kier and sila >= MIN_SILA_SYGNALU:
                        if czy_nowy_sygnal(sym, kier, ask):
                            sl, tp = oblicz_sl_tp(ask, kier)
                            loguj(f"SYGNAŁ {kier} {sym} siła:{sila}/100", "KUP" if kier=="BUY" else "SPRZEDAJ")

                            # Wyślij na Telegram
                            tekst = formatuj_sygnal(sym, kier, ask, sila, sl, tp, uzas)
                            threading.Thread(target=wyslij_telegram, args=(tekst,), daemon=True).start()

                            # Zapisz do historii
                            with lock:
                                sygnaly_historia.append({
                                    "symbol": sym, "kierunek": kier,
                                    "cena": ask, "sl": sl, "tp": tp,
                                    "sila": sila, "uzasadnienie": uzas,
                                    "czas": datetime.now().strftime("%H:%M %d.%m")
                                })
                                if len(sygnaly_historia) > 100: sygnaly_historia.pop(0)
        except: pass

    def on_err(ws, e): loguj(f"Błąd streamu: {e}","BLAD")

    def on_close(ws, *a):
        loguj("Stream rozłączony - restartuję...","BLAD")
        time.sleep(10)
        if sesja_id: threading.Thread(target=uruchom_stream, daemon=True).start()

    websocket.WebSocketApp(
        SERVER_STREAM,
        on_open=on_open, on_message=on_msg,
        on_error=on_err, on_close=on_close
    ).run_forever(ping_interval=30)

# ============================================================
# GŁÓWNE POŁĄCZENIE
# ============================================================
def on_msg_main(ws, msg):
    global sesja_id, polaczony

    try: d = json.loads(msg)
    except: return

    if d.get("status")==True and "streamSessionId" in d:
        sesja_id = d["streamSessionId"]
        polaczony = True
        loguj("Zalogowano! Startuje stream cen...", "OK")
        threading.Thread(target=uruchom_stream, daemon=True).start()

    elif d.get("status")==False:
        loguj(f"Błąd: {d.get('errorDescr','')}", "BLAD")

def on_err_main(ws, e):
    global polaczony
    polaczony = False

def on_close_main(ws, *a):
    global polaczony
    polaczony = False
    loguj("Rozłączono - restartuję...", "BLAD")

def on_open_main(ws):
    global ws_main_conn
    ws_main_conn = ws
    loguj("Połączono z serwerem!")
    ws.send(json.dumps({"command":"login","arguments":{"userId":LOGIN,"password":HASLO}}))

def uruchom_bota():
    while True:
        try:
            websocket.WebSocketApp(
                SERVER_MAIN,
                on_open=on_open_main, on_message=on_msg_main,
                on_error=on_err_main, on_close=on_close_main
            ).run_forever(ping_interval=30)
        except Exception as e:
            loguj(f"Błąd: {e}","BLAD")
        time.sleep(15)

def podsumowanie_co_godzine():
    """Co godzinę wysyła podsumowanie rynku na Telegram"""
    while True:
        time.sleep(3600)
        try:
            tekst = formatuj_aktualizacje()
            wyslij_telegram(tekst)
            loguj("Wysłano godzinne podsumowanie na Telegram", "TG")
        except Exception as e:
            loguj(f"Błąd podsumowania: {e}", "BLAD")

if __name__ == "__main__":
    print("="*60)
    print("📊 TRADING SIGNALS BOT → Telegram")
    print("="*60)
    print(f"📱 Chat ID: {CHAT_ID}")
    print(f"🔍 Min. siła sygnału: {MIN_SILA_SYGNALU}/100")
    print(f"📈 Symbole: {', '.join(SYMBOLE)}")
    print(f"🌐 Dashboard: http://127.0.0.1:{PORT}")
    print("="*60)

    # Wyślij wiadomość startową na Telegram
    wyslij_telegram(f"""
🚀 <b>BOT STARTUJE!</b>
━━━━━━━━━━━━━━━━━━━━
📈 Skanuję {len(SYMBOLE)} instrumentów
🎯 Min. siła sygnału: {MIN_SILA_SYGNALU}/100
⏰ Start: {datetime.now().strftime('%H:%M %d.%m.%Y')}
━━━━━━━━━━━━━━━━━━━━
Będę wysyłał sygnały gdy znajdę okazję!
    """)

    threading.Thread(target=uruchom_bota, daemon=True).start()
    threading.Thread(target=podsumowanie_co_godzine, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=False)
