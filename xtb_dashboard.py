import websocket
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template_string
import os

# ============================================================
# KONFIGURACJA
# ============================================================
LOGIN = "TWÓJ_LOGIN_XTB"
HASLO = "TWOJE_HASLO_XTB"
DEMO = True
BUDZET_MAX = 1500
WOLUMEN = 0.01
STOP_LOSS_PROCENT = 0.5
TAKE_PROFIT_PROCENT = 1.0
MAX_POZYCJI_KROTKICH = 3
MAX_POZYCJI_DLUGICH = 2
MIN_SILA_SYGNALU = 65
SYMBOLE_NA_RAZ = 100
ROTACJA_CO = 5
PORT_DASHBOARD = 8080

SERVER = "wss://ws.xtb.com/demo" if DEMO else "wss://ws.xtb.com/real"

# Stan globalny
sesja_id = None
grupy = []
obecna_grupa = 0
ceny_historia = {}
pozycje_krotkie = {}
pozycje_dlugie = {}
saldo_konta = 0
uzyte_saldo = 0
ws_global = None
lock = threading.Lock()
logi = []  # Historia logów dla dashboardu
historia_transakcji = []  # Zamknięte transakcje
start_czas = datetime.now()

SESJE = {
    "Krypto 24/7": {"zawsze_otwarty": True, "symbole": ["BITCOIN", "ETHEREUM"]},
    "Forex 24/5":  {"zawsze_otwarty": True, "symbole": ["EURUSD", "GBPUSD"]},
    "Azja":        {"otwarcie_utc": 0,  "zamkniecie_utc": 6,  "symbole": ["JP225"]},
    "Europa":      {"otwarcie_utc": 7,  "zamkniecie_utc": 15, "symbole": ["W20", "DE40"]},
    "USA":         {"otwarcie_utc": 13, "zamkniecie_utc": 20, "symbole": ["US500", "US100"]},
    "Surowce":     {"otwarcie_utc": 1,  "zamkniecie_utc": 23, "symbole": ["GOLD", "OIL"]},
}

# ============================================================
# DASHBOARD HTML
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XTB Trading Bot</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg: #080c12;
    --panel: #0d1420;
    --border: #1a2535;
    --green: #00ff88;
    --red: #ff3355;
    --blue: #00aaff;
    --yellow: #ffcc00;
    --text: #c8d8f0;
    --muted: #4a6080;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    min-height: 100vh;
    padding: 20px;
  }

  /* Siatka tła */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(0,170,255,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,170,255,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  .container { position: relative; z-index: 1; max-width: 1400px; margin: 0 auto; }

  /* HEADER */
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
  }

  .logo {
    font-family: 'Syne', sans-serif;
    font-size: 24px;
    font-weight: 800;
    letter-spacing: -1px;
  }

  .logo span { color: var(--green); }

  .status-bar {
    display: flex;
    gap: 20px;
    align-items: center;
    font-size: 12px;
    color: var(--muted);
  }

  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }

  .dot.offline { background: var(--red); box-shadow: 0 0 8px var(--red); animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  /* GRID */
  .grid { display: grid; gap: 16px; }
  .grid-4 { grid-template-columns: repeat(4, 1fr); }
  .grid-3 { grid-template-columns: repeat(3, 1fr); }
  .grid-2 { grid-template-columns: 2fr 1fr; }

  /* PANEL */
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    position: relative;
    overflow: hidden;
  }

  .panel::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--blue), transparent);
  }

  .panel-title {
    font-family: 'Syne', sans-serif;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 16px;
  }

  /* STAT CARDS */
  .stat-value {
    font-family: 'Syne', sans-serif;
    font-size: 36px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 6px;
  }

  .stat-label { font-size: 11px; color: var(--muted); }
  .stat-sub { font-size: 13px; color: var(--muted); margin-top: 8px; }

  .green { color: var(--green); }
  .red { color: var(--red); }
  .blue { color: var(--blue); }
  .yellow { color: var(--yellow); }

  /* POZYCJE */
  .pozycja {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: center;
    transition: border-color 0.3s;
  }

  .pozycja:hover { border-color: var(--blue); }
  .pozycja-symbol { font-family: 'Syne', sans-serif; font-size: 16px; font-weight: 700; }
  .pozycja-typ { font-size: 10px; letter-spacing: 1px; color: var(--muted); margin-top: 2px; }
  .pozycja-info { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .pozycja-pnl { font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 700; text-align: right; }
  .pozycja-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
  }
  .badge-buy { background: rgba(0,255,136,0.15); color: var(--green); }
  .badge-sell { background: rgba(255,51,85,0.15); color: var(--red); }
  .badge-long { background: rgba(0,170,255,0.15); color: var(--blue); }

  /* SESJE */
  .sesja {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .sesja:last-child { border-bottom: none; }
  .sesja-status {
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
  }
  .sesja-open { background: rgba(0,255,136,0.15); color: var(--green); }
  .sesja-closed { background: rgba(74,96,128,0.3); color: var(--muted); }

  /* LOGI */
  #log-container {
    height: 280px;
    overflow-y: auto;
    font-size: 11px;
    line-height: 1.8;
  }

  #log-container::-webkit-scrollbar { width: 4px; }
  #log-container::-webkit-scrollbar-track { background: transparent; }
  #log-container::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .log-entry { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .log-time { color: var(--muted); margin-right: 8px; }
  .log-buy { color: var(--green); }
  .log-sell { color: var(--red); }
  .log-ok { color: var(--blue); }
  .log-err { color: #ff6688; }

  /* HISTORIA */
  .trade {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    align-items: center;
  }
  .trade:last-child { border-bottom: none; }

  /* PROGRESS BAR */
  .progress-bar {
    background: rgba(255,255,255,0.05);
    border-radius: 4px;
    height: 6px;
    margin-top: 10px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    border-radius: 4px;
    background: linear-gradient(90deg, var(--blue), var(--green));
    transition: width 0.5s ease;
  }

  /* UPTIME */
  .uptime { font-size: 11px; color: var(--muted); }

  /* RESPONSIVE */
  @media (max-width: 1000px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .grid-3 { grid-template-columns: 1fr; }
    .grid-2 { grid-template-columns: 1fr; }
  }

  /* ANIMACJE */
  .panel { animation: fadeIn 0.5s ease forwards; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  .panel:nth-child(2) { animation-delay: 0.1s; }
  .panel:nth-child(3) { animation-delay: 0.2s; }
  .panel:nth-child(4) { animation-delay: 0.3s; }

  .brak { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }
</style>
</head>
<body>
<div class="container">

  <!-- HEADER -->
  <header>
    <div class="logo">XTB<span>BOT</span> <span style="font-size:13px;color:var(--muted);font-weight:400">{{ "DEMO" if demo else "LIVE" }}</span></div>
    <div class="status-bar">
      <div class="dot" id="conn-dot"></div>
      <span id="conn-status">Łączenie...</span>
      <span>|</span>
      <span id="uptime">00:00:00</span>
      <span>|</span>
      <span id="clock">--:--:--</span>
    </div>
  </header>

  <!-- STAT CARDS -->
  <div class="grid grid-4" style="margin-bottom:16px">
    <div class="panel">
      <div class="panel-title">Saldo konta</div>
      <div class="stat-value blue" id="saldo">--</div>
      <div class="stat-label">PLN</div>
      <div class="stat-sub">Limit bota: <span id="budzet-max">{{ budzet_max }}</span> zł</div>
    </div>
    <div class="panel">
      <div class="panel-title">Użyty budżet</div>
      <div class="stat-value yellow" id="uzyte">0</div>
      <div class="stat-label">PLN</div>
      <div class="progress-bar"><div class="progress-fill" id="budzet-bar" style="width:0%"></div></div>
    </div>
    <div class="panel">
      <div class="panel-title">Aktywne pozycje</div>
      <div class="stat-value green" id="poz-count">0</div>
      <div class="stat-label">otwartych</div>
      <div class="stat-sub">Krótkie: <span id="poz-krot">0</span> | Długie: <span id="poz-dlugie">0</span></div>
    </div>
    <div class="panel">
      <div class="panel-title">Skanowane symbole</div>
      <div class="stat-value" id="sym-count">--</div>
      <div class="stat-label">instrumentów</div>
      <div class="stat-sub">Grupa: <span id="grupa">--</span></div>
    </div>
  </div>

  <!-- ŚRODKOWA SEKCJA -->
  <div class="grid grid-2" style="margin-bottom:16px">

    <!-- POZYCJE -->
    <div class="panel">
      <div class="panel-title">Otwarte pozycje</div>
      <div id="pozycje-list"><div class="brak">Brak otwartych pozycji</div></div>
    </div>

    <!-- SESJE + HISTORIA -->
    <div style="display:flex;flex-direction:column;gap:16px">

      <div class="panel">
        <div class="panel-title">Sesje giełdowe</div>
        <div id="sesje-list"></div>
      </div>

      <div class="panel">
        <div class="panel-title">Ostatnie transakcje</div>
        <div id="historia-list"><div class="brak">Brak zamkniętych transakcji</div></div>
      </div>

    </div>
  </div>

  <!-- LOGI -->
  <div class="panel">
    <div class="panel-title">Logi bota (na żywo)</div>
    <div id="log-container"></div>
  </div>

</div>

<script>
const startTime = Date.now();

// Zegar
setInterval(() => {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString('pl-PL');
  const elapsed = Math.floor((Date.now() - startTime) / 1000);
  const h = String(Math.floor(elapsed/3600)).padStart(2,'0');
  const m = String(Math.floor((elapsed%3600)/60)).padStart(2,'0');
  const s = String(elapsed%60).padStart(2,'0');
  document.getElementById('uptime').textContent = `${h}:${m}:${s}`;
}, 1000);

// Pobierz dane co 3 sekundy
async function pobierzDane() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    // Status
    document.getElementById('conn-dot').className = 'dot' + (d.polaczony ? '' : ' offline');
    document.getElementById('conn-status').textContent = d.polaczony ? 'Połączony z XTB' : 'Rozłączony';

    // Stats
    document.getElementById('saldo').textContent = d.saldo ? d.saldo.toFixed(2) : '--';
    document.getElementById('uzyte').textContent = d.uzyte_saldo || 0;
    document.getElementById('poz-count').textContent = d.pozycje_krotkie.length + d.pozycje_dlugie.length;
    document.getElementById('poz-krot').textContent = d.pozycje_krotkie.length;
    document.getElementById('poz-dlugie').textContent = d.pozycje_dlugie.length;
    document.getElementById('sym-count').textContent = d.total_symboli || '--';
    document.getElementById('grupa').textContent = d.obecna_grupa || '--';

    // Budżet bar
    const proc = Math.min((d.uzyte_saldo / d.budzet_max) * 100, 100);
    document.getElementById('budzet-bar').style.width = proc + '%';

    // Pozycje
    const pList = document.getElementById('pozycje-list');
    const wszystkie = [...d.pozycje_krotkie, ...d.pozycje_dlugie];
    if (wszystkie.length === 0) {
      pList.innerHTML = '<div class="brak">Brak otwartych pozycji</div>';
    } else {
      pList.innerHTML = wszystkie.map(p => {
        const pnlKolor = p.pnl >= 0 ? 'green' : 'red';
        const pnlZnak = p.pnl >= 0 ? '+' : '';
        const badge = p.dlugoterminowy
          ? `<span class="pozycja-badge badge-long">DŁUGI</span>`
          : p.kierunek === 'BUY'
            ? `<span class="pozycja-badge badge-buy">BUY</span>`
            : `<span class="pozycja-badge badge-sell">SELL</span>`;
        return `
          <div class="pozycja">
            <div>
              <div class="pozycja-symbol">${p.symbol} ${badge}</div>
              <div class="pozycja-info">Wejście: ${p.cena} | SL: ${p.sl} | TP: ${p.tp}</div>
              <div class="pozycja-info">Siła sygnału: ${p.sila}/100</div>
            </div>
            <div>
              <div class="pozycja-pnl ${pnlKolor}">${pnlZnak}${p.pnl?.toFixed(3)}%</div>
              <div style="font-size:10px;color:var(--muted);text-align:right">${p.czas}</div>
            </div>
          </div>`;
      }).join('');
    }

    // Sesje
    const sList = document.getElementById('sesje-list');
    sList.innerHTML = d.sesje.map(s => `
      <div class="sesja">
        <span>${s.nazwa}</span>
        <span class="sesja-status ${s.otwarta ? 'sesja-open' : 'sesja-closed'}">
          ${s.otwarta ? '● OTWARTA' : '○ ZAMKNIĘTA'}
        </span>
      </div>`).join('');

    // Historia
    const hList = document.getElementById('historia-list');
    if (!d.historia || d.historia.length === 0) {
      hList.innerHTML = '<div class="brak">Brak zamkniętych transakcji</div>';
    } else {
      hList.innerHTML = d.historia.slice(-5).reverse().map(t => `
        <div class="trade">
          <span>${t.symbol} <span style="color:${t.kierunek==='BUY'?'var(--green)':'var(--red)'}">●</span></span>
          <span style="color:var(--muted)">${t.czas}</span>
          <span class="${t.zysk >= 0 ? 'green' : 'red'}">${t.zysk >= 0 ? '+' : ''}${t.zysk?.toFixed(3)}%</span>
        </div>`).join('');
    }

    // Logi
    const lDiv = document.getElementById('log-container');
    if (d.logi && d.logi.length > 0) {
      lDiv.innerHTML = d.logi.slice(-50).reverse().map(l => {
        let cls = '';
        if (l.includes('🟢') || l.includes('KUP')) cls = 'log-buy';
        else if (l.includes('🔴') || l.includes('SPRZEDAJ')) cls = 'log-sell';
        else if (l.includes('✅')) cls = 'log-ok';
        else if (l.includes('❌')) cls = 'log-err';
        const parts = l.match(/\[(\d+:\d+:\d+)\] (.+)/);
        if (parts) {
          return `<div class="log-entry"><span class="log-time">${parts[1]}</span><span class="${cls}">${parts[2]}</span></div>`;
        }
        return `<div class="log-entry ${cls}">${l}</div>`;
      }).join('');
    }

  } catch(e) {
    document.getElementById('conn-dot').className = 'dot offline';
    document.getElementById('conn-status').textContent = 'Brak połączenia z botem';
  }
}

pobierzDane();
setInterval(pobierzDane, 3000);
</script>
</body>
</html>
"""

# ============================================================
# FLASK API
# ============================================================
app = Flask(__name__)

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML, demo=DEMO, budzet_max=BUDZET_MAX)

@app.route('/api/status')
def api_status():
    with lock:
        # Oblicz P&L dla każdej pozycji
        def poz_info(symbol, poz):
            historia = ceny_historia.get(symbol, [])
            cena_akt = historia[-1] if historia else poz["cena"]
            if poz["kierunek"] == "BUY":
                pnl = (cena_akt - poz["cena"]) / poz["cena"] * 100
            else:
                pnl = (poz["cena"] - cena_akt) / poz["cena"] * 100
            return {
                "symbol": symbol,
                "kierunek": poz["kierunek"],
                "cena": poz["cena"],
                "sl": poz["sl"],
                "tp": poz["tp"],
                "sila": poz.get("sila", 0),
                "pnl": round(pnl, 4),
                "dlugoterminowy": poz.get("dlugoterminowy", False),
                "czas": poz.get("czas", "")[:16]
            }

        poz_k = [poz_info(s, p) for s, p in pozycje_krotkie.items()]
        poz_d = [poz_info(s, p) for s, p in pozycje_dlugie.items()]

        # Status sesji
        sesje_status = []
        for nazwa, info in SESJE.items():
            otwarta = czy_sesja_otwarta(nazwa, info)
            sesje_status.append({"nazwa": nazwa, "otwarta": otwarta})

        return jsonify({
            "polaczony": sesja_id is not None,
            "saldo": saldo_konta,
            "uzyte_saldo": uzyte_saldo,
            "budzet_max": BUDZET_MAX,
            "pozycje_krotkie": poz_k,
            "pozycje_dlugie": poz_d,
            "total_symboli": sum(len(g) for g in grupy),
            "obecna_grupa": f"{obecna_grupa+1}/{len(grupy)}" if grupy else "--",
            "sesje": sesje_status,
            "logi": logi[-100:],
            "historia": historia_transakcji[-20:]
        })

# ============================================================
# POMOCNICZE
# ============================================================
def czy_sesja_otwarta(nazwa, info):
    if info.get("zawsze_otwarty"):
        return True
    dzien = datetime.now(timezone.utc).weekday()
    if dzien >= 5:
        return False
    h = datetime.now(timezone.utc).hour
    return info.get("otwarcie_utc", 0) <= h < info.get("zamkniecie_utc", 24)

def loguj(wiadomosc, typ="INFO"):
    ikony = {
        "INFO": "ℹ️", "KUP": "🟢", "SPRZEDAJ": "🔴", "BLAD": "❌",
        "OK": "✅", "PORTFEL": "💰", "SKAN": "🔍", "ZAMKNIJ": "🔒",
        "ROTACJA": "🔄", "RANKING": "🏆", "SESJA": "🕐", "PLAN": "📋"
    }
    ikona = ikony.get(typ, "ℹ️")
    tekst = f"[{datetime.now().strftime('%H:%M:%S')}] {ikona} {wiadomosc}"
    print(tekst)
    logi.append(tekst)
    if len(logi) > 500:
        logi.pop(0)

def wyslij(ws, komenda, argumenty={}):
    try:
        ws.send(json.dumps({"command": komenda, "arguments": argumenty}))
    except Exception as e:
        loguj(f"Błąd wysyłania: {e}", "BLAD")

def czy_to_akcja(symbol):
    return ".US" in symbol or ".PL" in symbol

def oblicz_sl_tp(cena, kierunek):
    if kierunek == "BUY":
        return round(cena*(1-STOP_LOSS_PROCENT/100),5), round(cena*(1+TAKE_PROFIT_PROCENT/100),5)
    else:
        return round(cena*(1+STOP_LOSS_PROCENT/100),5), round(cena*(1-TAKE_PROFIT_PROCENT/100),5)

def analizuj(symbol, cena_ask, cena_bid):
    if symbol not in ceny_historia:
        ceny_historia[symbol] = []
    ceny_historia[symbol].append(cena_ask)
    if len(ceny_historia[symbol]) < 20:
        return None, 0, ""
    ceny_historia[symbol] = ceny_historia[symbol][-50:]
    ceny = ceny_historia[symbol]

    sma5  = sum(ceny[-5:])/5
    sma10 = sum(ceny[-10:])/10
    sma20 = sum(ceny[-20:])/20
    tk = (sma5-sma10)/sma10*100
    td = (sma10-sma20)/sma20*100

    zm = [ceny[i]-ceny[i-1] for i in range(1,len(ceny))]
    w = [z for z in zm[-14:] if z>0]
    s = [abs(z) for z in zm[-14:] if z<0]
    aw = sum(w)/len(w) if w else 0
    as_ = sum(s)/len(s) if s else 0.0001
    rsi = 100-(100/(1+aw/as_))
    mom = (ceny[-1]-ceny[-5])/ceny[-5]*100
    spread = (cena_ask-cena_bid)/cena_bid*100

    sila = 0
    kierunek = None
    uzasadnienie = []

    if tk > 0 and td > 0:
        sila += 30; uzasadnienie.append(f"Oba trendy rosną")
        if rsi < 70: sila += 20; uzasadnienie.append(f"RSI={rsi:.0f} OK")
        if rsi < 40: sila += 15; uzasadnienie.append("Wyprzedany - sygnał odbicia!")
        if mom > 0:  sila += 20; uzasadnienie.append("Momentum w górę")
        if spread < 0.1: sila += 10; uzasadnienie.append("Mały spread")
        kierunek = "BUY"
    elif tk < 0 and td < 0:
        sila += 30; uzasadnienie.append("Oba trendy spadają")
        if rsi > 30: sila += 20; uzasadnienie.append(f"RSI={rsi:.0f} OK")
        if rsi > 60: sila += 15; uzasadnienie.append("Wykupiony - sygnał spadku!")
        if mom < 0:  sila += 20; uzasadnienie.append("Momentum w dół")
        if spread < 0.1: sila += 10; uzasadnienie.append("Mały spread")
        kierunek = "SELL"

    return kierunek, sila, " | ".join(uzasadnienie)

def pilnuj_pozycji():
    while True:
        try:
            with lock:
                wszystkie = {**pozycje_krotkie, **pozycje_dlugie}
            for symbol, poz in wszystkie.items():
                hist = ceny_historia.get(symbol, [])
                if not hist: continue
                cena = hist[-1]
                tp_prog = 2.0 if poz.get("dlugoterminowy") else TAKE_PROFIT_PROCENT
                if poz["kierunek"] == "BUY":
                    zysk = (cena-poz["cena"])/poz["cena"]*100
                else:
                    zysk = (poz["cena"]-cena)/poz["cena"]*100
                if zysk >= tp_prog:
                    loguj(f"✨ TAKE PROFIT {symbol} +{zysk:.3f}%", "OK")
                    zamknij(ws_global, symbol, cena, poz, zysk)
                elif zysk <= -STOP_LOSS_PROCENT:
                    loguj(f"🛑 STOP LOSS {symbol} {zysk:.3f}%", "BLAD")
                    zamknij(ws_global, symbol, cena, poz, zysk)
        except Exception as e:
            loguj(f"Błąd pilnowania: {e}", "BLAD")
        time.sleep(15)

def zamknij(ws, symbol, cena, poz, zysk):
    global uzyte_saldo
    if not ws: return
    cmd = 1 if poz["kierunek"] == "BUY" else 0
    wyslij(ws, "tradeTransaction", {"tradeTransInfo": {
        "cmd": cmd, "symbol": symbol, "volume": WOLUMEN,
        "price": cena, "sl": 0, "tp": 0, "type": 2,
        "order": poz.get("order_id", 0)
    }})
    historia_transakcji.append({
        "symbol": symbol, "kierunek": poz["kierunek"],
        "zysk": round(zysk, 4),
        "czas": datetime.now().strftime("%H:%M %d.%m")
    })
    with lock:
        for ref in [pozycje_krotkie, pozycje_dlugie]:
            if symbol in ref: del ref[symbol]
        uzyte_saldo = max(0, uzyte_saldo-100)

def otworz(ws, symbol, kierunek, cena_ask, cena_bid, sila, uzasadnienie):
    global uzyte_saldo
    dlugi = czy_to_akcja(symbol)
    ref = pozycje_dlugie if dlugi else pozycje_krotkie
    max_p = MAX_POZYCJI_DLUGICH if dlugi else MAX_POZYCJI_KROTKICH
    with lock:
        if len(ref) >= max_p: return
        if uzyte_saldo >= BUDZET_MAX: return
        if symbol in pozycje_krotkie or symbol in pozycje_dlugie: return
    cena = cena_ask if kierunek == "BUY" else cena_bid
    sl, tp = oblicz_sl_tp(cena, kierunek)
    typ_str = "📈 DŁUGI" if dlugi else "⚡ KROTKI"
    loguj(f"{typ_str} {kierunek} {symbol} @ {cena} | Siła:{sila}/100 | {uzasadnienie}", kierunek)
    loguj(f"   Plan: SL={sl} | TP={tp} | Wyjście gdy {'RSI>75 lub odwrócenie' if dlugi else 'TP/SL'}", "PLAN")
    wyslij(ws, "tradeTransaction", {"tradeTransInfo": {
        "cmd": 0 if kierunek=="BUY" else 1,
        "symbol": symbol, "volume": WOLUMEN,
        "price": cena, "sl": sl, "tp": tp, "type": 0
    }})
    with lock:
        ref[symbol] = {
            "kierunek": kierunek, "cena": cena, "sl": sl, "tp": tp,
            "sila": sila, "uzasadnienie": uzasadnienie,
            "dlugoterminowy": dlugi, "order_id": None,
            "czas": datetime.now().isoformat()
        }
        uzyte_saldo += 100

def podziel(symbole, n):
    return [symbole[i:i+n] for i in range(0, len(symbole), n)]

def subskrybuj(ws, symbole):
    for s in symbole:
        try:
            ws.send(json.dumps({
                "command": "getTickPrices",
                "streamSessionId": sesja_id,
                "symbol": s, "minArrivalTime": 2000, "maxLevel": 2
            }))
            time.sleep(0.05)
        except: pass

def odsubskrybuj(ws, symbole):
    all_poz = {**pozycje_krotkie, **pozycje_dlugie}
    for s in symbole:
        if s in all_poz: continue
        try:
            ws.send(json.dumps({
                "command": "stopTickPrices",
                "streamSessionId": sesja_id, "symbol": s
            }))
            time.sleep(0.05)
        except: pass

def rotuj(ws):
    global obecna_grupa
    while True:
        try:
            if not grupy or not sesja_id:
                time.sleep(1); continue
            stara = grupy[obecna_grupa]
            obecna_grupa = (obecna_grupa+1) % len(grupy)
            nowa = grupy[obecna_grupa]
            odsubskrybuj(ws, stara)
            subskrybuj(ws, nowa)
            loguj(f"Rotacja → Grupa {obecna_grupa+1}/{len(grupy)} | Poz: K={len(pozycje_krotkie)} D={len(pozycje_dlugie)}", "ROTACJA")
        except Exception as e:
            loguj(f"Błąd rotatora: {e}", "BLAD")
        time.sleep(ROTACJA_CO)

# ============================================================
# WEBSOCKET
# ============================================================
def on_message(ws, msg):
    global sesja_id, grupy, saldo_konta
    try:
        d = json.loads(msg)
    except: return

    if d.get("status") == True and "streamSessionId" in d:
        sesja_id = d["streamSessionId"]
        loguj("Zalogowano do XTB!", "OK")
        wyslij(ws, "getAllSymbols")
        wyslij(ws, "getMarginLevel")

    elif d.get("status") == True and isinstance(d.get("returnData"), dict):
        rd = d["returnData"]
        if "balance" in rd:
            saldo_konta = rd["balance"]
            loguj(f"Saldo: {saldo_konta} zł | Limit: {BUDZET_MAX} zł", "PORTFEL")
        elif "order" in rd:
            oid = rd["order"]
            with lock:
                for ref in [pozycje_krotkie, pozycje_dlugie]:
                    for p in ref.values():
                        if p.get("order_id") is None:
                            p["order_id"] = oid; break

    elif d.get("status") == True and isinstance(d.get("returnData"), list):
        lista = d["returnData"]
        if lista and isinstance(lista[0], dict) and "symbol" in lista[0]:
            symbole = [s["symbol"] for s in lista]
            grupy[:] = podziel(symbole, SYMBOLE_NA_RAZ)
            loguj(f"Znaleziono {len(symbole)} symboli → {len(grupy)} grup", "OK")
            subskrybuj(ws, grupy[0])
            threading.Thread(target=rotuj, args=(ws,), daemon=True).start()
            threading.Thread(target=pilnuj_pozycji, daemon=True).start()

    elif d.get("command") == "tickPrices":
        td = d.get("data", {})
        sym = td.get("symbol")
        ask = td.get("ask")
        bid = td.get("bid")
        if not (sym and ask and bid): return
        if sym not in ceny_historia: ceny_historia[sym] = []
        ceny_historia[sym].append(ask)
        ceny_historia[sym] = ceny_historia[sym][-50:]
        all_poz = {**pozycje_krotkie, **pozycje_dlugie}
        if sym not in all_poz:
            kierunek, sila, uzas = analizuj(sym, ask, bid)
            if kierunek and sila >= MIN_SILA_SYGNALU:
                otworz(ws, sym, kierunek, ask, bid, sila, uzas)

    elif d.get("status") == False:
        err = d.get("errorDescr", "")
        if err: loguj(f"Błąd XTB: {err}", "BLAD")

def on_error(ws, e): loguj(f"Błąd WS: {e}", "BLAD")
def on_close(ws, *a): loguj("Rozłączono - restartuję...", "BLAD")

def on_open(ws):
    global ws_global
    ws_global = ws
    loguj("Połączono z XTB!")
    wyslij(ws, "login", {"userId": LOGIN, "password": HASLO})

def uruchom_bota():
    while True:
        try:
            ws = websocket.WebSocketApp(
                SERVER, on_open=on_open,
                on_message=on_message,
                on_error=on_error, on_close=on_close
            )
            ws.run_forever(ping_interval=30)
        except Exception as e:
            loguj(f"Błąd krytyczny: {e}", "BLAD")
        time.sleep(15)

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 XTB BOT z Dashboardem")
    print("=" * 60)
    print(f"🌐 Dashboard: http://TWOJE_IP:{PORT_DASHBOARD}")
    print(f"💰 Limit: {BUDZET_MAX} zł | Tryb: {'DEMO' if DEMO else 'LIVE'}")
    print("=" * 60)

    # Uruchom bota w tle
    threading.Thread(target=uruchom_bota, daemon=True).start()

    # Uruchom dashboard
    app.run(host='0.0.0.0', port=PORT_DASHBOARD, debug=False)
